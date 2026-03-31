from django.contrib import admin
from django.urls import path

from apps.users.api import api
from config.views import dashboard, documents, login_view, register_view, staff, workflows

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api.urls),
    path("", dashboard, name="dashboard"),
    path("login/", login_view, name="login"),
    path("register/", register_view, name="register"),
    path("workflows/", workflows, name="workflows"),
    path("documents/", documents, name="documents"),
    path("staff/", staff, name="staff"),
]
