"""Read-only Gmail search and plain-text preview collection. No AI service is used."""
import base64
import binascii
import json
import re
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from email.header import decode_header, make_header
from html import unescape
from html.parser import HTMLParser
from urllib.parse import quote

import requests
from cryptography.fernet import InvalidToken
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from .gmail_views import encryption
from .models import GmailConnection, ImportedEmail

BATCH_SIZE = 20
PREVIEW_LIMIT = 8000
SEARCH_TERMS = '{application interview "job offer" "offer of employment" "thank you for applying" "moving forward"}'


class SyncError(Exception):
    """A safe, user-facing error without tokens or provider response bodies."""


class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'head'):
            self.hidden += 1
        if tag in ('br', 'p', 'div', 'li', 'tr') and not self.hidden:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'head') and self.hidden:
            self.hidden -= 1
        if tag in ('p', 'div', 'li', 'tr') and not self.hidden:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def header_text(value):
    try:
        return str(make_header(decode_header(value)))[:500]
    except (LookupError, UnicodeError, ValueError):
        return value[:500]


def plain_preview(payload, snippet):
    plain, html = [], []
    stack = [payload]
    for _ in range(200):
        if not stack:
            break
        part = stack.pop()
        if part.get('filename'):
            continue  # Never fetch or render attachments.
        stack.extend(reversed(part.get('parts', [])))
        data = part.get('body', {}).get('data', '')
        mime = part.get('mimeType', '')
        if not data or mime not in ('text/plain', 'text/html'):
            continue
        try:
            data = data[:160000]
            raw = base64.urlsafe_b64decode(data + '=' * (-len(data) % 4))
            headers = {h['name'].lower(): h['value'] for h in part.get('headers', [])}
            charset = re.search(r'charset=["\s]*([^;"\s]+)', headers.get('content-type', ''), re.I)
            try:
                decoded = raw.decode(charset.group(1) if charset else 'utf-8', errors='replace')
            except LookupError:
                decoded = raw.decode('utf-8', errors='replace')
        except (ValueError, binascii.Error):
            continue
        if mime == 'text/plain':
            plain.append(decoded)
        else:
            parser = TextOnly()
            parser.feed(decoded)
            html.append(''.join(parser.parts))
    result = '\n'.join(plain or html) or unescape(snippet)
    return result.strip()[:PREVIEW_LIMIT]

def _recognition_text(value):
    value = value.casefold().replace("’", "'")
    return re.sub(r"\s+", " ", value).strip()


def suggest_type(subject, preview):
    text = _recognition_text(subject + " " + preview[:3000])

    rules = [
        ("rejection", [
            r"\b(?:not|won't|will not) (?:be )?(?:moving|move) forward\b",
            r"\b(?:have|has) not been selected\b",
            r"\b(?:proceed|proceeding|moving forward) with other candidates\b",
            r"\bunable to offer (?:you )?(?:the |a |an )?(?:role|position|internship|employment)\b",
            r"\bregret to inform you\b",
        ]),
        ("offer", [
            r"\boffer of employment\b",
            r"\b(?:your|our) (?:job|internship) offer\b",
            r"\b(?:pleased|delighted|excited) to offer you\b.{0,120}\b(?:position|role|internship|employment)\b",
        ]),
        ("interview", [
            r"\binterview invitation\b",
            r"\bschedule (?:an?|your|the) interview\b",
            r"\binvite you to (?:an? )?interview\b",
            r"\binterview (?:has been |is )?scheduled\b",
            r"\bconfirm your interview\b",
            r"\bavailability for (?:an?|the) interview\b",
        ]),
        ("confirmation", [
            r"\b(?:thank you|thanks) for applying\b",
            r"\b(?:we have |we've )?received your application\b",
            r"\bapplication (?:has been |was |is )?(?:successfully )?(?:received|submitted)\b",
            r"\b(?:reviewing|review) (?:each|your) application\b",
            r"\bappreciate the time you took to apply\b",
        ]),
    ]

    for event_type, patterns in rules:
        if any(re.search(pattern, text) for pattern in patterns):
            return event_type

    return "other"


def is_marketing_email(subject, preview):
    # Hiring signals take priority over promotional wording.
    if suggest_type(subject, preview) != "other":
        return False

    subject = _recognition_text(subject)
    patterns = [
        r"\b(?:job|career) alerts?\b",
        r"\b(?:jobs?|roles?|opportunities) (?:picked|recommended|selected|matched) for you\b",
        r"\b(?:daily|weekly) (?:job|career) (?:digest|roundup|update)\b",
        r"\bhere's what's live (?:right )?now\b",
        r"\b(?:job|career) newsletter\b",
        r"\bflash sale\b",
        r"\bpromo code\b",
        r"\blimited[- ]time offer\b",
    ]

    return any(re.search(pattern, subject) for pattern in patterns)


def recheck_pending_imports(owner):
    pending = ImportedEmail.objects.filter(
        owner=owner,
        review_state=ImportedEmail.ReviewState.PENDING,
    )

    for item in pending.iterator():
        changes = {
            "suggested_type": suggest_type(item.subject, item.preview),
        }

        if is_marketing_email(item.subject, item.preview):
            changes["review_state"] = ImportedEmail.ReviewState.IGNORED

        # Do not overwrite an item accepted during this recheck.
        ImportedEmail.objects.filter(
            pk=item.pk,
            owner=owner,
            review_state=ImportedEmail.ReviewState.PENDING,
        ).update(**changes)

def parse_message(message):
    payload = message.get('payload', {})
    headers = {h['name'].lower(): h['value'] for h in payload.get('headers', [])}
    subject = header_text(headers.get('subject', ''))
    preview = plain_preview(payload, message.get('snippet', ''))
    try:
        received = datetime.fromtimestamp(int(message['internalDate']) / 1000, dt_timezone.utc)
    except (KeyError, ValueError, OverflowError, OSError):
        raise SyncError('Gmail returned a message without a usable received date. Retry syncing later.')
    return dict(subject=subject, sender=header_text(headers.get('from', '')),
                received_at=received, preview=preview,
                suggested_type=suggest_type(subject, preview))


class GmailReader:
    def __init__(self, connection):
        self.connection = connection
        try:
            self.tokens = json.loads(encryption().decrypt(connection.encrypted_tokens.encode()).decode())
        except (InvalidToken, ValueError, ImproperlyConfigured):
            raise SyncError('The saved Gmail connection cannot be decrypted. Restore the original encryption key or disconnect and reconnect Gmail.')

    def refresh(self):
        if not self.tokens.get('refresh_token'):
            raise SyncError('Gmail needs to be reconnected. Disconnect and connect it again.')
        try:
            response = requests.post('https://oauth2.googleapis.com/token', data={
                'client_id': settings.GOOGLE_CLIENT_ID,
                'client_secret': settings.GOOGLE_CLIENT_SECRET,
                'refresh_token': self.tokens['refresh_token'],
                'grant_type': 'refresh_token',
            }, timeout=(5, 15))
            if response.status_code in (400, 401):
                raise SyncError('Gmail authorization expired or was revoked. Disconnect and reconnect Gmail, then retry.')
            response.raise_for_status()
            data = response.json()
            self.tokens['token'] = data['access_token']
            if data.get('refresh_token'):
                self.tokens['refresh_token'] = data['refresh_token']
            self.tokens['expiry'] = (timezone.now() + timedelta(seconds=int(data.get('expires_in', 3600)))).isoformat()
            encrypted = encryption().encrypt(json.dumps(self.tokens).encode()).decode()
            updated = GmailConnection.objects.filter(pk=self.connection.pk, owner=self.connection.owner).update(encrypted_tokens=encrypted)
            if not updated:
                raise SyncError('Gmail was disconnected during sync.')
        except requests.RequestException:
            raise SyncError('Could not renew Gmail access. Check your connection and try again.')
        except (KeyError, ValueError, AttributeError, ImproperlyConfigured):
            raise SyncError('Could not renew Gmail access. Check your Google configuration and reconnect.')

    def get(self, path, params=None):
        if not GmailConnection.objects.filter(
            pk=self.connection.pk,
            owner_id=self.connection.owner_id,
            owner__is_active=True,
        ).exists():
            raise SyncError(
                "The Gmail connection is no longer available. Sync stopped."
            )

        expiry = self.tokens.get('expiry')
        try:
            expiry = datetime.fromisoformat(expiry) if expiry else None
            if expiry and expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=dt_timezone.utc)
        except (TypeError, ValueError):
            expiry = None
        if not self.tokens.get('token') or not expiry or expiry <= timezone.now() + timedelta(seconds=60):
            self.refresh()
        try:
            for attempt in range(2):
                response = requests.get('https://gmail.googleapis.com/gmail/v1/users/me/' + path,
                    params=params, headers={'Authorization': 'Bearer ' + self.tokens['token']}, timeout=(5, 15))
                if response.status_code == 401 and attempt == 0:
                    self.refresh()
                    continue
                if response.status_code in (401, 403):
                    raise SyncError('Gmail access was denied. Check the Gmail API and read-only permission, or reconnect Gmail.')
                if response.status_code == 429:
                    raise SyncError('Google temporarily limited requests. Wait a minute and try again.')
                if response.status_code == 404:
                    return None  # Message may have been deleted after listing.
                response.raise_for_status()
                return response.json()
        except requests.RequestException:
            raise SyncError('Gmail could not be reached. Any previews already fetched are saved; retry to continue.')
        except ValueError:
            raise SyncError('Gmail returned an unexpected response. Please retry.')

def save_import_preview(connection, key, details):
    with transaction.atomic():
        # Use the same user-row lock as account deletion.
        active = get_user_model().objects.filter(
            pk=connection.owner_id,
            is_active=True,
        ).update(is_active=True)

        if not active:
            raise SyncError(
                "The tracker account was removed or disabled. Sync stopped."
            )

        if not GmailConnection.objects.filter(
            pk=connection.pk,
            owner_id=connection.owner_id,
        ).exists():
            raise SyncError(
                "Gmail was disconnected during sync. "
                "No further previews were saved."
            )

        _, created = ImportedEmail.objects.get_or_create(
            **key,
            defaults=details,
        )
        return created


def _collect_batch(connection):
    recheck_pending_imports(connection.owner)
    reader = GmailReader(connection)
    started = time.monotonic()
    # Freeze the window across pages so newly arriving messages do not shift the search.
    query = connection.sync_query
    if not query:
        now = timezone.now()
        query = f'after:{int((now-timedelta(days=90)).timestamp())} before:{int(now.timestamp())+1} -in:sent -in:drafts {SEARCH_TERMS}'
    GmailConnection.objects.filter(pk=connection.pk, owner=connection.owner).update(sync_query=query)
    params = {'q': query, 'maxResults': BATCH_SIZE, 'includeSpamTrash': 'false'}
    if connection.sync_page_token:
        params['pageToken'] = connection.sync_page_token
    result = reader.get('messages', params) or {}
    added = 0
    for item in result.get('messages', []):
        if time.monotonic() - started > 45:
            raise SyncError('This batch took too long. Previews already fetched are saved; press Sync again to finish the batch.')
        key = dict(owner=connection.owner, mailbox=connection.email.casefold(), message_id=item['id'])
        if ImportedEmail.objects.filter(**key).exists():
            continue
        message = reader.get('messages/' + quote(item['id'], safe=''), {'format': 'full'})
        if message is None:
            continue
        if not GmailConnection.objects.filter(pk=connection.pk, owner=connection.owner).exists():
            raise SyncError('Gmail was disconnected during sync. No further previews were saved.')
        details = parse_message(message)

        if is_marketing_email(details["subject"], details["preview"]):
            details["review_state"] = ImportedEmail.ReviewState.IGNORED

        created = save_import_preview(connection, key, details)
        added += int(created)
    next_page = result.get('nextPageToken', '')
    GmailConnection.objects.filter(pk=connection.pk, owner=connection.owner).update(
        sync_page_token=next_page, sync_query=query if next_page else '', last_sync_at=timezone.now())
    return added, bool(next_page)

def collect_batch(connection):
    # Only the current lock holder may update this attempt's status.
    current = GmailConnection.objects.filter(
        pk=connection.pk,
        owner=connection.owner,
        sync_locked_until=connection.sync_locked_until,
    )

    if not current.update(last_sync_attempt_at=timezone.now()):
        raise SyncError(
            "The Gmail connection or sync lock is no longer available. "
            "Sync stopped."
        )

    try:
        added, more = _collect_batch(connection)

    except SyncError as exc:
        current.update(last_sync_error=str(exc)[:500])
        raise

    except Exception:
        current.update(
            last_sync_error=(
                "Sync stopped because of an unexpected error. "
                "Check the server or sync terminal before retrying."
            ),
        )
        raise

    else:
        changes = {"last_sync_error": ""}

        if not more:
            changes["last_search_completed_at"] = timezone.now()

        current.update(**changes)
        return added, more