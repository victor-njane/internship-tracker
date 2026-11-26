"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from django.contrib.auth import views as auth_views
from applications.forms import UsernameOrEmailAuthenticationForm
from applications.account_views import account_settings, account_deleted
from applications.auth_limits import LimitedPasswordResetView

from applications.views import (
    application_add,
    application_export,
    application_complete_action,
    application_edit,
    application_history,
    contact_event_edit,
    contact_event_delete,
    dashboard,
    signup,
)

from applications.gmail_views import (
    gmail_settings, gmail_connect, gmail_callback, gmail_disconnect,
)

from applications.import_views import (
    gmail_sync, import_list, import_review, import_ignore,
)

urlpatterns = [
    path("accounts/settings/", account_settings, name="account_settings"),
    path("accounts/deleted/", account_deleted, name="account_deleted"),
    path(
        "applications/export/",
        application_export,
        name="application_export",
    ),
    path(
        "accounts/password-reset/",
        LimitedPasswordResetView.as_view(
            template_name="registration/tracker_reset/form.html",
            email_template_name="registration/tracker_reset/email.txt",
            subject_template_name="registration/tracker_reset/subject.txt",
        ),
        name="password_reset",
    ),
    path(
        "accounts/password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="registration/tracker_reset/done.html",
        ),
        name="password_reset_done",
    ),
    path(
        "accounts/reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="registration/tracker_reset/confirm.html",
        ),
        name="password_reset_confirm",
    ),
    path(
        "accounts/reset/done/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="registration/tracker_reset/complete.html",
        ),
        name="password_reset_complete",
    ),
    path("settings/email/sync/", gmail_sync, name="gmail_sync"),
    path("imports/", import_list, name="import_list"),
    path("imports/<int:pk>/", import_review, name="import_review"),
    path("imports/<int:pk>/ignore/", import_ignore, name="import_ignore"),
    path("settings/email/", gmail_settings, name="gmail_settings"),
    path("accounts/google/connect/", gmail_connect, name="gmail_connect"),
    path("accounts/google/callback/", gmail_callback, name="gmail_callback"),
    path("accounts/google/disconnect/", gmail_disconnect, name="gmail_disconnect"),
    path("applications/<int:pk>/complete-action/", application_complete_action, name="application_complete_action"),
    path("", dashboard, name="dashboard"),
    path(
        "applications/add/",
        application_add,
        name="application_add",
    ),
    path(
        "applications/<int:pk>/edit/",
        application_edit,
        name="application_edit",
    ),
    path(
        "applications/<int:pk>/history/",
        application_history,
        name="application_history",
    ),
    path(
        "applications/<int:pk>/contact-events/<int:event_pk>/edit/",
        contact_event_edit,
        name="contact_event_edit",
    ),
    path(
        "applications/<int:pk>/contact-events/<int:event_pk>/delete/",
        contact_event_delete,
        name="contact_event_delete",
    ),
    path("admin/", admin.site.urls),
    path(
    "accounts/signup/",
    signup,
    name="signup",
),
path(
        "accounts/login/",
        auth_views.LoginView.as_view(
            template_name="registration/account_form.html",
            authentication_form=UsernameOrEmailAuthenticationForm,
        ),
        name="login",
),
path(
    "accounts/logout/",
    auth_views.LogoutView.as_view(),
    name="logout",
),
]
