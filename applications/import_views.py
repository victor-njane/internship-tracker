from datetime import timedelta
import re

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .gmail_sync import SyncError, collect_batch
from .models import Application, ContactEvent, GmailConnection, ImportedEmail

def suggest_company_and_position(subject):
    subject = " ".join(subject.split())
    subject = re.sub(
        r"^(?:(?:re|fw|fwd):\s*)+",
        "",
        subject,
        flags=re.I,
    )

    patterns = [
        r"^(?:your )?application for (?P<position>.+?) at (?P<company>.+)$",
        r"^(?:thank you|thanks) for applying (?:for|to) (?P<position>.+?) at (?P<company>.+)$",
        r"^interview invitation for (?P<position>.+?) at (?P<company>.+)$",
    ]

    for pattern in patterns:
        match = re.fullmatch(pattern, subject, flags=re.I)

        if match:
            company = match.group("company").strip()
            position = match.group("position").strip()

            # Multiple "at" separators are ambiguous.
            if re.search(r"\bat\b", company, flags=re.I):
                return "", ""

            if 1 <= len(company) <= 150 and 1 <= len(position) <= 200:
                return company, position

    return "", ""


def find_matching_application(owner, company, position):
    if not company or not position:
        return None

    def normalized(value):
        return " ".join(value.casefold().split())

    matches = []

    for application in Application.objects.filter(owner=owner):
        if (
            normalized(application.company) == normalized(company)
            and normalized(application.position) == normalized(position)
        ):
            matches.append(application)

            if len(matches) > 1:
                return None

    return matches[0] if matches else None

class ImportReviewForm(forms.Form):
    destination = forms.ChoiceField(choices=[('existing', 'Add to an existing application'), ('new', 'Create a new application')])
    application = forms.ModelChoiceField(queryset=Application.objects.none(), required=False,
        label='Existing application', help_text='Select the exact company AND role. Used only when adding to an existing application.')
    company = forms.CharField(max_length=150, required=False, help_text='Required only for a new application.')
    position = forms.CharField(max_length=200, required=False, label='Position / role', help_text='Required only for a new application.')
    date_applied = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        help_text='For a new application, confirm when you applied. An interview email date may be later.')
    event_type = forms.ChoiceField(choices=ContactEvent.EventType.choices, label='Communication type')
    event_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        label='Communication date', help_text='When the email arrived, not the scheduled interview date.')
    status = forms.ChoiceField(required=False, choices=[('', 'Keep current status (new applications start as Applied)')] + list(Application.Status.choices),
        label='Application status', help_text='Optional. Older emails should not overwrite a newer status unless you choose to.')
    summary = forms.CharField(max_length=8000, widget=forms.Textarea(attrs={'rows': 7}),
        help_text='Review or shorten this text before adding it to communication history.')

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['application'].queryset = Application.objects.filter(owner=owner)

    def clean(self):
        data = super().clean()
        if data.get('destination') == 'existing' and not data.get('application'):
            self.add_error('application', 'Choose one of your applications.')
        if data.get('destination') == 'new':
            for field in ('company', 'position', 'date_applied'):
                if not data.get(field):
                    self.add_error(field, 'Required for a new application.')
        return data


@login_required
@require_POST
@sensitive_variables()
def gmail_sync(request):
    connection = GmailConnection.objects.filter(owner=request.user).first()
    if connection is None:
        messages.info(request, 'Connect Gmail before syncing.')
        return redirect('gmail_settings')
    now = timezone.now()
    lease = now + timedelta(minutes=2)
    claimed = GmailConnection.objects.filter(pk=connection.pk).filter(
        Q(sync_locked_until__isnull=True) | Q(sync_locked_until__lt=now)
    ).update(sync_locked_until=lease)
    if not claimed:
        messages.info(request, 'A sync is already running. Wait for it to finish before trying again.')
        return redirect('import_list')
    try:
        if request.POST.get('restart') == '1':
            GmailConnection.objects.filter(pk=connection.pk).update(sync_query='', sync_page_token='')
        connection.refresh_from_db()
        added, more = collect_batch(connection)
        text = (
            f"{added} new email preview(s) saved. "
            "Check Pending review and Ignored."
        )
        if more:
            text += ' More matches are available; press Continue sync for the next batch.'
        else:
            text += ' The search is complete. No applications were changed.'
        messages.success(request, text)
    except SyncError as exc:
        messages.error(request, str(exc))
    except GmailConnection.DoesNotExist:
        messages.info(request, 'Gmail was disconnected during sync.')
    finally:
        GmailConnection.objects.filter(pk=connection.pk, sync_locked_until=lease).update(sync_locked_until=None)
    return redirect('import_list')


class BulkIgnoreForm(forms.Form):
    emails = forms.ModelMultipleChoiceField(
        queryset=ImportedEmail.objects.none(),
    )

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["emails"].queryset = ImportedEmail.objects.filter(
            owner=owner,
            review_state=ImportedEmail.ReviewState.PENDING,
        )

@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def import_list(request):
    if request.method == "POST":
        form = BulkIgnoreForm(request.POST, owner=request.user)

        selected = request.POST.getlist("emails")
        valid_ids = 0 < len(selected) <= 20 and all(
            value.isascii() and value.isdecimal()
            and len(value) <= 19 and 0 < int(value) <= 9223372036854775807
            for value in selected
        )

        if (
            request.POST.get("action") == "bulk_ignore"
            and valid_ids
            and form.is_valid()
        ):
            changed = ImportedEmail.objects.filter(
                owner=request.user,
                review_state=ImportedEmail.ReviewState.PENDING,
                pk__in=form.cleaned_data["emails"],
            ).update(
                review_state=ImportedEmail.ReviewState.IGNORED,
            )

            messages.success(
                request,
                f"{changed} email(s) moved to Ignored.",
            )
        else:
            messages.warning(
                request,
                "Select at least one pending email. If a selection was already "
                "reviewed, select again from the updated list.",
            )

        return redirect("import_list")

    state = request.GET.get("state", ImportedEmail.ReviewState.PENDING)
    if state not in ImportedEmail.ReviewState.values:
        state = ImportedEmail.ReviewState.PENDING

    items = ImportedEmail.objects.filter(
        owner=request.user,
        review_state=state,
    )

    return render(request, "applications/import_list.html", {
        "page_obj": Paginator(items, 20).get_page(request.GET.get("page")),
        "selected_state": state,
        "states": ImportedEmail.ReviewState.choices,
        "connection": GmailConnection.objects.filter(
            owner=request.user,
        ).first(),
    })


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def import_review(request, pk):
    item = get_object_or_404(
        ImportedEmail,
        pk=pk,
        owner=request.user,
    )

    if item.review_state == ImportedEmail.ReviewState.ACCEPTED:
        return render(
            request,
            "applications/import_review.html",
            {"item": item, "form": None},
        )

    if request.method == "POST":
        form = ImportReviewForm(request.POST, owner=request.user)

        if form.is_valid():
            data = form.cleaned_data

            with transaction.atomic():
                # Prevent duplicate applications or events.
                claimed = (
                    ImportedEmail.objects
                    .filter(pk=item.pk, owner=request.user)
                    .exclude(
                        review_state=ImportedEmail.ReviewState.ACCEPTED
                    )
                    .update(
                        review_state=ImportedEmail.ReviewState.ACCEPTED
                    )
                )

                if not claimed:
                    messages.info(request, "This email was already accepted.")
                    return redirect("import_list")

                if data["destination"] == "new":
                    application = Application.objects.create(
                        owner=request.user,
                        company=data["company"],
                        position=data["position"],
                        date_applied=data["date_applied"],
                        source="Gmail import",
                        status=data["status"] or Application.Status.APPLIED,
                    )
                else:
                    application = get_object_or_404(
                        Application,
                        pk=data["application"].pk,
                        owner=request.user,
                    )

                    if data["status"]:
                        application.status = data["status"]
                        application.save(update_fields=["status"])

                ContactEvent.objects.create(
                    application=application,
                    date=data["event_date"],
                    event_type=data["event_type"],
                    summary=data["summary"],
                )

                ImportedEmail.objects.filter(
                    pk=item.pk,
                    owner=request.user,
                ).update(application=application)

            messages.success(
                request,
                "Email accepted and saved in communication history. "
                "Next-action fields were not changed.",
            )
            return redirect(
                "application_history",
                pk=application.pk,
            )

    else:
        local_date = timezone.localtime(item.received_at).date()

        summary = (
            f"From: {item.sender}\n"
            f"Subject: {item.subject}\n\n"
            f"{item.preview}"
        )[:8000]

        company, position = suggest_company_and_position(item.subject)

        match = find_matching_application(
            request.user,
            company,
            position,
        )

        has_applications = Application.objects.filter(
            owner=request.user,
        ).exists()

        form = ImportReviewForm(
            owner=request.user,
            initial={
                "destination": "existing" if has_applications else "new",
                "application": match.pk if match else None,
                "company": company,
                "position": position,
                "event_type": item.suggested_type,
                "event_date": local_date,
                "date_applied": local_date,
                "summary": summary,
            },
        )

        if match:
            form.fields["application"].help_text = (
                "Suggested match based on company and role. "
                "Confirm it is the correct application."
            )
        else:
            form.fields["application"].help_text = (
                "No unique exact match was found. "
                "Choose an existing application, "
                "or select Create a new application above."
            )

    return render(
        request,
        "applications/import_review.html",
        {"item": item, "form": form},
    )


@login_required
@require_POST
def import_ignore(request, pk):
    item = get_object_or_404(ImportedEmail, pk=pk, owner=request.user)
    changed = ImportedEmail.objects.filter(pk=item.pk, owner=request.user,
        review_state=ImportedEmail.ReviewState.PENDING).update(review_state=ImportedEmail.ReviewState.IGNORED)
    if changed:
        messages.info(request, 'Email ignored. It will not return on the next sync; you can review it from the Ignored tab.')
    else:
        messages.info(request, 'This email was already reviewed.')
    return redirect('import_list')
