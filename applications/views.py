import csv
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST
from datetime import timedelta
from django.utils import timezone
from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from django.db import DatabaseError

from .auth_limits import (
    auth_rate_limit,
    auth_request_ip,
    auth_limit_response,
    auth_limit_unavailable,
)

from .forms import ApplicationForm, ContactEventForm, SignUpForm
from .models import Application, ContactEvent


@login_required
def dashboard(request):
        today = timezone.localdate()
        week_end = today + timedelta(days=7)

        scheduled_actions = (
            Application.objects
            .filter(
                owner=request.user,
                next_action_date__isnull=False,
            )
            .exclude(next_action="")
            .exclude(next_action__isnull=True)
            .order_by("next_action_date", "company", "pk")
        )

        action_groups = [
        {
            "title": "Overdue",
            "style": "overdue",
            "items": scheduled_actions.filter(
                next_action_date__lt=today,
            ),
            "empty_message": "No overdue actions.",
        },
        {
            "title": "Due today",
            "style": "today",
            "items": scheduled_actions.filter(
                next_action_date=today,
            ),
            "empty_message": "Nothing due today.",
        },
        {
            "title": "Next seven days",
            "style": "upcoming",
            "items": scheduled_actions.filter(
                next_action_date__gt=today,
                next_action_date__lte=week_end,
            ),
            "empty_message": "Nothing scheduled for the next seven days.",
        },
    ]
        all_applications = Application.objects.filter(owner=request.user)

        search = request.GET.get("q", "").strip()
        selected_status = request.GET.get("status", "")

        applications = all_applications

        if search:
            applications = applications.filter(
            Q(company__icontains=search)
            | Q(position__icontains=search)
        )

        valid_statuses = Application.Status.values
        if selected_status in valid_statuses:
            applications = applications.filter(status=selected_status)
        else:
            selected_status = ""

        context = {
            "applications": applications,
            "today": today,
            "week_end": week_end,
            "search": search,
            "selected_status": selected_status,
            "statuses": Application.Status.choices,
            "total": all_applications.count(),
            "interviewing": all_applications.filter(
                status=Application.Status.INTERVIEWING
            ).count(),
            "offers": all_applications.filter(
                status=Application.Status.OFFER
            ).count(),
            "action_groups": action_groups,
        }

        return render(
            request,
            "applications/dashboard.html",
            context,
        )

@login_required
def application_add(request):
    if request.method == "POST":
        form = ApplicationForm(request.POST)

        if form.is_valid():
            application = form.save(commit=False)
            application.owner = request.user
            application.save()
            return redirect("dashboard")
    else:
        form = ApplicationForm()

    return render(
        request,
        "applications/application_form.html",
        {
            "form": form,
            "page_title": "Add application",
            "button_text": "Save application",
        },
    )


@login_required
def application_edit(request, pk):
    application = get_object_or_404(
    Application,
    pk=pk,
    owner=request.user,
)

    if request.method == "POST":
        form = ApplicationForm(request.POST, instance=application)

        if form.is_valid():
            form.save()
            return redirect("dashboard")
    else:
        form = ApplicationForm(instance=application)

    return render(
        request,
        "applications/application_form.html",
        {
            "form": form,
            "application": application,
            "page_title": "Edit application",
            "button_text": "Save changes",
        },
    )

@login_required
def application_history(request, pk):
    application = get_object_or_404(Application, pk=pk, owner=request.user)

    if request.method == "POST":
        form = ContactEventForm(request.POST)

        if form.is_valid():
            event = form.save(commit=False)
            event.application = application
            event.save()

            return redirect("application_history", pk=application.pk)
    else:
        form = ContactEventForm()

    events = application.contact_events.order_by("-date", "-id")

    return render(
        request,
        "applications/application_history.html",
        {
            "application": application,
            "events": events,
            "form": form,
        },
    )

@login_required
@require_http_methods(["GET", "POST"])
def contact_event_edit(request, pk, event_pk):
    application = get_object_or_404(Application, pk=pk, owner=request.user)

    event = get_object_or_404(
        ContactEvent,
        pk=event_pk,
        application=application,
    )

    if request.method == "POST":
        form = ContactEventForm(request.POST, instance=event)

        if form.is_valid():
            form.save()
            return redirect("application_history", pk=application.pk)
    else:
        form = ContactEventForm(instance=event)

    return render(
        request,
        "applications/contact_event_edit.html",
        {
            "application": application,
            "event": event,
            "form": form,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def contact_event_delete(request, pk, event_pk):
    application = get_object_or_404(Application, pk=pk, owner=request.user)

    event = get_object_or_404(
        ContactEvent,
        pk=event_pk,
        application=application,
    )

    if request.method == "POST":
        event.delete()
        return redirect("application_history", pk=application.pk)

    return render(
        request,
        "applications/contact_event_confirm_delete.html",
        {
            "application": application,
            "event": event,
        },
    )

@require_http_methods(["GET", "POST"])
def signup(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        try:
            allowed, seconds = auth_rate_limit(
                "signup-ip",
                auth_request_ip(request),
                5,
                60 * 60,
            )
        except DatabaseError:
            return auth_limit_unavailable()

        if not allowed:
            return auth_limit_response(seconds)

        form = SignUpForm(request.POST)

        if form.is_valid():
            form.save()
            return redirect("login")
    else:
        form = SignUpForm()

    return render(
        request,
        "registration/account_form.html",
        {
            "form": form,
            "is_signup": True,
        },
    )

@login_required
@require_POST
def application_complete_action(request, pk):
    application = get_object_or_404(Application, pk=pk, owner=request.user)
    expected_action = request.POST.get("expected_action", "")
    expected_date = request.POST.get("expected_date", "")
    actual_date = application.next_action_date.isoformat() if application.next_action_date else ""

    if not application.next_action.strip():
        messages.info(request, "This application has no action to complete.")
        return redirect("dashboard")
    if expected_action != application.next_action or expected_date != actual_date:
        messages.warning(request, "This action has changed. Review it before completing it.")
        return redirect("dashboard")

    with transaction.atomic():
        # Claim only the exact action shown on the page; repeated submissions do nothing.
        updated = Application.objects.filter(
            pk=application.pk,
            owner=request.user,
            next_action=expected_action,
            next_action_date=application.next_action_date,
        ).update(next_action="", next_action_date=None)
        if updated:
            due = actual_date or "Not scheduled"
            ContactEvent.objects.create(
                application=application,
                date=timezone.localdate(),
                event_type=ContactEvent.EventType.OTHER,
                summary=f"Completed action: {expected_action}\nOriginal due date: {due}",
            )

    if updated:
        messages.success(request, f"Action completed for {application.company}. Saved in history.")
    else:
        messages.info(request, "This action was already completed or changed. Refresh and review it.")
    return redirect("dashboard")

def csv_text(value):
    text = "" if value is None else str(value)
    probe = text.lstrip(" \t\r\n\ufeff")

    if (
        probe.startswith(("=", "+", "-", "@", "＝", "＋", "－", "＠"))
        or text.startswith(("\t", "\r", "\n"))
    ):
        return "[text] " + text

    return text


@login_required
@never_cache
@require_http_methods(["GET"])
def application_export(request):
    response = HttpResponse(content_type="text/csv; charset=utf-8")

    filename = (
        f"internship-applications-{timezone.localdate().isoformat()}.csv"
    )
    response["Content-Disposition"] = (
        f'attachment; filename="{filename}"'
    )
    response["X-Content-Type-Options"] = "nosniff"

    # Help Excel recognize accented characters and other UTF-8 text.
    response.write("\ufeff")

    writer = csv.writer(response, quoting=csv.QUOTE_ALL)

    writer.writerow([
        "Company",
        "Position",
        "Job URL",
        "Source",
        "Date applied",
        "Application deadline",
        "Status",
        "Next action",
        "Next-action date",
        "Notes",
    ])

    applications = Application.objects.filter(
        owner=request.user,
    ).order_by("-date_applied", "company", "pk")

    for application in applications.iterator():
        writer.writerow([
            csv_text(application.company),
            csv_text(application.position),
            csv_text(application.application_url),
            csv_text(application.source),
            application.date_applied.isoformat(),
            (
                application.application_deadline.isoformat()
                if application.application_deadline else ""
            ),
            csv_text(application.get_status_display()),
            csv_text(application.next_action),
            (
                application.next_action_date.isoformat()
                if application.next_action_date else ""
            ),
            csv_text(application.notes),
        ])

    return response

def login_client_ip(request):
    return request.META.get("REMOTE_ADDR")


def login_locked_response(request, credentials=None, *args, **kwargs):
    response = HttpResponse(
        "Too many unsuccessful login attempts from this network. "
        "Please wait 15 minutes before trying again.",
        status=429,
        content_type="text/plain; charset=utf-8",
    )
    response["Retry-After"] = "900"
    response["Cache-Control"] = "no-store"
    return response