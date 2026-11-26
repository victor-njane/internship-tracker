"""Connect Gmail to the current tracker user."""
import json
import secrets
import time
from urllib.parse import urlsplit

import requests
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_GET, require_POST
from google_auth_oauthlib.flow import Flow
from oauthlib.oauth2 import OAuth2Error

from .models import GmailConnection

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
SESSION_KEY = "gmail_oauth_pending"


def encryption():
    try:
        return Fernet(settings.GMAIL_TOKEN_ENCRYPTION_KEY.encode("ascii"))
    except (AttributeError, ValueError, UnicodeError) as exc:
        raise ImproperlyConfigured("Check GMAIL_TOKEN_ENCRYPTION_KEY in .env.") from exc


def make_flow(**kwargs):
    client_id = getattr(settings, "GOOGLE_CLIENT_ID", "")
    secret = getattr(settings, "GOOGLE_CLIENT_SECRET", "")
    callback = getattr(settings, "GOOGLE_REDIRECT_URI", "")
    if not client_id or not secret or not callback:
        raise ImproperlyConfigured("Add Google credentials to .env and restart Django.")
    parts = urlsplit(callback)
    local_http = settings.DEBUG and parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost")
    if parts.scheme != "https" and not local_http:
        raise ImproperlyConfigured("Use HTTPS, or a local HTTP callback with DEBUG=True.")
    encryption()  # Fail before asking Google for permission if the key is invalid.
    return Flow.from_client_config(
        {"web": {
            "client_id": client_id,
            "client_secret": secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [callback],
        }},
        scopes=SCOPES,
        redirect_uri=callback,
        **kwargs,
    )


@login_required
@never_cache
@require_GET
def gmail_settings(request):
    connection = GmailConnection.objects.filter(owner=request.user).first()
    return render(request, "applications/gmail_settings.html", {"connection": connection})


@login_required
@require_POST
@sensitive_variables()
def gmail_connect(request):
    # An existing connection must be disconnected before choosing another mailbox.
    if GmailConnection.objects.filter(owner=request.user).exists():
        messages.info(request, "Disconnect the current Gmail account before connecting another.")
        return redirect("gmail_settings")
    try:
        flow = make_flow(autogenerate_code_verifier=True)
        auth_url, state = flow.authorization_url(
            access_type="offline", prompt="consent select_account",
        )
    except ImproperlyConfigured:
        messages.error(request, "Gmail setup is incomplete. Check the .env values and settings.py, then restart the server.")
        return redirect("gmail_settings")
    request.session[SESSION_KEY] = {
        "state": state,
        "verifier": flow.code_verifier,
        "owner": request.user.pk,
        "created": time.time(),
    }
    return redirect(auth_url)


@login_required
@never_cache
@require_GET
@sensitive_variables()
def gmail_callback(request):
    pending = request.session.get(SESSION_KEY)
    received_state = request.GET.get("state", "")
    if (
        not pending or not received_state
        or pending.get("owner") != request.user.pk
        or not secrets.compare_digest(pending.get("state", ""), received_state)
        or not 0 <= time.time() - pending.get("created", 0) <= 600
    ):
        messages.error(request, "The Gmail connection request expired or did not match. Start again from Connect Gmail.")
        return redirect("gmail_settings")
    request.session.pop(SESSION_KEY, None)  # Consume once, including denied requests.
    if request.GET.get("error") or not request.GET.get("code"):
        messages.info(request, "Gmail was not connected. You can try again whenever you are ready.")
        return redirect("gmail_settings")
    if GmailConnection.objects.filter(owner=request.user).exists():
        messages.info(request, "Gmail is already connected.")
        return redirect("gmail_settings")
    try:
        flow = make_flow(state=pending["state"], code_verifier=pending["verifier"])
        # The code is exchanged over HTTPS. No global insecure-transport override is needed.
        flow.fetch_token(code=request.GET["code"], timeout=20)
        credentials = flow.credentials
        granted = credentials.granted_scopes
        if granted is not None and not set(SCOPES).issubset(set(granted)):
            messages.error(request, "Read-only Gmail access was not granted. Please reconnect and approve that permission.")
            return redirect("gmail_settings")
        if not credentials.refresh_token:
            messages.error(request, "Google did not provide offline access. Remove this app from your Google account connections, then try again.")
            return redirect("gmail_settings")
        profile_response = requests.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            headers={"Authorization": f"Bearer {credentials.token}"},
            timeout=20,
        )
        profile_response.raise_for_status()
        profile = profile_response.json()
        email = profile["emailAddress"]
        # Store tokens encrypted; client secret remains only in server configuration.
        token_data = {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "scopes": list(credentials.scopes or SCOPES),
            "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
        }
        encrypted = encryption().encrypt(json.dumps(token_data).encode()).decode()
        connection, created = GmailConnection.objects.get_or_create(
            owner=request.user,
            defaults={
                "email": email,
                "encrypted_tokens": encrypted,
            },
        )

        if not created:
            messages.info(
                request,
                "Gmail was already connected in another request. "
                "Your existing connection was kept.",
            )
            return redirect("gmail_settings")
    except (OAuth2Error, requests.RequestException, ValueError, KeyError, ImproperlyConfigured, Warning):
        # Do not expose authorization codes, tokens, or provider response bodies.
        messages.error(request, "Gmail could not be connected. Check the Gmail API, test-user entry, credentials, and callback URL, then try again.")
        return redirect("gmail_settings")
    messages.success(request, f"Connected {email}. Use Sync now to find messages for review.")
    return redirect("gmail_settings")


@login_required
@require_POST
@sensitive_variables()
def gmail_disconnect(request):
    request.session.pop(SESSION_KEY, None)
    connection = GmailConnection.objects.filter(owner=request.user).first()
    if connection is None:
        messages.info(request, "No Gmail account is connected.")
        return redirect("gmail_settings")
    revoked = False
    try:
        data = json.loads(encryption().decrypt(connection.encrypted_tokens.encode()).decode())
        token = data.get("refresh_token") or data.get("token")
        if token:
            response = requests.post("https://oauth2.googleapis.com/revoke", data={"token": token}, timeout=15)
            revoked = response.status_code == 200
    except (InvalidToken, ValueError, requests.RequestException, ImproperlyConfigured):
        pass
    connection.delete()
    if revoked:
        messages.success(request, "Gmail disconnected and Google access revoked. Your applications and history are unchanged.")
    else:
        messages.warning(request, "The local connection was removed, but Google revocation could not be confirmed. Remove Internship Tracker from your Google account connections using the link below.")
    return redirect("gmail_settings")
