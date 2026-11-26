from django.conf import settings
from django.db import models
from django.utils import timezone


class Application(models.Model):
    class Status(models.TextChoices):
        APPLIED = "applied", "Applied"
        INTERVIEWING = "interviewing", "Interviewing"
        OFFER = "offer", "Offer received"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="applications",
    )
    
    company = models.CharField(max_length=150)
    position = models.CharField(max_length=200)
    application_url = models.URLField(max_length=1000, blank=True)
    source = models.CharField(max_length=100, blank=True)
    date_applied = models.DateField()
    application_deadline = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.APPLIED,
    )
    next_action = models.CharField(max_length=200, blank=True)
    next_action_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date_applied", "company"]

    def __str__(self):
        return f"{self.company} — {self.position}"


class ContactEvent(models.Model):
    class EventType(models.TextChoices):
        CONFIRMATION = "confirmation", "Automatic confirmation"
        RECRUITER = "recruiter", "Recruiter response"
        INTERVIEW = "interview", "Interview invitation"
        OFFER = "offer", "Offer"
        REJECTION = "rejection", "Rejection"
        FOLLOW_UP = "follow_up", "My follow-up"
        OTHER = "other", "Other"

    application = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name="contact_events",
    )
    date = models.DateField()
    event_type = models.CharField(
        max_length=20,
        choices=EventType.choices,
    )
    summary = models.TextField()

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.get_event_type_display()} — {self.date}"


class GmailConnection(models.Model):
    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="gmail_connection",
    )
    email = models.EmailField()
    encrypted_tokens = models.TextField(editable=False)
    connected_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_sync_attempt_at = models.DateTimeField(null=True, blank=True)
    last_search_completed_at = models.DateTimeField(null=True, blank=True)
    last_sync_error = models.CharField(max_length=500, blank=True)
    sync_page_token = models.TextField(blank=True)
    sync_query = models.TextField(blank=True)
    sync_locked_until = models.DateTimeField(null=True, blank=True)
    @property
    def sync_in_progress(self):
        return bool(
            self.sync_locked_until
            and self.sync_locked_until > timezone.now()
        )
    def __str__(self):
        return self.email


class ImportedEmail(models.Model):
    class ReviewState(models.TextChoices):
        PENDING = "pending", "Pending review"
        ACCEPTED = "accepted", "Accepted"
        IGNORED = "ignored", "Ignored"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name="imported_emails")
    mailbox = models.EmailField()
    message_id = models.CharField(max_length=255)
    subject = models.CharField(max_length=500, blank=True)
    sender = models.CharField(max_length=500, blank=True)
    received_at = models.DateTimeField()
    preview = models.TextField(blank=True)
    suggested_type = models.CharField(max_length=20, choices=ContactEvent.EventType.choices,
                                      default=ContactEvent.EventType.OTHER)
    review_state = models.CharField(max_length=10, choices=ReviewState.choices,
                                    default=ReviewState.PENDING)
    application = models.ForeignKey(Application, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="source_emails")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at", "-pk"]
        constraints = [models.UniqueConstraint(
            fields=["owner", "mailbox", "message_id"], name="unique_user_mailbox_message"
        )]

    def __str__(self):
        return self.subject or "Email without subject"

class AuthRateLimit(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    attempts = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)
