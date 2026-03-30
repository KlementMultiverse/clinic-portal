"""Tenant-specific URL configuration. Loaded for all tenant subdomains."""

from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
]
