from typing import Callable

from django.http import HttpRequest, HttpResponse, JsonResponse


class PasswordResetMiddleware:
    """Middleware that enforces password reset for users with must_reset_password=True.

    If the user is authenticated and has must_reset_password set, all requests
    are blocked with a 403 response EXCEPT:
    - The password reset endpoint itself (/api/auth/reset-password)
    - The logout endpoint (/api/auth/logout)

    This ensures newly invited staff members change their temporary password
    before accessing any other functionality.
    """

    # Paths that are always allowed even when must_reset_password is True
    EXEMPT_PATHS = (
        "/api/auth/reset-password",
        "/api/auth/logout",
    )

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if (
            request.user.is_authenticated
            and hasattr(request.user, "must_reset_password")
            and request.user.must_reset_password
        ):
            # Allow exempt paths
            if not any(
                request.path.rstrip("/").startswith(p.rstrip("/"))
                for p in self.EXEMPT_PATHS
            ):
                msg = (
                    "Password reset required."
                    " Please change your password"
                    " before continuing."
                )
                return JsonResponse(
                    {"message": msg},
                    status=403,
                )

        return self.get_response(request)
