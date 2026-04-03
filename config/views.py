from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import render


def is_admin(user):
    return user.role == "admin"


def landing(request):
    """Public landing page at portal.localhost."""
    return render(request, "landing.html")


def login_view(request):
    """Login page."""
    return render(request, "login.html")


def register_view(request):
    """Registration page."""
    return render(request, "register.html")


@login_required
def dashboard(request):
    """Dashboard page (auth required)."""
    return render(request, "dashboard.html")


@login_required
def workflows(request):
    """Workflows page (auth required)."""
    return render(request, "workflows.html")


@login_required
def documents(request):
    """Documents page (auth required)."""
    return render(request, "documents.html")


@login_required
@user_passes_test(is_admin)
def staff(request):
    """Staff management page (auth required, admin only)."""
    return render(request, "staff.html")


@login_required
def search_view(request):
    """Clinical QA search page (auth required)."""
    return render(request, "search.html")


@login_required
def chat_view(request):
    """Clinical QA chat page (auth required)."""
    return render(request, "chat.html")
