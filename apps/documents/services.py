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
def _invoke_llm(messages, max_tokens=500):
    """Call Claude API directly (Haiku — cheapest model)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    model = os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")
    url = "https://api.anthropic.com/v1/messages"
    payload = {
        "model": model,
        "max_tokens": max_tokens,
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


def _summarize_via_llm(text):
    """Summarize text directly via Bedrock (no Lambda)."""
    logger.info("LLM invoke: task_type=%s", "summarize_document")
    messages = [
        {
            "role": "user",
            "content": (
                "You are a helpful assistant that summarizes documents for "
                "medical clinic staff. Provide a clear, concise summary.\n\n"
                f"Document:\n{text[:10000]}"
            ),
        },
    ]
    result = _invoke_llm(messages, max_tokens=500)
    logger.info("LLM result: %d chars", len(result))
    return result


def _generate_tasks_via_llm(workflow_description):
    """Generate tasks directly via Bedrock (no Lambda)."""
    logger.info("LLM invoke: task_type=%s", "generate_tasks")
    messages = [
        {
            "role": "user",
            "content": (
                "Generate a task checklist for the following medical clinic "
                'workflow. Return ONLY a JSON object with a "tasks" key '
                "containing an array of tasks, each with "
                '"title" and "description" fields.\n\n'
                f"Workflow:\n{workflow_description[:5000]}\n\n"
                'Return JSON: {{"tasks": [{{"title": "...", "description": "..."}}]}}'
            ),
        },
    ]
    content = _invoke_llm(messages, max_tokens=1000)
    # Extract JSON from response (may have markdown fences)
    if "```" in content:
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    result = json.loads(content.strip())
    return result.get("tasks", [])


# ---------------------------------------------------------------------------
# Public API: tries Lambda first, falls back to direct Bedrock
# ---------------------------------------------------------------------------
def invoke_summarize_lambda(text):
    """Invoke summarization — via Lambda if configured, else direct Bedrock.

    Per CLAUDE.md Rule #8: LLM calls via Lambda when deployed.
    Falls back to direct Bedrock call for local development.
    """
    lambda_arn = settings.LAMBDA_SUMMARIZE_ARN
    if not lambda_arn:
        logger.info("LAMBDA_SUMMARIZE_ARN not set — calling Bedrock directly")
        return _summarize_via_llm(text)

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
            Payload=json.dumps({"text": text, "task_type": "summarize_document"}),
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


def invoke_generate_tasks_lambda(workflow_description):
    """Invoke task generation — via Lambda if configured, else direct Bedrock.

    Per CLAUDE.md Rule #8: LLM calls via Lambda when deployed.
    Falls back to direct Bedrock call for local development.
    """
    lambda_arn = settings.LAMBDA_SUMMARIZE_ARN
    if not lambda_arn:
        logger.info("LAMBDA_SUMMARIZE_ARN not set — calling Bedrock directly")
        return _generate_tasks_via_llm(workflow_description)

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
            Payload=json.dumps(
                {"text": workflow_description, "task_type": "generate_tasks"}
            ),
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
