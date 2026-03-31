import json
import os
import uuid

import boto3
from django.conf import settings
from django.db import connection


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
    return {"upload_url": presigned, "s3_key": s3_key}


def generate_download_url(s3_key):
    """Generate presigned GET URL for S3 download. Expires in 15 minutes."""
    s3_client = get_s3_client()
    url = s3_client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.AWS_STORAGE_BUCKET_NAME,
            "Key": s3_key,
        },
        ExpiresIn=900,
    )
    return url


def delete_s3_object(s3_key):
    """Delete object from S3."""
    s3_client = get_s3_client()
    s3_client.delete_object(
        Bucket=settings.AWS_STORAGE_BUCKET_NAME,
        Key=s3_key,
    )


def invoke_summarize_lambda(text):
    """Invoke Lambda for document summarization.

    Per CLAUDE.md Rule #8: Lambda invocation via boto3 -- NEVER call OpenAI directly.
    Per CLAUDE.md Rule #9: credentials from os.environ.
    """
    lambda_client = boto3.client(
        "lambda",
        region_name=settings.AWS_S3_REGION_NAME,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    )
    response = lambda_client.invoke(
        FunctionName=settings.LAMBDA_SUMMARIZE_ARN,
        InvocationType="RequestResponse",
        Payload=json.dumps({"text": text, "task_type": "summarize_document"}),
    )
    result = json.loads(response["Payload"].read())
    if "FunctionError" in response:
        raise RuntimeError(f"Lambda error: {result}")
    return result.get("summary", "")
