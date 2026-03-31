import json
from unittest.mock import MagicMock, patch

from django.db import connection
from django.test import override_settings
from django_tenants.test.cases import TenantTestCase
from django_tenants.test.client import TenantClient

from apps.documents.models import Document
from apps.tenants.models import Tenant
from apps.users.models import User
from apps.workflows.models import AuditLog, Workflow


def _ensure_public_tenant():
    """Create the public tenant if it does not already exist."""
    if Tenant.objects.filter(schema_name="public").exists():
        return
    from tenant_users.tenants.utils import create_public_tenant

    create_public_tenant(
        domain_url="testserver",
        owner_email="system@doc-test.local",
    )


def _create_owner_user():
    """Create a user in the public schema to act as tenant owner."""
    _ensure_public_tenant()
    connection.set_schema_to_public()
    try:
        return User.objects.get(email="owner@doc-test.com")
    except User.DoesNotExist:
        return User.objects.create_user(
            email="owner@doc-test.com",
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
class DocumentEndpointTest(TenantTestCase):
    """Tests for document CRUD endpoints (tenant app).

    Uses TenantTestCase per CLAUDE.md testing rules.
    All boto3 calls mocked -- no real AWS calls.
    """

    @classmethod
    def setup_tenant(cls, tenant):
        owner = _create_owner_user()
        tenant.owner = owner
        tenant.name = "Doc Test Clinic"

    def setUp(self):
        super().setUp()
        self.client = TenantClient(self.tenant)
        # Users must be created in public schema per tenant-users
        connection.set_schema_to_public()
        self.admin = User.objects.create_user(
            email="doc-admin@test.com",
            password="adminpass123",
        )
        self.admin.role = "admin"
        self.admin.name = "Admin User"
        self.admin.save()
        self.staff = User.objects.create_user(
            email="doc-staff@test.com",
            password="staffpass123",
        )
        self.staff.role = "staff"
        self.staff.name = "Staff User"
        self.staff.save()
        # Add users to tenant so TenantAccessMiddleware allows access
        self.tenant.add_user(self.admin)
        self.tenant.add_user(self.staff)
        connection.set_tenant(self.tenant)
        self.tenant_schema = connection.schema_name

    def _post_json(self, url, data):
        return self.client.post(
            url,
            data=json.dumps(data),
            content_type="application/json",
        )

    # -- Upload URL tests --

    @patch("apps.documents.services.get_s3_client")
    def test_upload_url_returns_presigned_url_with_tenant_key(self, mock_get_client):
        """GET upload URL returns presigned URL with tenant-namespaced s3_key."""
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://s3.example.com/presigned"
        mock_get_client.return_value = mock_s3

        self.client.force_login(self.staff)
        resp = self._post_json(
            "/api/documents/upload-url",
            {"filename": "report.pdf", "content_type": "application/pdf"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("upload_url", data)
        self.assertIn("s3_key", data)
        # Per CLAUDE.md Rule #6: s3_key starts with tenant schema name
        self.assertTrue(data["s3_key"].startswith(f"{self.tenant_schema}/"))
        self.assertTrue(data["s3_key"].endswith("/report.pdf"))

    def test_upload_url_rejects_bad_content_type(self):
        """Unsupported content type returns 400."""
        self.client.force_login(self.staff)
        resp = self._post_json(
            "/api/documents/upload-url",
            {"filename": "malware.exe", "content_type": "application/x-executable"},
        )
        self.assertEqual(resp.status_code, 400)

    # -- Create document tests --

    def test_create_document_success_with_audit_log(self):
        """Register document creates record + AuditLog entry."""
        self.client.force_login(self.staff)
        s3_key = f"{self.tenant_schema}/uuid123/report.pdf"
        resp = self._post_json(
            "/api/documents/",
            {
                "name": "Quarterly Report",
                "s3_key": s3_key,
                "content_type": "application/pdf",
                "size_bytes": 2048,
            },
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["name"], "Quarterly Report")
        self.assertEqual(data["s3_key"], s3_key)
        self.assertEqual(data["uploaded_by_id"], self.staff.id)

        # Per CLAUDE.md Rule #12: AuditLog tracks every state mutation
        audit = AuditLog.objects.filter(
            entity_type="document", action="created"
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.entity_id, data["id"])

    def test_create_document_wrong_tenant_prefix_returns_403(self):
        """Security: s3_key must start with current tenant schema name."""
        self.client.force_login(self.staff)
        resp = self._post_json(
            "/api/documents/",
            {
                "name": "Stolen Doc",
                "s3_key": "other_tenant/uuid123/stolen.pdf",
                "content_type": "application/pdf",
                "size_bytes": 1024,
            },
        )
        self.assertEqual(resp.status_code, 403)

    def test_create_document_with_workflow(self):
        """Register document linked to a workflow."""
        self.client.force_login(self.staff)
        workflow = Workflow.objects.create(name="Intake", created_by=self.staff)
        s3_key = f"{self.tenant_schema}/uuid456/intake.pdf"
        resp = self._post_json(
            "/api/documents/",
            {
                "name": "Intake Doc",
                "s3_key": s3_key,
                "content_type": "application/pdf",
                "size_bytes": 512,
                "workflow_id": workflow.id,
            },
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["workflow_id"], workflow.id)

    # -- List documents tests --

    def test_list_all_documents(self):
        """List returns all tenant documents."""
        self.client.force_login(self.staff)
        Document.objects.create(
            name="doc1.pdf",
            s3_key=f"{self.tenant_schema}/a/doc1.pdf",
            content_type="application/pdf",
            size_bytes=100,
            uploaded_by=self.staff,
        )
        Document.objects.create(
            name="doc2.pdf",
            s3_key=f"{self.tenant_schema}/b/doc2.pdf",
            content_type="application/pdf",
            size_bytes=200,
            uploaded_by=self.staff,
        )
        resp = self.client.get("/api/documents/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 2)

    def test_filter_documents_by_workflow_id(self):
        """Filter documents by workflow_id returns only matching docs."""
        self.client.force_login(self.staff)
        workflow = Workflow.objects.create(name="Test WF", created_by=self.staff)
        Document.objects.create(
            name="doc1.pdf",
            s3_key=f"{self.tenant_schema}/a/doc1.pdf",
            content_type="application/pdf",
            size_bytes=100,
            uploaded_by=self.staff,
            workflow=workflow,
        )
        Document.objects.create(
            name="doc2.pdf",
            s3_key=f"{self.tenant_schema}/b/doc2.pdf",
            content_type="application/pdf",
            size_bytes=200,
            uploaded_by=self.staff,
        )
        resp = self.client.get(f"/api/documents/?workflow_id={workflow.id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["name"], "doc1.pdf")

    # -- Download URL tests --

    @patch("apps.documents.api.generate_download_url")
    def test_get_download_url(self, mock_gen_url):
        """Download URL endpoint returns presigned URL."""
        self.client.force_login(self.staff)
        doc = Document.objects.create(
            name="download.pdf",
            s3_key=f"{self.tenant_schema}/x/download.pdf",
            content_type="application/pdf",
            size_bytes=300,
            uploaded_by=self.staff,
        )
        mock_gen_url.return_value = "https://s3.example.com/download-presigned"
        resp = self.client.get(f"/api/documents/{doc.id}/download-url")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json()["download_url"], "https://s3.example.com/download-presigned"
        )
        mock_gen_url.assert_called_once_with(doc.s3_key)

    def test_download_url_not_found(self):
        """Download URL for non-existent document returns 404."""
        self.client.force_login(self.staff)
        resp = self.client.get("/api/documents/99999/download-url")
        self.assertEqual(resp.status_code, 404)

    # -- Delete tests --

    @patch("apps.documents.api.delete_s3_object")
    def test_admin_can_delete_document(self, mock_s3_delete):
        """Admin delete removes DB record + calls S3 delete + AuditLog."""
        self.client.force_login(self.admin)
        doc = Document.objects.create(
            name="to-delete.pdf",
            s3_key=f"{self.tenant_schema}/d/to-delete.pdf",
            content_type="application/pdf",
            size_bytes=400,
            uploaded_by=self.admin,
        )
        resp = self.client.delete(f"/api/documents/{doc.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Document.objects.filter(pk=doc.id).exists())
        mock_s3_delete.assert_called_once_with(doc.s3_key)

        # Per CLAUDE.md Rule #12: AuditLog entry
        audit = AuditLog.objects.filter(
            entity_type="document", action="deleted"
        ).first()
        self.assertIsNotNone(audit)

    def test_staff_cannot_delete_document(self):
        """Non-admin delete returns 403."""
        self.client.force_login(self.staff)
        doc = Document.objects.create(
            name="no-delete.pdf",
            s3_key=f"{self.tenant_schema}/d/no-delete.pdf",
            content_type="application/pdf",
            size_bytes=400,
            uploaded_by=self.staff,
        )
        resp = self.client.delete(f"/api/documents/{doc.id}")
        self.assertEqual(resp.status_code, 403)

    # -- Summarize tests --

    @patch("apps.documents.api.invoke_summarize_lambda")
    def test_summarize_success(self, mock_lambda):
        """Summarize endpoint invokes Lambda, saves summary, creates AuditLog."""
        self.client.force_login(self.staff)
        doc = Document.objects.create(
            name="summary.pdf",
            s3_key=f"{self.tenant_schema}/s/summary.pdf",
            content_type="application/pdf",
            size_bytes=500,
            uploaded_by=self.staff,
        )
        mock_lambda.return_value = "This is a test summary."
        resp = self.client.post(f"/api/documents/{doc.id}/summarize")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["summary"], "This is a test summary.")
        self.assertEqual(data["id"], doc.id)

        # Verify saved to DB
        doc.refresh_from_db()
        self.assertEqual(doc.summary, "This is a test summary.")

        # AuditLog entry
        audit = AuditLog.objects.filter(
            entity_type="document", action="summarized"
        ).first()
        self.assertIsNotNone(audit)

    @patch("apps.documents.api.invoke_summarize_lambda")
    def test_summarize_handles_lambda_error_gracefully(self, mock_lambda):
        """Lambda failure returns 500 with error message, not a crash."""
        self.client.force_login(self.staff)
        doc = Document.objects.create(
            name="fail.pdf",
            s3_key=f"{self.tenant_schema}/f/fail.pdf",
            content_type="application/pdf",
            size_bytes=100,
            uploaded_by=self.staff,
        )
        mock_lambda.side_effect = RuntimeError("Lambda not configured")
        resp = self.client.post(f"/api/documents/{doc.id}/summarize")
        self.assertEqual(resp.status_code, 500)
        self.assertIn("Summarization failed", resp.json()["message"])

    def test_summarize_not_found(self):
        """Summarize non-existent document returns 404."""
        self.client.force_login(self.staff)
        resp = self.client.post("/api/documents/99999/summarize")
        self.assertEqual(resp.status_code, 404)


class ValidateSummaryTest(TenantTestCase):
    """Tests for _validate_summary output validation.

    Per CLAUDE.md Rule #18: LLM output sanitized with strip_tags().
    """

    @classmethod
    def setup_tenant(cls, tenant):
        owner = _create_owner_user()
        tenant.owner = owner
        tenant.name = "Validate Summary Clinic"

    def test_strips_reasoning_block(self):
        """Reasoning tags and their content are removed."""
        from apps.documents.services import _validate_summary

        raw = (
            "<reasoning>This is internal analysis</reasoning>\n"
            "Summary: The document covers quarterly results."
        )
        result = _validate_summary(raw)
        self.assertNotIn("reasoning", result)
        self.assertNotIn("internal analysis", result)
        self.assertIn("quarterly results", result)

    def test_extracts_after_summary_prefix(self):
        """Text after 'Summary:' is extracted."""
        from apps.documents.services import _validate_summary

        raw = "Some preamble text\nSummary: The actual summary content."
        result = _validate_summary(raw)
        self.assertEqual(result, "The actual summary content.")

    def test_strips_html_tags(self):
        """HTML tags are sanitized from output."""
        from apps.documents.services import _validate_summary

        raw = "Summary: <b>Bold</b> text and <script>alert('xss')</script> danger."
        result = _validate_summary(raw)
        self.assertNotIn("<b>", result)
        self.assertNotIn("<script>", result)
        self.assertIn("Bold", result)

    def test_empty_output_returns_fallback(self):
        """Empty or whitespace-only output returns 'Summary unavailable'."""
        from apps.documents.services import _validate_summary

        self.assertEqual(_validate_summary(""), "Summary unavailable")
        self.assertEqual(_validate_summary("   "), "Summary unavailable")

    def test_truncates_long_output(self):
        """Output exceeding 500 words is truncated."""
        from apps.documents.services import _validate_summary

        long_text = " ".join(["word"] * 600)
        result = _validate_summary(f"Summary: {long_text}")
        self.assertTrue(result.endswith("... [summary truncated]"))
        # 500 words + the truncation suffix
        self.assertLessEqual(len(result.split()), 503)

    def test_plain_text_passes_through(self):
        """Plain text without reasoning or Summary: prefix passes through."""
        from apps.documents.services import _validate_summary

        raw = "This is a simple summary of the document."
        result = _validate_summary(raw)
        self.assertEqual(result, "This is a simple summary of the document.")


class ValidateTasksJsonTest(TenantTestCase):
    """Tests for _validate_tasks_json output validation.

    Per CLAUDE.md Rule #18: LLM output sanitized with strip_tags().
    """

    @classmethod
    def setup_tenant(cls, tenant):
        owner = _create_owner_user()
        tenant.owner = owner
        tenant.name = "Validate Tasks Clinic"

    def test_valid_json_returns_tasks(self):
        """Valid JSON with tasks array returns cleaned list."""
        from apps.documents.services import _validate_tasks_json

        raw = json.dumps(
            {
                "tasks": [
                    {"title": "Step 1", "description": "Do the first thing."},
                    {"title": "Step 2", "description": "Do the second thing."},
                ]
            }
        )
        result = _validate_tasks_json(raw)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["title"], "Step 1")

    def test_invalid_json_returns_none(self):
        """Invalid JSON returns None."""
        from apps.documents.services import _validate_tasks_json

        result = _validate_tasks_json("this is not json at all")
        self.assertIsNone(result)

    def test_extracts_json_from_markdown_fences(self):
        """JSON inside markdown code fences is extracted."""
        from apps.documents.services import _validate_tasks_json

        raw = (
            "Here is the output:\n"
            "```json\n"
            '{"tasks": [{"title": "Task A", "description": "Desc A"}]}\n'
            "```"
        )
        result = _validate_tasks_json(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["title"], "Task A")

    def test_empty_tasks_returns_none(self):
        """Empty tasks array returns None."""
        from apps.documents.services import _validate_tasks_json

        result = _validate_tasks_json('{"tasks": []}')
        self.assertIsNone(result)

    def test_strips_html_from_tasks(self):
        """HTML tags are stripped from task titles and descriptions."""
        from apps.documents.services import _validate_tasks_json

        raw = json.dumps(
            {
                "tasks": [
                    {
                        "title": "<b>Bold Task</b>",
                        "description": "<script>xss</script>Safe desc.",
                    },
                ]
            }
        )
        result = _validate_tasks_json(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["title"], "Bold Task")
        self.assertNotIn("<script>", result[0]["description"])

    def test_title_over_100_chars_excluded(self):
        """Tasks with titles over 100 chars are excluded."""
        from apps.documents.services import _validate_tasks_json

        raw = json.dumps(
            {
                "tasks": [
                    {"title": "A" * 101, "description": "Too long title."},
                    {"title": "Valid", "description": "Ok."},
                ]
            }
        )
        result = _validate_tasks_json(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Valid")

    def test_missing_tasks_key_returns_none(self):
        """JSON without 'tasks' key returns None."""
        from apps.documents.services import _validate_tasks_json

        result = _validate_tasks_json('{"items": []}')
        self.assertIsNone(result)


@CACHE_OVERRIDE
class GenerateTasksReflexionTest(TenantTestCase):
    """Tests for _generate_tasks_via_llm reflexion retry logic."""

    @classmethod
    def setup_tenant(cls, tenant):
        owner = _create_owner_user()
        tenant.owner = owner
        tenant.name = "Reflexion Test Clinic"

    @patch("apps.documents.services._invoke_llm")
    def test_reflexion_retry_on_invalid_first_response(self, mock_llm):
        """When first LLM call returns invalid JSON, reflexion retry succeeds."""
        from apps.documents.services import _generate_tasks_via_llm

        valid_json = json.dumps(
            {
                "tasks": [
                    {"title": "Task 1", "description": "First task."},
                    {"title": "Task 2", "description": "Second task."},
                ]
            }
        )
        # First call returns garbage, second call returns valid JSON
        mock_llm.side_effect = ["not valid json", valid_json]

        result = _generate_tasks_via_llm("Test workflow description")
        self.assertEqual(len(result), 2)
        self.assertEqual(mock_llm.call_count, 2)

    @patch("apps.documents.services._invoke_llm")
    def test_reflexion_raises_after_two_failures(self, mock_llm):
        """When both LLM calls return invalid JSON, RuntimeError is raised."""
        from apps.documents.services import _generate_tasks_via_llm

        mock_llm.return_value = "still not json"

        with self.assertRaises(RuntimeError) as ctx:
            _generate_tasks_via_llm("Bad workflow")
        self.assertIn("could not generate tasks", str(ctx.exception))
        self.assertEqual(mock_llm.call_count, 2)

    @patch("apps.documents.services._invoke_llm")
    def test_no_retry_when_first_response_valid(self, mock_llm):
        """When first LLM call returns valid JSON, no retry occurs."""
        from apps.documents.services import _generate_tasks_via_llm

        valid_json = json.dumps(
            {"tasks": [{"title": "Only Task", "description": "Done."}]}
        )
        mock_llm.return_value = valid_json

        result = _generate_tasks_via_llm("Simple workflow")
        self.assertEqual(len(result), 1)
        self.assertEqual(mock_llm.call_count, 1)


@CACHE_OVERRIDE
class SummarizeViaLlmTest(TenantTestCase):
    """Tests for _summarize_via_llm with structured prompting."""

    @classmethod
    def setup_tenant(cls, tenant):
        owner = _create_owner_user()
        tenant.owner = owner
        tenant.name = "Summarize LLM Clinic"

    @patch("apps.documents.services._invoke_llm")
    def test_summary_strips_reasoning_from_llm_output(self, mock_llm):
        """LLM output with reasoning block is cleaned before return."""
        from apps.documents.services import _summarize_via_llm

        mock_llm.return_value = (
            "<reasoning>Document is a quarterly report.</reasoning>\n"
            "Summary: The clinic saw 200 patients in Q3."
        )
        result = _summarize_via_llm("Some document text")
        self.assertNotIn("reasoning", result)
        self.assertIn("200 patients", result)

    @patch("apps.documents.services._invoke_llm")
    def test_summary_passes_temperature(self, mock_llm):
        """_summarize_via_llm passes temperature=0.2 to _invoke_llm."""
        from apps.documents.services import _summarize_via_llm

        mock_llm.return_value = "Summary: Test result."
        _summarize_via_llm("Some text")
        call_kwargs = mock_llm.call_args
        self.assertEqual(
            call_kwargs[1].get(
                "temperature", call_kwargs[0][2] if len(call_kwargs[0]) > 2 else None
            ),
            0.2,
        )

    @patch("apps.documents.services._invoke_llm")
    def test_summary_empty_llm_response_returns_fallback(self, mock_llm):
        """Empty LLM response returns 'Summary unavailable'."""
        from apps.documents.services import _summarize_via_llm

        mock_llm.return_value = ""
        result = _summarize_via_llm("Some text")
        self.assertEqual(result, "Summary unavailable")
