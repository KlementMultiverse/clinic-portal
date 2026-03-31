import json
from unittest.mock import AsyncMock, MagicMock, patch

from django.db import connection
from django.test import override_settings
from django_tenants.test.cases import TenantTestCase
from django_tenants.test.client import TenantClient

from apps.search.models import SearchHistory
from apps.search.services import (
    _fetch_clinical_trials,
    _fetch_pubmed_papers,
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
                "descriptionModule": {"briefSummary": "A test trial for testing."},
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
    """Create the public tenant if it does not already exist."""
    if Tenant.objects.filter(schema_name="public").exists():
        return
    from tenant_users.tenants.utils import create_public_tenant

    create_public_tenant(
        domain_url="testserver",
        owner_email="system@search-test.local",
    )


def _create_owner_user():
    """Create a user in the public schema to act as tenant owner."""
    _ensure_public_tenant()
    connection.set_schema_to_public()
    try:
        return User.objects.get(email="owner@search-test.com")
    except User.DoesNotExist:
        return User.objects.create_user(
            email="owner@search-test.com",
            password="ownerpass123",
        )


CACHE_OVERRIDE = override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    },
    SESSION_ENGINE="django.contrib.sessions.backends.db",
)


@CACHE_OVERRIDE
class FetchClinicalTrialsTest(TenantTestCase):
    """Tests for _fetch_clinical_trials service function."""

    @classmethod
    def setup_tenant(cls, tenant):
        owner = _create_owner_user()
        tenant.owner = owner
        tenant.name = "Search Trials Clinic"

    @patch("apps.search.services.httpx.AsyncClient")
    def test_fetch_trials_parses_response(self, mock_client_cls):
        """Clinical trials response is parsed correctly."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_response = MagicMock()
        mock_response.json.return_value = MOCK_TRIALS_RESPONSE
        mock_response.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        import asyncio

        result = asyncio.run(_fetch_clinical_trials("test query"))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["nct_id"], "NCT12345678")
        self.assertEqual(result[0]["title"], "Test Trial")
        self.assertEqual(result[0]["status"], "Recruiting")

    @patch("apps.search.services.httpx.AsyncClient")
    def test_fetch_trials_empty_response(self, mock_client_cls):
        """Empty studies list returns empty list."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_response = MagicMock()
        mock_response.json.return_value = {"studies": []}
        mock_response.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        import asyncio

        result = asyncio.run(_fetch_clinical_trials("empty query"))
        self.assertEqual(result, [])

    @patch("apps.search.services.httpx.AsyncClient")
    def test_fetch_trials_timeout_returns_empty(self, mock_client_cls):
        """API timeout returns empty list, not an error."""
        import httpx

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_client_cls.return_value = mock_client

        import asyncio

        result = asyncio.run(_fetch_clinical_trials("timeout query"))
        self.assertEqual(result, [])


@CACHE_OVERRIDE
class FetchPubmedPapersTest(TenantTestCase):
    """Tests for _fetch_pubmed_papers service function."""

    @classmethod
    def setup_tenant(cls, tenant):
        owner = _create_owner_user()
        tenant.owner = owner
        tenant.name = "Search Papers Clinic"

    @patch("apps.search.services.httpx.AsyncClient")
    def test_fetch_papers_parses_response(self, mock_client_cls):
        """PubMed response is parsed correctly."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        # Two sequential get calls: esearch then esummary
        search_resp = MagicMock()
        search_resp.json.return_value = MOCK_PUBMED_SEARCH
        search_resp.raise_for_status = MagicMock()

        summary_resp = MagicMock()
        summary_resp.json.return_value = MOCK_PUBMED_SUMMARY
        summary_resp.raise_for_status = MagicMock()

        mock_client.get = AsyncMock(side_effect=[search_resp, summary_resp])
        mock_client_cls.return_value = mock_client

        import asyncio

        result = asyncio.run(_fetch_pubmed_papers("test query"))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["pmid"], "98765")
        self.assertEqual(result[0]["title"], "Test Paper Title")
        self.assertEqual(result[0]["authors"], "Smith J, Doe A")
        self.assertEqual(result[0]["journal"], "J Test Med")

    @patch("apps.search.services.httpx.AsyncClient")
    def test_fetch_papers_empty_ids(self, mock_client_cls):
        """Empty PubMed ID list returns empty list."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        search_resp = MagicMock()
        search_resp.json.return_value = {"esearchresult": {"idlist": []}}
        search_resp.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=search_resp)
        mock_client_cls.return_value = mock_client

        import asyncio

        result = asyncio.run(_fetch_pubmed_papers("no results"))
        self.assertEqual(result, [])

    @patch("apps.search.services.httpx.AsyncClient")
    def test_fetch_papers_timeout_returns_empty(self, mock_client_cls):
        """API timeout returns empty list, not an error."""
        import httpx

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_client_cls.return_value = mock_client

        import asyncio

        result = asyncio.run(_fetch_pubmed_papers("timeout query"))
        self.assertEqual(result, [])


@CACHE_OVERRIDE
class SummarizeSearchResultsTest(TenantTestCase):
    """Tests for summarize_search_results service function."""

    @classmethod
    def setup_tenant(cls, tenant):
        owner = _create_owner_user()
        tenant.owner = owner
        tenant.name = "Summarize Search Clinic"

    def test_no_results_returns_message(self):
        """Empty trials and papers returns a 'no results' message."""
        result = summarize_search_results("test", [], [])
        self.assertEqual(result, "No clinical trials or papers found for this query.")

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""})
    def test_no_api_key_returns_unavailable(self):
        """Missing API key returns unavailable message."""
        trials = [
            {
                "nct_id": "NCT001",
                "title": "Test",
                "status": "Active",
                "summary": "A test.",
            }
        ]
        result = summarize_search_results("test", trials, [])
        self.assertIn("API key not configured", result)


@CACHE_OVERRIDE
class SearchEndpointTest(TenantTestCase):
    """Tests for search API endpoints (tenant app).

    Uses TenantTestCase per CLAUDE.md testing rules.
    All external HTTP calls mocked.
    """

    @classmethod
    def setup_tenant(cls, tenant):
        owner = _create_owner_user()
        tenant.owner = owner
        tenant.name = "Search Endpoint Clinic"

    def setUp(self):
        super().setUp()
        self.client = TenantClient(self.tenant)
        # Users must be created in public schema per tenant-users
        connection.set_schema_to_public()
        self.user = User.objects.create_user(
            email="search-user@test.com",
            password="testpass123",
        )
        self.user.role = "staff"
        self.user.name = "Search User"
        self.user.save()
        self.tenant.add_user(self.user)
        connection.set_tenant(self.tenant)

    def _post_json(self, url, data):
        return self.client.post(
            url,
            data=json.dumps(data),
            content_type="application/json",
        )

    @patch("apps.search.api.summarize_search_results")
    @patch("apps.search.api.run_search")
    def test_search_creates_history_record(self, mock_run, mock_summarize):
        """POST /api/search/ creates SearchHistory record."""
        mock_run.return_value = (
            [
                {
                    "nct_id": "NCT001",
                    "title": "Trial 1",
                    "status": "Recruiting",
                    "summary": "A trial.",
                }
            ],
            [
                {
                    "pmid": "12345",
                    "title": "Paper 1",
                    "authors": "Smith J",
                    "journal": "J Med",
                    "pub_date": "2024",
                }
            ],
        )
        mock_summarize.return_value = "Test AI summary."

        self.client.force_login(self.user)
        resp = self._post_json("/api/search/", {"query": "diabetes treatment"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["summary"], "Test AI summary.")
        self.assertEqual(len(data["trials"]), 1)
        self.assertEqual(len(data["papers"]), 1)
        self.assertIn("search_id", data)

        # Verify SearchHistory record created
        record = SearchHistory.objects.get(pk=data["search_id"])
        self.assertEqual(record.query, "diabetes treatment")
        self.assertEqual(record.user, self.user)

    @patch("apps.search.api.summarize_search_results")
    @patch("apps.search.api.run_search")
    def test_search_history_returns_user_searches(self, mock_run, mock_summarize):
        """GET /api/search/history returns user's searches."""
        mock_run.return_value = ([], [])
        mock_summarize.return_value = "No results."

        self.client.force_login(self.user)
        # Create a search first
        self._post_json("/api/search/", {"query": "test query"})

        resp = self.client.get("/api/search/history")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["query"], "test query")

    def test_search_requires_authentication(self):
        """POST /api/search/ without auth returns 401."""
        resp = self._post_json("/api/search/", {"query": "test"})
        self.assertEqual(resp.status_code, 401)

    def test_history_requires_authentication(self):
        """GET /api/search/history without auth returns 401."""
        resp = self.client.get("/api/search/history")
        self.assertEqual(resp.status_code, 401)

    def test_search_empty_query_returns_400(self):
        """POST /api/search/ with empty query returns 400."""
        self.client.force_login(self.user)
        resp = self._post_json("/api/search/", {"query": ""})
        self.assertEqual(resp.status_code, 400)

    def test_search_short_query_returns_400(self):
        """POST /api/search/ with query under 3 chars returns 400."""
        self.client.force_login(self.user)
        resp = self._post_json("/api/search/", {"query": "ab"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("at least 3 characters", resp.json()["message"])
