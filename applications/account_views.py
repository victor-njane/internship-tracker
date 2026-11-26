"""Password-confirmed deletion of the signed-in tracker account."""
import json

import requests
from cryptography.fernet import InvalidToken
from django import forms
from django.contrib.auth import get_user_model, logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError, transaction
from django.db.models import F
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_GET, require_http_methods

from .gmail_views import encryption
from .models import Application, ContactEvent, GmailConnection, ImportedEmail

NOTICE_KEY = "tracker_account_deleted"


class DeleteAccountForm(forms.Form):
    account_id = forms.CharField(widget=forms.HiddenInput)
    password = forms.CharField(
        label="Current password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
    confirm = forms.BooleanField(
        label="I understand that my account and tracker records will be permanently deleted.",
        required=True,
    )

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["account_id"].initial = str(user.pk)

    def clean_account_id(self):
        value = self.cleaned_data["account_id"]
        if value != str(self.user.pk):
            raise forms.ValidationError("Your signed-in account changed. Reload this page.")
        return value

    def clean_password(self):
        password = self.cleaned_data["password"]
        if not self.user.check_password(password):
            raise forms.ValidationError("Your current password was not correct.")
        return password


@sensitive_variables()
def revoke_saved_gmail_access(encrypted_tokens):
    """Best effort only; never block local deletion on Google's availability."""
    try:
        data = json.loads(encryption().decrypt(encrypted_tokens.encode()).decode())
        token = data.get("refresh_token") or data.get("token")
        if not isinstance(token, str) or not token:
            return False
        response = requests.post(
            "https://oauth2.googleapis.com/revoke",
            data={"token": token},
            timeout=(5, 10),
        )
        return response.status_code == 200
    except (
        InvalidToken, ValueError, TypeError, AttributeError,
        requests.RequestException, ImproperlyConfigured,
    ):
        return False


@transaction.non_atomic_requests
@login_required
@never_cache
@require_http_methods(["GET", "POST"])
@sensitive_post_parameters("password")
@sensitive_variables()
def account_settings(request):
    User = get_user_model()
    form = DeleteAccountForm(user=request.user)
    deleted = False
    had_gmail = False
    encrypted_tokens = None

    if request.method == "POST":
        form = DeleteAccountForm(request.POST, user=request.user)
        try:
            with transaction.atomic():
                # Take a write lock before checking the password and cascading deletes.
                # The preview-save helper takes the same lock (including on SQLite).
                claimed = User.objects.filter(pk=request.user.pk).update(
                    is_active=F("is_active"),
                )
                if not claimed:
                    logout(request)
                    return redirect("login")
                current_user = User.objects.get(pk=request.user.pk)
                form = DeleteAccountForm(request.POST, user=current_user)
                if form.is_valid():
                    connection = GmailConnection.objects.filter(owner=current_user).first()
                    had_gmail = connection is not None
                    encrypted_tokens = connection.encrypted_tokens if connection else None
                    current_user.delete()
                    deleted = True
        except DatabaseError:
            deleted = False
            form.add_error(None, "Deletion could not finish. Your data was not deleted. Please retry.")

        if deleted:
            # The database deletion committed before any network call to Google.
            logout(request)
            revoked = revoke_saved_gmail_access(encrypted_tokens) if had_gmail else False
            request.session[NOTICE_KEY] = {"had_gmail": had_gmail, "revoked": revoked}
            return redirect("account_deleted")

    return render(request, "applications/account_settings.html", {
        "form": form,
        "application_count": Application.objects.filter(owner=request.user).count(),
        "event_count": ContactEvent.objects.filter(application__owner=request.user).count(),
        "preview_count": ImportedEmail.objects.filter(owner=request.user).count(),
        "has_gmail": GmailConnection.objects.filter(owner=request.user).exists(),
    })


@never_cache
@require_GET
def account_deleted(request):
    notice = request.session.pop(NOTICE_KEY, None)
    if notice is None:
        return redirect("login")
    return render(request, "applications/account_settings.html", {
        "deleted": True,
        "had_gmail": notice["had_gmail"],
        "revoked": notice["revoked"],
    })
