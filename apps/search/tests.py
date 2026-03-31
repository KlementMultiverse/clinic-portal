import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django_tenants.test.cases import TenantTestCase
from django_tenants.test.client import TenantClient

from apps.search.models import SearchHistory
from apps.search.services import (
    _fallback_summary,
    _fetch_clinical_trials,
    _fetch_pubmed_papers,
    _sanitize_query,
    summarize_search_results,
)
from apps.tenants.models import Tenant
from apps.users.models import User

MOCK_TRIALS_RESPONSE = {
    "studies": [
        {
            "protocolSection": {
                "identificationModule": {
                    "nctId": "NCT12345678",
                    "briefTitle": "Test Trial",
                },
                "statusModule": {"overallStatus": "Recruiting"},
                "descriptionModule": {"briefSummary": "A test trial."},
            }
        }
    ]
}

MOCK_PUBMED_SEARCH = {"esearchresult": {"idlist": ["98765"]}}
MOCK_PUBMED_SUMMARY = {
    "result": {
        "98765": {
            "title": "Test Paper Title",
            "authors": [{"name": "Smith J"}, {"name": "Doe A"}],
            "source": "J Test Med",
            "pubdate": "2024 Jan",
        }
    }
}


def _ensure_public_tenant():
    if Tenant.objects.filter(schema_name="public").exists():
        return
    from tenant_users.tenants.utils import create_public_tenant

    create_public_tenant(
        domain_url="testserver",
        owner_email="system@search-test.local",
    )


def _create_owner_user():
    _ensure_public_tenant()
    connection.set_schema_to_public()
    try:
        return User.objects.get(email="owner@search-test.com")
    except User.DoesNotExist:
        return User.objects.create_user(
            email="owner@search-test.com",
            password="ownerpass123",
        )


def _mock_httpx_client(responses):
    """Helper: create a mock httpx.AsyncClient that returns given responses."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    if isinstance(responses, Exception):
        mock_client.get = AsyncMock(side_effect=responses)
    elif isinstance(responses, list):
        mocks = []
        for r in responses:
            m = MagicMock()
            m.json.return_value = r
            m.raise_for_status = MagicMock()
            mocks.append(m)
        mock_client.get = AsyncMock(side_effect=mocks)
    else:
        m = MagicMock()
        m.json.return_value = responses
        m.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=m)
    return mock_client


CACHE_OVERRIDE = override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    },
    SESSION_ENGINE="django.contrib.sessions.backends.db",
)


# ─────────────────────────────────────────────
# Edge cases: Query sanitization
# ─────────────────────────────────────────────
class SanitizeQueryTest(TenantTestCase):
    @classmethod
    def setup_tenant(cls, tenant):
        owner = _create_owner_user()
        tenant.owner = owner
        tenant.name = "Sanitize Test"

    def test_strips_whitespace(self):
        self.assertEqual(_sanitize_query("  hello  "), "hello")

    def test_truncates_long_query(self):
        long_q = "a" * 600
        self.assertEqual(len(_sanitize_query(long_q)), 500)

    def test_removes_special_chars(self):
        self.assertEqual(
            _sanitize_query("test<script>alert</script>"), "testscriptalert/script"
        )

    def test_removes_brackets(self):
        self.assertEqual(_sanitize_query("test[injection]"), "testinjection")

    def test_empty_query(self):
        self.assertEqual(_sanitize_query(""), "")
        self.assertEqual(_sanitize_query("   "), "")


# ─────────────────────────────────────────────
# Edge cases: Clinical trials fetch
# ─────────────────────────────────────────────
@CACHE_OVERRIDE
class FetchClinicalTrialsTest(TenantTestCase):
    @classmethod
    def setup_tenant(cls, tenant):
        owner = _create_owner_user()
        tenant.owner = owner
        tenant.name = "Trials Test"

    def setUp(self):
        super().setUp()
        cache.clear()

    @patch("apps.search.services.httpx.AsyncClient")
    def test_parses_response(self, mock_cls):
        mock_cls.return_value = _mock_httpx_client(MOCK_TRIALS_RESPONSE)
        result = asyncio.run(_fetch_clinical_trials("test"))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["nct_id"], "NCT12345678")
        self.assertEqual(result[0]["status"], "Recruiting")

    @patch("apps.search.services.httpx.AsyncClient")
    def test_empty_studies(self, mock_cls):
        mock_cls.return_value = _mock_httpx_client({"studies": []})
        result = asyncio.run(_fetch_clinical_trials("empty"))
        self.assertEqual(result, [])

    @patch("apps.search.services.httpx.AsyncClient")
    def test_timeout_returns_empty(self, mock_cls):
        mock_cls.return_value = _mock_httpx_client(httpx.TimeoutException("timeout"))
        result = asyncio.run(_fetch_clinical_trials("timeout"))
        self.assertEqual(result, [])

    @patch("apps.search.services.httpx.AsyncClient")
    def test_http_error_returns_empty(self, mock_cls):
        mock_cls.return_value = _mock_httpx_client(httpx.HTTPError("500"))
        result = asyncio.run(_fetch_clinical_trials("error"))
        self.assertEqual(result, [])

    @patch("apps.search.services.httpx.AsyncClient")
    def test_malformed_response(self, mock_cls):
        """Missing protocolSection doesn't crash."""
        mock_cls.return_value = _mock_httpx_client({"studies": [{"garbage": True}]})
        result = asyncio.run(_fetch_clinical_trials("malformed"))
        self.assertEqual(result, [])

    @patch("apps.search.services.httpx.AsyncClient")
    def test_missing_nct_id_skipped(self, mock_cls):
        """Studies without nctId are skipped."""
        data = {
            "studies": [
                {
                    "protocolSection": {
                        "identificationModule": {"briefTitle": "No ID"},
                        "statusModule": {"overallStatus": "Active"},
                    }
                }
            ]
        }
        mock_cls.return_value = _mock_httpx_client(data)
        result = asyncio.run(_fetch_clinical_trials("no-id"))
        self.assertEqual(result, [])

    @patch("apps.search.services.httpx.AsyncClient")
    def test_html_in_title_stripped(self, mock_cls):
        """HTML tags in trial title are stripped."""
        data = {
            "studies": [
                {
                    "protocolSection": {
                        "identificationModule": {
                            "nctId": "NCT999",
                            "briefTitle": "<b>Bold Trial</b>",
                        },
                        "statusModule": {"overallStatus": "Active"},
                    }
                }
            ]
        }
        mock_cls.return_value = _mock_httpx_client(data)
        result = asyncio.run(_fetch_clinical_trials("html"))
        self.assertEqual(result[0]["title"], "Bold Trial")

    @patch("apps.search.services.httpx.AsyncClient")
    def test_cache_hit(self, mock_cls):
        """Second call returns cached result, no HTTP call."""
        mock_cls.return_value = _mock_httpx_client(MOCK_TRIALS_RESPONSE)
        asyncio.run(_fetch_clinical_trials("cached_q"))
        # Reset mock — second call should use cache
        mock_cls.reset_mock()
        result = asyncio.run(_fetch_clinical_trials("cached_q"))
        self.assertEqual(len(result), 1)
        mock_cls.assert_not_called()

    def test_empty_query_returns_empty(self):
        result = asyncio.run(_fetch_clinical_trials(""))
        self.assertEqual(result, [])


# ─────────────────────────────────────────────
# Edge cases: PubMed fetch
# ─────────────────────────────────────────────
@CACHE_OVERRIDE
class FetchPubmedPapersTest(TenantTestCase):
    @classmethod
    def setup_tenant(cls, tenant):
        owner = _create_owner_user()
        tenant.owner = owner
        tenant.name = "Papers Test"

    def setUp(self):
        super().setUp()
        cache.clear()

    @patch("apps.search.services.httpx.AsyncClient")
    def test_parses_response(self, mock_cls):
        mock_cls.return_value = _mock_httpx_client(
            [MOCK_PUBMED_SEARCH, MOCK_PUBMED_SUMMARY]
        )
        result = asyncio.run(_fetch_pubmed_papers("test"))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["pmid"], "98765")
        self.assertEqual(result[0]["authors"], "Smith J, Doe A")

    @patch("apps.search.services.httpx.AsyncClient")
    def test_empty_ids_returns_empty(self, mock_cls):
        mock_cls.return_value = _mock_httpx_client({"esearchresult": {"idlist": []}})
        result = asyncio.run(_fetch_pubmed_papers("no results"))
        self.assertEqual(result, [])

    @patch("apps.search.services.httpx.AsyncClient")
    def test_timeout_returns_empty(self, mock_cls):
        mock_cls.return_value = _mock_httpx_client(httpx.TimeoutException("t"))
        result = asyncio.run(_fetch_pubmed_papers("timeout"))
        self.assertEqual(result, [])

    @patch("apps.search.services.httpx.AsyncClient")
    def test_many_authors_truncated(self, mock_cls):
        """More than 3 authors shows 'et al.'"""
        summary = {
            "result": {
                "111": {
                    "title": "Paper",
                    "authors": [
                        {"name": "A"},
                        {"name": "B"},
                        {"name": "C"},
                        {"name": "D"},
                    ],
                    "source": "J",
                    "pubdate": "2024",
                }
            }
        }
        mock_cls.return_value = _mock_httpx_client(
            [{"esearchresult": {"idlist": ["111"]}}, summary]
        )
        result = asyncio.run(_fetch_pubmed_papers("authors"))
        self.assertIn("et al.", result[0]["authors"])

    @patch("apps.search.services.httpx.AsyncClient")
    def test_non_dict_paper_skipped(self, mock_cls):
        """Non-dict entries in result are skipped."""
        summary = {"result": {"111": "not a dict"}}
        mock_cls.return_value = _mock_httpx_client(
            [{"esearchresult": {"idlist": ["111"]}}, summary]
        )
        result = asyncio.run(_fetch_pubmed_papers("bad"))
        self.assertEqual(result, [])

    @patch("apps.search.services.httpx.AsyncClient")
    def test_missing_title_skipped(self, mock_cls):
        """Papers without title are skipped."""
        summary = {"result": {"111": {"authors": [], "source": "J", "pubdate": "2024"}}}
        mock_cls.return_value = _mock_httpx_client(
            [{"esearchresult": {"idlist": ["111"]}}, summary]
        )
        result = asyncio.run(_fetch_pubmed_papers("no-title"))
        self.assertEqual(result, [])

    def test_empty_query_returns_empty(self):
        result = asyncio.run(_fetch_pubmed_papers(""))
        self.assertEqual(result, [])


# ─────────────────────────────────────────────
# Edge cases: Summarization
# ─────────────────────────────────────────────
@CACHE_OVERRIDE
class SummarizeTest(TenantTestCase):
    @classmethod
    def setup_tenant(cls, tenant):
        owner = _create_owner_user()
        tenant.owner = owner
        tenant.name = "Summarize Test"

    def test_no_results_message(self):
        result = summarize_search_results("test", [], [])
        self.assertEqual(result, "No clinical trials or papers found for this query.")

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""})
    def test_no_api_key_fallback(self):
        """Missing API key returns fallback summary, not crash."""
        trials = [{"nct_id": "NCT1", "title": "T", "status": "Active", "summary": ""}]
        result = summarize_search_results("test", trials, [])
        self.assertIn("1 Active", result)
        self.assertIn("AI summary unavailable", result)

    def test_fallback_summary_with_mixed_data(self):
        trials = [
            {"nct_id": "NCT1", "title": "T", "status": "Recruiting", "summary": ""},
            {"nct_id": "NCT2", "title": "T", "status": "Completed", "summary": ""},
            {"nct_id": "NCT3", "title": "T", "status": "Recruiting", "summary": ""},
        ]
        papers = [
            {"pmid": "1", "title": "P", "authors": "", "journal": "", "pub_date": ""}
        ]
        result = _fallback_summary(trials, papers)
        self.assertIn("3 clinical trials", result)
        self.assertIn("2 Recruiting", result)
        self.assertIn("1 Completed", result)
        self.assertIn("1 research papers", result)

    def test_fallback_no_data(self):
        self.assertEqual(_fallback_summary([], []), "No results found.")


# ─────────────────────────────────────────────
# Edge cases: API endpoint
# ─────────────────────────────────────────────
@CACHE_OVERRIDE
class SearchEndpointTest(TenantTestCase):
    @classmethod
    def setup_tenant(cls, tenant):
        owner = _create_owner_user()
        tenant.owner = owner
        tenant.name = "Endpoint Test"

    def setUp(self):
        super().setUp()
        self.client = TenantClient(self.tenant)
        connection.set_schema_to_public()
        self.user = User.objects.create_user(
            email="search-user@test.com", password="testpass123"
        )
        self.user.role = "staff"
        self.user.name = "Search User"
        self.user.save()
        self.tenant.add_user(self.user)
        # Second user for isolation test
        self.user2 = User.objects.create_user(
            email="search-user2@test.com", password="testpass123"
        )
        self.user2.role = "staff"
        self.user2.save()
        self.tenant.add_user(self.user2)
        connection.set_tenant(self.tenant)
        cache.clear()

    def _post(self, url, data):
        return self.client.post(
            url, data=json.dumps(data), content_type="application/json"
        )

    def test_unauthenticated_returns_401(self):
        resp = self._post("/api/search/", {"query": "test"})
        self.assertEqual(resp.status_code, 401)

    def test_history_unauthenticated_returns_401(self):
        resp = self.client.get("/api/search/history")
        self.assertEqual(resp.status_code, 401)

    def test_empty_query_returns_400(self):
        self.client.force_login(self.user)
        resp = self._post("/api/search/", {"query": ""})
        self.assertEqual(resp.status_code, 400)

    def test_short_query_returns_400(self):
        self.client.force_login(self.user)
        resp = self._post("/api/search/", {"query": "ab"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("at least 3", resp.json()["message"])

    def test_whitespace_only_returns_400(self):
        self.client.force_login(self.user)
        resp = self._post("/api/search/", {"query": "   "})
        self.assertEqual(resp.status_code, 400)

    @patch("apps.search.api.summarize_search_results")
    @patch("apps.search.api.run_search")
    def test_creates_history_and_audit(self, mock_run, mock_summarize):
        mock_run.return_value = (
            [{"nct_id": "NCT1", "title": "T", "status": "Active", "summary": ""}],
            [
                {
                    "pmid": "1",
                    "title": "P",
                    "authors": "A",
                    "journal": "J",
                    "pub_date": "2024",
                }
            ],
        )
        mock_summarize.return_value = "Test summary."
        self.client.force_login(self.user)
        resp = self._post("/api/search/", {"query": "diabetes"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["summary"], "Test summary.")
        self.assertEqual(len(data["trials"]), 1)
        self.assertEqual(len(data["papers"]), 1)

        # Verify DB record
        record = SearchHistory.objects.get(pk=data["search_id"])
        self.assertEqual(record.user, self.user)
        self.assertEqual(record.query, "diabetes")

        # Verify AuditLog
        from apps.workflows.models import AuditLog

        audit = AuditLog.objects.filter(
            entity_type="search", entity_id=record.id
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.action, "searched")

    @patch("apps.search.api.summarize_search_results")
    @patch("apps.search.api.run_search")
    def test_user_isolation_in_history(self, mock_run, mock_summarize):
        """User1's searches not visible to User2."""
        mock_run.return_value = ([], [])
        mock_summarize.return_value = "No results."

        # User1 searches
        self.client.force_login(self.user)
        self._post("/api/search/", {"query": "user1 query"})

        # User2 searches
        self.client.force_login(self.user2)
        self._post("/api/search/", {"query": "user2 query"})

        # User2's history should only show their search
        resp = self.client.get("/api/search/history")
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["query"], "user2 query")

        # User1's history should only show their search
        self.client.force_login(self.user)
        resp = self.client.get("/api/search/history")
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["query"], "user1 query")

    @patch("apps.search.api.summarize_search_results")
    @patch("apps.search.api.run_search")
    def test_long_query_truncated(self, mock_run, mock_summarize):
        """Very long query is truncated, not rejected."""
        mock_run.return_value = ([], [])
        mock_summarize.return_value = "OK."
        self.client.force_login(self.user)
        long_q = "metformin " * 100  # 1000 chars
        resp = self._post("/api/search/", {"query": long_q})
        self.assertEqual(resp.status_code, 200)

    @patch("apps.search.api.summarize_search_results")
    @patch("apps.search.api.run_search")
    def test_search_failure_returns_200_with_empty(self, mock_run, mock_summarize):
        """If external APIs fail, return 200 with empty results."""
        mock_run.side_effect = Exception("network error")
        mock_summarize.return_value = "unavailable"
        self.client.force_login(self.user)
        resp = self._post("/api/search/", {"query": "test query"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["trials"], [])
        self.assertEqual(data["papers"], [])

    @patch("apps.search.api.summarize_search_results")
    @patch("apps.search.api.run_search")
    def test_history_count_and_order(self, mock_run, mock_summarize):
        """History returns most recent first, limited to 20."""
        mock_run.return_value = ([], [])
        mock_summarize.return_value = "."
        self.client.force_login(self.user)
        for i in range(5):
            self._post("/api/search/", {"query": f"query {i}"})
        resp = self.client.get("/api/search/history")
        data = resp.json()
        self.assertEqual(len(data), 5)
        # Most recent first
        self.assertEqual(data[0]["query"], "query 4")
        self.assertEqual(data[4]["query"], "query 0")
