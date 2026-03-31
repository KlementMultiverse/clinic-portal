import json
import logging
import os
import urllib.error
import urllib.request
import uuid

import boto3
import botocore.exceptions
from django.conf import settings
from django.db import connection

logger = logging.getLogger(__name__)

BEDROCK_REGION = os.environ.get("AWS_REGION", "us-east-1")
BEDROCK_MODEL = os.environ.get(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-3-5-haiku-20241022-v1:0"
)


def get_user_context(user):
    """Get last 3 AuditLog entries for context injection into LLM prompts."""
    from apps.workflows.models import AuditLog

    recent = AuditLog.objects.filter(performed_by=user).order_by("-timestamp")[:3]
    if not recent:
        return ""
    context_lines = []
    for entry in recent:
        context_lines.append(
            f"- {entry.action} on {entry.entity_type} (ID: {entry.entity_id})"
        )
    return "User's recent activity (for context):\n" + "\n".join(context_lines) + "\n\n"


def get_s3_client():
    """Create S3 client. Per CLAUDE.md Rule #9: credentials from os.environ."""
    return boto3.client(
        "s3",
        region_name=settings.AWS_S3_REGION_NAME,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    )


def generate_upload_url(filename, content_type):
    """Generate presigned PUT URL for S3 upload.

    Per CLAUDE.md Rule #6: Key namespaced by tenant schema name.
    Per CLAUDE.md Rule #7: Presigned URLs expire after 15 minutes (900s).
    """
    tenant_schema = connection.schema_name
    doc_uuid = str(uuid.uuid4())
    s3_key = f"{tenant_schema}/{doc_uuid}/{filename}"

    try:
        s3_client = get_s3_client()
        presigned = s3_client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": settings.AWS_STORAGE_BUCKET_NAME,
                "Key": s3_key,
                "ContentType": content_type,
            },
            ExpiresIn=900,
        )
    except botocore.exceptions.ClientError as exc:
        logger.warning("S3 generate_upload_url error: %s", exc)
        raise RuntimeError(f"Storage service error: {exc}") from exc
    except (
        botocore.exceptions.ReadTimeoutError,
        botocore.exceptions.ConnectTimeoutError,
    ) as exc:
        logger.warning("S3 generate_upload_url timeout: %s", exc)
        raise RuntimeError("Storage service unavailable (timeout)") from exc
    except (
        botocore.exceptions.NoCredentialsError,
        botocore.exceptions.PartialCredentialsError,
    ) as exc:
        logger.error("AWS credentials error: %s", exc)
        raise RuntimeError("Storage service unavailable (credentials)") from exc

    logger.info("S3 generate_upload_url: key=%s", s3_key)
    return {"upload_url": presigned, "s3_key": s3_key}


def generate_download_url(s3_key):
    """Generate presigned GET URL for S3 download. Expires in 15 minutes."""
    try:
        s3_client = get_s3_client()
        url = s3_client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": settings.AWS_STORAGE_BUCKET_NAME,
                "Key": s3_key,
            },
            ExpiresIn=900,
        )
    except botocore.exceptions.ClientError as exc:
        logger.warning("S3 generate_download_url error: %s", exc)
        raise RuntimeError(f"Storage service error: {exc}") from exc
    except (
        botocore.exceptions.ReadTimeoutError,
        botocore.exceptions.ConnectTimeoutError,
    ) as exc:
        logger.warning("S3 generate_download_url timeout: %s", exc)
        raise RuntimeError("Storage service unavailable (timeout)") from exc
    except (
        botocore.exceptions.NoCredentialsError,
        botocore.exceptions.PartialCredentialsError,
    ) as exc:
        logger.error("AWS credentials error: %s", exc)
        raise RuntimeError("Storage service unavailable (credentials)") from exc

    logger.info("S3 generate_download_url: key=%s", s3_key)
    return url


def delete_s3_object(s3_key):
    """Delete object from S3."""
    try:
        s3_client = get_s3_client()
        s3_client.delete_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=s3_key,
        )
    except botocore.exceptions.ClientError as exc:
        logger.warning("S3 delete_object error: %s", exc)
        raise RuntimeError(f"Storage service error: {exc}") from exc
    except (
        botocore.exceptions.ReadTimeoutError,
        botocore.exceptions.ConnectTimeoutError,
    ) as exc:
        logger.warning("S3 delete_object timeout: %s", exc)
        raise RuntimeError("Storage service unavailable (timeout)") from exc
    except (
        botocore.exceptions.NoCredentialsError,
        botocore.exceptions.PartialCredentialsError,
    ) as exc:
        logger.error("AWS credentials error: %s", exc)
        raise RuntimeError("Storage service unavailable (credentials)") from exc

    logger.info("S3 delete_object: key=%s", s3_key)


# ---------------------------------------------------------------------------
# Bedrock direct call (used when LAMBDA_SUMMARIZE_ARN is not set)
# ---------------------------------------------------------------------------
def _invoke_llm(messages, max_tokens=500, temperature=0.2):
    """Call Claude API directly (Haiku — cheapest model)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    model = os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")
    url = "https://api.anthropic.com/v1/messages"
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body["content"][0]["text"]
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        logger.error("Claude API HTTP %d: %s", e.code, error_body)
        raise RuntimeError(f"Claude API error ({e.code}): {error_body}") from e


def _validate_summary(raw_output):
    """Validate and clean LLM summary output.

    Per CLAUDE.md Rule #18: LLM output sanitized with strip_tags().
    """
    import re

    from django.utils.html import strip_tags

    # Strip reasoning block
    text = re.sub(
        r"<reasoning>.*?</reasoning>", "", raw_output, flags=re.DOTALL
    ).strip()

    # Extract after "Summary:" if present
    if "Summary:" in text:
        text = text.split("Summary:", 1)[1].strip()

    # Strip HTML tags
    text = strip_tags(text)

    # Empty check
    if not text or not text.strip():
        return "Summary unavailable"

    # Length check (500 words max)
    words = text.split()
    if len(words) > 500:
        text = " ".join(words[:500]) + "... [summary truncated]"

    return text


def _summarize_via_llm(text, user_context=""):
    """Summarize text directly via Claude API (no Lambda).

    Uses system-reminder + Chain-of-Thought prompting with output validation.
    """
    logger.info("LLM invoke: task_type=%s", "summarize_document")
    messages = [
        {
            "role": "user",
            "content": (
                "<system-reminder>\n"
                "You are summarizing a document for a medical clinic staff member.\n"
                "- Summarize ONLY what is in the document — do NOT add information"
                " from your training data\n"
                "- Keep the summary under 200 words\n"
                "- Use plain language — avoid medical jargon unless it's in the"
                " document\n"
                "- Structure: 1-2 sentence overview, then key points as bullets\n"
                "- If the document is too short to summarize meaningfully, say so\n"
                "</system-reminder>\n\n"
                f"{user_context}"
                "Follow these steps:\n"
                "Step 1: Identify the document type (report, form, notes, letter)\n"
                "Step 2: Extract the 3-5 most important facts\n"
                "Step 3: Write a concise summary based on those facts\n\n"
                "<reasoning>\n"
                "[Your analysis here — this will be stripped before storing]\n"
                "</reasoning>\n\n"
                "Summary: [Your final summary here — this is what gets stored]\n\n"
                f"Document to summarize:\n{text[:10000]}"
            ),
        },
    ]
    raw = _invoke_llm(messages, max_tokens=500, temperature=0.2)
    result = _validate_summary(raw)
    logger.info("LLM result: %d chars", len(result))
    return result


def _validate_tasks_json(raw_output):
    """Validate and parse LLM task generation JSON output. Returns list or None.

    Per CLAUDE.md Rule #18: LLM output sanitized with strip_tags().
    """
    from django.utils.html import strip_tags

    text = raw_output.strip()

    # Extract JSON from markdown fences if present
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                text = part
                break

    try:
        result = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("LLM returned invalid JSON: %s", text[:200])
        return None

    tasks = result.get("tasks", [])
    if not isinstance(tasks, list) or len(tasks) == 0:
        logger.warning("LLM returned empty or invalid tasks array")
        return None

    # Sanitize each task
    cleaned = []
    for t in tasks:
        title = strip_tags(str(t.get("title", ""))).strip()
        desc = strip_tags(str(t.get("description", ""))).strip()
        if title and len(title) <= 100:
            cleaned.append({"title": title, "description": desc})

    return cleaned if cleaned else None


def _generate_tasks_via_llm(workflow_description, user_context=""):
    """Generate tasks directly via Claude API (no Lambda).

    Uses system-reminder + k-shot examples + validation + reflexion retry.
    """
    logger.info("LLM invoke: task_type=%s", "generate_tasks")
    prompt = (
        "<system-reminder>\n"
        "You are generating a task checklist for a clinic workflow.\n"
        "- Generate 3-8 tasks (not more, not fewer)\n"
        "- Each task must be a concrete, actionable step (not vague like"
        " 'do the thing')\n"
        "- Tasks should be in logical order (dependencies first)\n"
        "- Each task needs a title (under 100 chars) and a description"
        " (1-2 sentences)\n"
        '- Output ONLY valid JSON: {"tasks": [{"title": "...",'
        ' "description": "..."}, ...]}\n'
        "- Do NOT include tasks outside the workflow's scope\n"
        "</system-reminder>\n\n"
        "Example 1:\n"
        'Input: "Patient check-in process at front desk"\n'
        'Output: {"tasks": [{"title": "Greet patient and verify appointment", '
        '"description": "Confirm patient name, appointment time, and'
        ' provider."}, '
        '{"title": "Collect insurance card and ID", '
        '"description": "Scan or copy insurance card and photo ID for'
        ' records."}]}\n\n'
        "Example 2:\n"
        'Input: "Lab result review workflow"\n'
        'Output: {"tasks": [{"title": "Retrieve lab results from portal", '
        '"description": "Log into lab portal and download latest results'
        ' for the patient."}, '
        '{"title": "Flag abnormal values", '
        '"description": "Highlight any out-of-range results for provider'
        ' review."}]}\n\n'
        f"{user_context}"
        f'Now generate tasks for this workflow:\n"{workflow_description[:5000]}"\n\n'
        "Return ONLY valid JSON."
    )
    messages = [{"role": "user", "content": prompt}]

    raw = _invoke_llm(messages, max_tokens=1000, temperature=0.5)
    tasks = _validate_tasks_json(raw)

    if tasks is None:
        # Reflexion: retry once with error context
        logger.warning("LLM task generation failed validation, retrying with reflexion")
        retry_messages = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    "Your previous response was invalid because it was not valid"
                    " JSON or did not contain a 'tasks' array. Please try again."
                    ' Return ONLY a valid JSON object: {"tasks": [{"title": "...",'
                    ' "description": "..."}]}'
                ),
            },
        ]
        raw2 = _invoke_llm(retry_messages, max_tokens=1000, temperature=0.5)
        tasks = _validate_tasks_json(raw2)
        if tasks is None:
            raise RuntimeError(
                "AI could not generate tasks — try rephrasing the workflow"
                " description"
            )

    return tasks


# ---------------------------------------------------------------------------
# Public API: tries Lambda first, falls back to direct Bedrock
# ---------------------------------------------------------------------------
def invoke_summarize_lambda(text, user_context=None):
    """Invoke summarization — via Lambda if configured, else direct Bedrock.

    Per CLAUDE.md Rule #8: LLM calls via Lambda when deployed.
    Falls back to direct Bedrock call for local development.
    """
    lambda_arn = settings.LAMBDA_SUMMARIZE_ARN
    if not lambda_arn:
        logger.info("LAMBDA_SUMMARIZE_ARN not set — calling Bedrock directly")
        return _summarize_via_llm(text, user_context=user_context or "")

    payload_data = {"text": text, "task_type": "summarize_document"}
    if user_context:
        payload_data["user_context"] = user_context

    try:
        lambda_client = boto3.client(
            "lambda",
            region_name=settings.AWS_S3_REGION_NAME,
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        )
        response = lambda_client.invoke(
            FunctionName=lambda_arn,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload_data),
        )
    except botocore.exceptions.ClientError as exc:
        logger.error("Lambda ClientError during summarize: %s", exc)
        raise RuntimeError(f"Summarization service error: {exc}") from exc
    except (
        botocore.exceptions.ReadTimeoutError,
        botocore.exceptions.ConnectTimeoutError,
    ) as exc:
        logger.error("Lambda timeout during summarize: %s", exc)
        raise RuntimeError("Summarization service unavailable (timeout)") from exc
    except (
        botocore.exceptions.NoCredentialsError,
        botocore.exceptions.PartialCredentialsError,
    ) as exc:
        logger.error("AWS credentials error during summarize: %s", exc)
        raise RuntimeError("Summarization service unavailable (credentials)") from exc

    result = json.loads(response["Payload"].read())
    if "FunctionError" in response:
        raise RuntimeError(f"Lambda error: {result}")
    if "error" in result:
        raise RuntimeError(f"Lambda returned error: {result['error']}")
    return result.get("summary", "")


def invoke_generate_tasks_lambda(workflow_description, user_context=None):
    """Invoke task generation — via Lambda if configured, else direct Bedrock.

    Per CLAUDE.md Rule #8: LLM calls via Lambda when deployed.
    Falls back to direct Bedrock call for local development.
    """
    lambda_arn = settings.LAMBDA_SUMMARIZE_ARN
    if not lambda_arn:
        logger.info("LAMBDA_SUMMARIZE_ARN not set — calling Bedrock directly")
        return _generate_tasks_via_llm(
            workflow_description, user_context=user_context or ""
        )

    payload_data = {"text": workflow_description, "task_type": "generate_tasks"}
    if user_context:
        payload_data["user_context"] = user_context

    try:
        lambda_client = boto3.client(
            "lambda",
            region_name=settings.AWS_S3_REGION_NAME,
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        )
        response = lambda_client.invoke(
            FunctionName=lambda_arn,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload_data),
        )
    except botocore.exceptions.ClientError as exc:
        logger.error("Lambda ClientError during generate_tasks: %s", exc)
        raise RuntimeError(f"AI service error: {exc}") from exc
    except (
        botocore.exceptions.ReadTimeoutError,
        botocore.exceptions.ConnectTimeoutError,
    ) as exc:
        logger.error("Lambda timeout during generate_tasks: %s", exc)
        raise RuntimeError("AI service unavailable (timeout)") from exc
    except (
        botocore.exceptions.NoCredentialsError,
        botocore.exceptions.PartialCredentialsError,
    ) as exc:
        logger.error("AWS credentials error during generate_tasks: %s", exc)
        raise RuntimeError("AI service unavailable (credentials)") from exc

    result = json.loads(response["Payload"].read())
    if "FunctionError" in response:
        raise RuntimeError(f"Lambda error: {result}")
    if "error" in result:
        raise RuntimeError(f"Lambda returned error: {result['error']}")
    return result.get("tasks", [])
