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
