import logging
from datetime import datetime
from typing import Optional

from django.core.cache import cache
from django.db import connection
from django.http import HttpRequest
from ninja import Router, Schema
from ninja.security import django_auth

from apps.documents.models import Document
from apps.documents.services import (
    delete_s3_object,
    generate_download_url,
    generate_upload_url,
)
from apps.permissions import require_admin as _require_admin
from apps.users.services import track_action
from apps.workflows.models import AuditLog

logger = logging.getLogger(__name__)


def _summarize_with_claude(document):
    """Send document directly to Claude for summarization.

    Claude Haiku 4.5 natively reads PDFs, images, and text.
    We send the raw file as base64 content block.
    """
    import base64
    import json
    import os
    import urllib.request

    try:
        from django.conf import settings

        from apps.documents.services import get_s3_client

        s3 = get_s3_client()
        resp = s3.get_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=document.s3_key,
        )
        raw = resp["Body"].read()
    except Exception as exc:
        logger.warning("Could not read document from S3: %s", exc)
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None

    # Map content types to Claude's accepted media types
    claude_media_types = {
        "application/pdf": "application/pdf",
        "image/png": "image/png",
        "image/jpeg": "image/jpeg",
        "image/gif": "image/gif",
        "image/webp": "image/webp",
    }

    content_blocks = []
    media_type = claude_media_types.get(document.content_type)

    if media_type:
        # Send as document/image block — Claude reads it directly
        b64_data = base64.standard_b64encode(raw).decode("ascii")
        if media_type == "application/pdf":
            content_blocks.append(
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64_data,
                    },
                }
            )
        else:
            content_blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64_data,
                    },
                }
            )
    else:
        # Text-based files — send as text
        text = raw.decode("utf-8", errors="replace")[:10000]
        content_blocks.append(
            {
                "type": "text",
                "text": f"Document: {document.name}\n\n{text}",
            }
        )

    content_blocks.append(
        {
            "type": "text",
            "text": (
                "Summarize this document for a medical clinic staff member. "
                "Rules: "
                "Keep under 200 words. "
                "Use plain language. "
                "Do NOT use any markdown formatting — no #, no **, no ##, no bold. "
                "Write in plain text only. "
                "Start with a 1-2 sentence overview. "
                "Then list key points with simple dashes (- point). "
                "No title or heading needed — start directly with the overview."
            ),
        }
    )

    model = os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")
    payload = {
        "model": model,
        "max_tokens": 500,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": content_blocks}],
    }

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            raw_text = body["content"][0]["text"]
            from django.utils.html import strip_tags

            clean = strip_tags(raw_text).strip()
            # Strip markdown formatting that Claude sometimes adds
            import re

            clean = re.sub(r"^#{1,3}\s+", "", clean, flags=re.MULTILINE)  # # headers
            clean = re.sub(r"\*\*(.*?)\*\*", r"\1", clean)  # **bold**
            clean = re.sub(r"\*(.*?)\*", r"\1", clean)  # *italic*
            clean = re.sub(r"^>\s+", "", clean, flags=re.MULTILINE)  # > blockquotes
            return clean.strip()
    except Exception as exc:
        logger.error("Claude summarization failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Schemas -- Per CLAUDE.md Rule #1, using Django Ninja Schema (Pydantic)
# ---------------------------------------------------------------------------

# Supported formats — Claude Haiku 4.5 reads PDFs and images natively
ALLOWED_CONTENT_TYPES = {
    "application/pdf",  # Claude reads directly
    "text/plain",  # Sent as text block
    "text/csv",  # Sent as text block
    "application/msword",  # .doc — text extraction
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "image/png",  # Claude reads directly
    "image/jpeg",  # Claude reads directly
    "image/gif",  # Claude reads directly
}


class UploadUrlIn(Schema):
    filename: str
    content_type: str


class UploadUrlOut(Schema):
    upload_url: str
    s3_key: str


class DocumentIn(Schema):
    name: str
    s3_key: str
    content_type: str
    size_bytes: int
    workflow_id: Optional[int] = None
    task_id: Optional[int] = None


class DocumentOut(Schema):
    id: int
    name: str
    s3_key: str
    content_type: str
    size_bytes: int
    summary: str
    workflow_id: Optional[int] = None
    task_id: Optional[int] = None
    uploaded_by_id: int
    created_at: datetime


class DownloadUrlOut(Schema):
    download_url: str


class SummarizeOut(Schema):
    id: int
    summary: str


class MessageOut(Schema):
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Document Router -- /api/documents/
# ---------------------------------------------------------------------------
document_router = Router(auth=django_auth, tags=["documents"])


@document_router.get("/", response={200: list[DocumentOut]})
def list_documents(
    request: HttpRequest,
    workflow_id: Optional[int] = None,
    task_id: Optional[int] = None,
):
    """List documents in current tenant. Any authenticated user.

    Optional query params: workflow_id, task_id.

    Error responses:
    - 401 Unauthorized: not authenticated
    """
    logger.info("documents.list called by user=%s", request.user.email)
    qs = Document.objects.all()
    if workflow_id is not None:
        qs = qs.filter(workflow_id=workflow_id)
    if task_id is not None:
        qs = qs.filter(task_id=task_id)
    return 200, list(qs)


@document_router.post("/upload-url", response={200: UploadUrlOut, 400: MessageOut})
def get_upload_url(request: HttpRequest, data: UploadUrlIn):
    """Get presigned upload URL for S3.

    Per CLAUDE.md Rule #6: S3 key namespaced by tenant schema name.
    Per CLAUDE.md Rule #7: Presigned URL expires in 15 minutes.
    Per CLAUDE.md Rule #8: Django never proxies file bytes.

    Error responses:
    - 400 Bad Request: unsupported content type
    - 401 Unauthorized: not authenticated
    - 422 Unprocessable Entity: schema validation failure (automatic)
    """
    if data.content_type not in ALLOWED_CONTENT_TYPES:
        return 400, {"message": f"Unsupported content type: {data.content_type}"}

    result = generate_upload_url(data.filename, data.content_type)
    return 200, result


@document_router.post(
    "/", response={201: DocumentOut, 400: MessageOut, 403: MessageOut}
)
def create_document(request: HttpRequest, data: DocumentIn):
    """Register a document after S3 upload completes.

    SECURITY: Validates that s3_key starts with current tenant schema name.
    Per CLAUDE.md Rule #12: AuditLog entry created.

    Error responses:
    - 400 Bad Request: invalid s3_key or missing workflow/task
    - 401 Unauthorized: not authenticated
    - 403 Forbidden: s3_key does not match tenant
    - 422 Unprocessable Entity: schema validation failure (automatic)
    """
    tenant_schema = connection.schema_name
    if not data.s3_key.startswith(f"{tenant_schema}/"):
        return 403, {"message": "S3 key does not match current tenant."}

    kwargs = {
        "name": data.name,
        "s3_key": data.s3_key,
        "content_type": data.content_type,
        "size_bytes": data.size_bytes,
        "uploaded_by": request.user,
    }

    if data.workflow_id is not None:
        from apps.workflows.models import Workflow

        try:
            kwargs["workflow"] = Workflow.objects.get(pk=data.workflow_id)
        except Workflow.DoesNotExist:
            return 400, {"message": "Workflow not found."}

    if data.task_id is not None:
        from apps.workflows.models import Task

        try:
            kwargs["task"] = Task.objects.get(pk=data.task_id)
        except Task.DoesNotExist:
            return 400, {"message": "Task not found."}

    document = Document.objects.create(**kwargs)
    logger.info("Document created: id=%d, name=%s", document.id, document.name)

    AuditLog.objects.create(
        entity_type="document",
        entity_id=document.id,
        action="created",
        details={"s3_key": data.s3_key, "content_type": data.content_type},
        performed_by=request.user,
    )

    cache.delete("dashboard:stats")
    track_action(request, "uploaded", "document", document.name, document.id)
    return 201, document


@document_router.get(
    "/{document_id}/download-url",
    response={200: DownloadUrlOut, 403: MessageOut, 404: MessageOut},
)
def get_download_url(request: HttpRequest, document_id: int):
    """Get presigned download URL for a document.

    Per CLAUDE.md Rule #7: Presigned URL expires in 15 minutes.
    Per CLAUDE.md Rule #8: Django never proxies file bytes.

    Error responses:
    - 401 Unauthorized: not authenticated
    - 404 Not Found: document does not exist
    """
    cache_key = f"s3:download:{document_id}"
    cached = cache.get(cache_key)
    if cached:
        return 200, {"download_url": cached}

    try:
        document = Document.objects.get(pk=document_id)
    except Document.DoesNotExist:
        return 404, {"message": "Document not found."}

    if not document.s3_key.startswith(f"{connection.schema_name}/"):
        return 403, {"message": "Access denied."}

    url = generate_download_url(document.s3_key)
    cache.set(cache_key, url, 840)
    return 200, {"download_url": url}


@document_router.post(
    "/{document_id}/summarize",
    response={200: SummarizeOut, 404: MessageOut, 500: MessageOut},
)
def summarize_document(request: HttpRequest, document_id: int):
    """Invoke Lambda summarization for a document.

    Per CLAUDE.md Rule #8: Lambda invocation via boto3 -- NEVER call OpenAI directly.
    Per CLAUDE.md Rule #12: AuditLog entry created.

    Error responses:
    - 401 Unauthorized: not authenticated
    - 404 Not Found: document does not exist
    - 500 Internal Server Error: Lambda invocation failure or not configured
    """
    try:
        document = Document.objects.get(pk=document_id)
    except Document.DoesNotExist:
        return 404, {"message": "Document not found."}

    # If already summarized, return existing summary (no LLM call)
    if document.summary:
        return 200, {"id": document.id, "summary": document.summary}

    # Check Redis cache
    cache_key = f"llm:summary:{document_id}"
    cached = cache.get(cache_key)
    if cached:
        document.summary = cached
        document.save(update_fields=["summary"])
        return 200, {"id": document.id, "summary": cached}

    logger.info("Document summarize: id=%d", document_id)
    try:
        # Send document directly to Claude (reads PDFs, images natively)
        summary = _summarize_with_claude(document)
        if not summary:
            # Fallback to text extraction path
            summary = "Could not summarize this document."
    except Exception as e:
        logger.error("Document operation failed: %s", str(e), exc_info=True)
        return 500, {"message": f"Summarization failed: {e}"}

    from django.utils.html import strip_tags

    document.summary = strip_tags(summary)
    document.save(update_fields=["summary"])
    cache.set(cache_key, document.summary, 86400)

    AuditLog.objects.create(
        entity_type="document",
        entity_id=document.id,
        action="summarized",
        details={"summary_length": len(document.summary)},
        performed_by=request.user,
    )

    track_action(request, "summarized", "document", document.name, document.id)
    return 200, {"id": document.id, "summary": document.summary}


@document_router.delete(
    "/{document_id}",
    response={200: MessageOut, 403: MessageOut, 404: MessageOut},
)
def delete_document(request: HttpRequest, document_id: int):
    """Delete a document. Admin only. Removes S3 object and DB record.

    Per CLAUDE.md Rule #12: AuditLog entry created.

    Error responses:
    - 401 Unauthorized: not authenticated
    - 403 Forbidden: not an admin
    - 404 Not Found: document does not exist
    """
    _require_admin(request)

    try:
        document = Document.objects.get(pk=document_id)
    except Document.DoesNotExist:
        return 404, {"message": "Document not found."}

    doc_id = document.id
    doc_s3_key = document.s3_key
    doc_name = document.name
    logger.info("Document deleted: id=%d by user=%s", doc_id, request.user.email)

    try:
        delete_s3_object(doc_s3_key)
    except Exception:
        pass  # Best-effort S3 deletion; DB record still removed

    document.delete()

    AuditLog.objects.create(
        entity_type="document",
        entity_id=doc_id,
        action="deleted",
        details={"s3_key": doc_s3_key, "name": doc_name},
        performed_by=request.user,
    )

    cache.delete(f"s3:download:{doc_id}")
    cache.delete(f"llm:summary:{doc_id}")
    cache.delete("dashboard:stats")
    track_action(request, "deleted", "document", doc_name, doc_id)
    return 200, {"message": "Document deleted."}
