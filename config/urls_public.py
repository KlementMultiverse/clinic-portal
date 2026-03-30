"""Public schema URL configuration. Loaded for portal.localhost (public tenant)."""

from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
]
