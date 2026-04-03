"""AWS Lambda handler for document summarization and task generation.

Uses AWS Bedrock (Claude) for LLM calls. Supports both:
- IAM auth (when deployed as Lambda with IAM role)
- Bearer token auth (via AWS_BEARER_TOKEN_BEDROCK env var)

Per CLAUDE.md Rule #8: Django NEVER calls LLM directly -- always through Lambda.
"""

import json
import os
import urllib.error
import urllib.request

BEDROCK_REGION = os.environ.get("AWS_REGION", "us-east-1")
BEDROCK_MODEL = os.environ.get(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-3-5-haiku-20241022-v1:0"
)


def _invoke_bedrock(messages, max_tokens=500):
    """Call Bedrock via REST with bearer token auth."""
    token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")
    if not token:
        raise RuntimeError("AWS_BEARER_TOKEN_BEDROCK not configured")

    url = (
        f"https://bedrock-runtime.{BEDROCK_REGION}.amazonaws.com"
        f"/model/{BEDROCK_MODEL}/invoke"
    )
    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "messages": messages,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body["content"][0]["text"]
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Bedrock HTTP {e.code}: {error_body}") from e


def handler(event, context):
    """Lambda handler for document summarization and task generation.

    Input event:
        {"text": "...", "task_type": "summarize_document" | "generate_tasks"}

    Output:
        {"summary": "..."} for summarize_document
        {"tasks": [{"title": "...", "description": "..."}, ...]} for generate_tasks
        {"error": "..."} on failure
    """
    try:
        text = event.get("text", "")
        task_type = event.get("task_type", "summarize_document")

        if not text:
            return {"error": "No text provided"}

        if task_type == "summarize_document":
            messages = [
                {
                    "role": "user",
                    "content": (
                        "You are a helpful assistant that summarizes "
                        "documents for medical clinic staff. "
                        "Provide a clear, concise summary.\n\n"
                        f"Document:\n{text[:10000]}"
                    ),
                },
            ]
            summary = _invoke_bedrock(messages, max_tokens=500)
            return {"summary": summary}

        elif task_type == "generate_tasks":
            messages = [
                {
                    "role": "user",
                    "content": (
                        "Generate a task checklist for the following "
                        "medical clinic workflow. Return ONLY a JSON object "
                        'with a "tasks" key containing an array of tasks, '
                        'each with "title" and "description" fields.\n\n'
                        f"Workflow:\n{text[:5000]}\n\n"
                        "Return JSON: "
                        '{"tasks": [{"title": "...", "description": "..."}]}'
                    ),
                },
            ]
            content = _invoke_bedrock(messages, max_tokens=1000)
            # Extract JSON from response (may have markdown fences)
            if "```" in content:
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            result = json.loads(content.strip())
            tasks = result.get("tasks", [])
            return {"tasks": tasks}

        else:
            return {"error": f"Unknown task_type: {task_type}"}

    except Exception as e:
        return {"error": str(e)}
