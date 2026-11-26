"""Run with: python manage.py sync_gmail --username victornjane --watch

Install at applications/management/commands/sync_gmail.py.
Uses the existing collector, marketing rules, duplicate checks and sync lock.
No applications are accepted or modified by this command.
"""
import time
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, close_old_connections
from django.db.models import Q
from django.utils import timezone

from applications.gmail_sync import SyncError, collect_batch
from applications.models import GmailConnection


class Command(BaseCommand):
    help = "Fetch Gmail previews once, or repeatedly with --watch. Review is still required."
    requires_migrations_checks = True

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument('--username', help='Tracker username, not the Gmail address.')
        target.add_argument('--all-users', action='store_true', help='Sync all active tracker users with Gmail connected.')
        parser.add_argument('--watch', action='store_true', help='Keep running until Ctrl+C.')
        parser.add_argument('--interval', type=int, default=300, help='Seconds between passes in watch mode (minimum 60).')
        parser.add_argument('--max-batches', type=int, default=5, help='Maximum 20-message batches per account per pass (1 to 10).')

    def stamp(self):
        return timezone.localtime().strftime('%Y-%m-%d %H:%M:%S')

    def sync_one_batch(self, connection_id):
        now = timezone.now()
        lease = now + timedelta(minutes=2)
        # Same conditional database claim used by the manual Sync now view.
        claimed = GmailConnection.objects.filter(
            pk=connection_id, owner__is_active=True,
        ).filter(
            Q(sync_locked_until__isnull=True) | Q(sync_locked_until__lt=now)
        ).update(sync_locked_until=lease)
        if not claimed:
            return None
        try:
            connection = GmailConnection.objects.select_related('owner').filter(
                pk=connection_id, owner__is_active=True,
            ).first()
            if connection is None:
                return None
            return collect_batch(connection)
        finally:
            # Never release a lease that another process has since acquired.
            GmailConnection.objects.filter(
                pk=connection_id, sync_locked_until=lease,
            ).update(sync_locked_until=None)

    def run_pass(self, owner_id, max_batches):
        connections = GmailConnection.objects.filter(owner__is_active=True)
        if owner_id is not None:
            connections = connections.filter(owner_id=owner_id)
        connection_ids = list(connections.order_by('pk').values_list('pk', flat=True))
        if not connection_ids:
            self.stdout.write(f'[{self.stamp()}] No connected Gmail account found. Connect it on the site first.')
            return

        for connection_id in connection_ids:
            for batch in range(1, max_batches + 1):
                try:
                    result = self.sync_one_batch(connection_id)
                except SyncError as exc:
                    # SyncError contains a sanitized message from the existing service.
                    self.stderr.write(f'[{self.stamp()}] Connection {connection_id}: {exc}')
                    break
                except DatabaseError:
                    self.stderr.write(
                        f'[{self.stamp()}] Connection {connection_id}: database unavailable or busy. '
                        'Check migrations and retry; watch mode will try again next pass.'
                    )
                    close_old_connections()
                    break
                except Exception as exc:
                    # Unexpected provider/library errors must not print tokens or email bodies.
                    raise CommandError(
                        f'Connection {connection_id}: unexpected {type(exc).__name__}. '
                        'Worker stopped. Report the error type without sharing secrets.'
                    ) from None

                if result is None:
                    self.stdout.write(f'[{self.stamp()}] Connection {connection_id}: busy, disconnected, or inactive; skipped.')
                    break
                added, more = result
                self.stdout.write(
                    f'[{self.stamp()}] Connection {connection_id}, batch {batch}: '
                    f'{added} new preview(s) saved. Check Pending review and Ignored.'
                )
                if not more:
                    self.stdout.write('Search complete. The next pass starts a fresh search.')
                    break
                if batch == max_batches:
                    self.stdout.write('More history remains; the next pass will continue this search.')

    def handle(self, *args, **options):
        if options['interval'] < 60:
            raise CommandError('--interval must be at least 60 seconds.')
        if not 1 <= options['max_batches'] <= 10:
            raise CommandError('--max-batches must be between 1 and 10.')

        owner_id = None
        if options['username']:
            User = get_user_model()
            owner_id = User.objects.filter(
                **{User.USERNAME_FIELD: options['username']}, is_active=True,
            ).values_list('pk', flat=True).first()
            if owner_id is None:
                raise CommandError('No active tracker user has that username.')

        mode = 'Automatic sync started. Press Ctrl+C to stop.' if options['watch'] else 'Running one sync pass.'
        self.stdout.write(mode)
        self.stdout.write('This fetches previews only. Nothing is accepted automatically.')
        try:
            while True:
                close_old_connections()
                try:
                    self.run_pass(owner_id, options['max_batches'])
                except DatabaseError:
                    self.stderr.write('Database unavailable. Check migrations or database access.')
                finally:
                    close_old_connections()
                if not options['watch']:
                    break
                self.stdout.write(f'Waiting {options["interval"]} seconds before the next pass...')
                self.stdout.flush()
                time.sleep(options['interval'])
        except KeyboardInterrupt:
            self.stdout.write('\nAutomatic sync stopped. Saved previews are retained.')
