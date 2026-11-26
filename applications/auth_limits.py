"""Independent, database-backed signup and reset request limits."""
import json
import math
from datetime import datetime, timedelta, timezone as dt_timezone
from ipaddress import ip_address

from django.contrib.auth.views import PasswordResetView
from django.db import DatabaseError
from django.db.models import F
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import AuthRateLimit

def auth_rate_limit(scope, subject, limit, window_seconds):
    now = timezone.now()
    window = int(now.timestamp()) // window_seconds
    end = datetime.fromtimestamp(
        (window + 1) * window_seconds, tz=dt_timezone.utc,
    )
    key = salted_hmac(
        "tracker.auth-rate-limit.v1",
        json.dumps([scope, subject, window], separators=(",", ":")),
        algorithm="sha256",
    ).hexdigest()

    # Bounded cleanup of expired counters; never delete an active window.
    old_keys = list(AuthRateLimit.objects.filter(
        expires_at__lt=now - timedelta(days=1),
    ).values_list("pk", flat=True)[:100])
    if old_keys:
        AuthRateLimit.objects.filter(pk__in=old_keys).delete()

    AuthRateLimit.objects.get_or_create(
        key=key, defaults={"expires_at": end},
    )
    allowed = AuthRateLimit.objects.filter(
        key=key, attempts__lt=limit,
    ).update(attempts=F("attempts") + 1)
    return bool(allowed), max(1, math.ceil((end - now).total_seconds()))


def auth_request_ip(request):
    # Only trust the direct client address until hosting is configured.
    try:
        return str(ip_address(request.META.get("REMOTE_ADDR", "")))
    except ValueError:
        return "unknown"


def auth_limit_response(seconds):
    minutes = max(1, math.ceil(seconds / 60))
    response = HttpResponse(
        f"Too many requests from this network. Try again in about {minutes} minute(s).",
        status=429,
        content_type="text/plain; charset=utf-8",
    )
    response["Retry-After"] = str(seconds)
    response["Cache-Control"] = "no-store"
    return response


def auth_limit_unavailable():
    response = HttpResponse(
        "This request is temporarily unavailable. Please try again shortly.",
        status=503,
        content_type="text/plain; charset=utf-8",
    )
    response["Retry-After"] = "60"
    response["Cache-Control"] = "no-store"
    return response


class LimitedPasswordResetView(PasswordResetView):
    def post(self, request, *args, **kwargs):
        try:
            allowed, seconds = auth_rate_limit(
                "reset-ip", auth_request_ip(request), 10, 15 * 60,
            )
        except DatabaseError:
            return auth_limit_unavailable()
        if not allowed:
            return auth_limit_response(seconds)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        email = form.cleaned_data["email"].strip().casefold()
        try:
            allowed, _ = auth_rate_limit("reset-email", email, 3, 60 * 60)
        except DatabaseError:
            return auth_limit_unavailable()
        if not allowed:
            # Same destination whether the address exists or not.
            return redirect(self.get_success_url())
        return super().form_valid(form)
