"""Shared authorization helpers for Django Ninja API endpoints.

This module centralizes permission checks that were previously duplicated
across workflows, documents, and users API modules.
"""

from django.http import HttpRequest
from ninja.errors import HttpError


def require_admin(
    request: HttpRequest,
    message: str = "Only admins can perform this action.",
) -> None:
    """Raise 403 if the authenticated user is not an admin.

    Args:
        request: The current HTTP request (must have an authenticated user).
        message: Custom error message for the 403 response.
    """
    if request.user.role != "admin":
        raise HttpError(403, message)
