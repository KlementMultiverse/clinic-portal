"""AWS Lambda handler for document summarization and task generation.

This function is deployed separately to AWS Lambda. It receives requests from
Django via boto3.client("lambda").invoke() and calls OpenAI internally.

Per CLAUDE.md Rule #8: Django NEVER calls OpenAI directly -- always through Lambda.
"""

import json
import os


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

        # Import here to avoid cold start overhead if not needed
        from openai import OpenAI

        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

        if task_type == "summarize_document":
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a helpful assistant that summarizes "
                            "documents for medical clinic staff. "
                            "Provide clear, concise summaries."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Summarize the following document:\n\n{text[:10000]}"
                        ),
                    },
                ],
                max_tokens=500,
            )
            summary = response.choices[0].message.content
            return {"summary": summary}

        elif task_type == "generate_tasks":
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a helpful assistant that generates "
                            "task checklists for medical clinic workflows."
                            " Return a JSON object with a 'tasks' key "
                            "containing an array of tasks, each with "
                            "'title' and 'description' fields."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Generate a task checklist for the following workflow:\n\n"
                            f"{text[:5000]}\n\n"
                            'Return JSON format: {"tasks": [{"title": "...", '
                            '"description": "..."}]}'
                        ),
                    },
                ],
                max_tokens=1000,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            result = json.loads(content)
            tasks = result.get("tasks", [])
            return {"tasks": tasks}

        else:
            return {"error": f"Unknown task_type: {task_type}"}

    except Exception as e:
        return {"error": str(e)}
