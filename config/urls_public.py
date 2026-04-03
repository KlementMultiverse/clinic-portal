from django.contrib import admin
from django.urls import path

from apps.users.api import api
from config.views import landing, login_view, register_view

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api.urls),
    path("", landing, name="landing"),
    path("login/", login_view, name="login"),
    path("register/", register_view, name="register"),
]
