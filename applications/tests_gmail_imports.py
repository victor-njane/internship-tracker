"""Run with: python manage.py test applications.tests_gmail_imports"""
import base64
import json
from datetime import timedelta
from unittest.mock import patch, Mock

from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from .gmail_sync import collect_batch, parse_message, suggest_type, SyncError, GmailReader
from .models import Application, ContactEvent, GmailConnection, ImportedEmail


def fake_message(mid='abc1', text='Thank you for applying for Software Engineering Intern at Example Company.'):
    return {'id': mid, 'internalDate': str(int(timezone.now().timestamp()*1000)), 'payload': {
        'mimeType':'text/plain', 'headers':[{'name':'Subject','value':'Application received'},
        {'name':'From','value':'Recruiter <recruiter@example.com>'}],
        'body':{'data':base64.urlsafe_b64encode(text.encode()).decode()}}}


@override_settings(GMAIL_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
                   GOOGLE_CLIENT_ID='fake-client', GOOGLE_CLIENT_SECRET='fake-secret')
class GmailImportTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('import-owner', password='test-only-password')
        self.other=get_user_model().objects.create_user('other-owner')
        self.client.force_login(self.user)
        self.app=Application.objects.create(owner=self.user,company='Example',position='Intern',
            date_applied=timezone.localdate(),status='offer',next_action='Reply',next_action_date=timezone.localdate())
        self.foreign=Application.objects.create(owner=self.other,company='Private',position='Intern',date_applied=timezone.localdate())
        data={'token':'test-access','refresh_token':'test-refresh','expiry':(timezone.now()+timedelta(hours=1)).isoformat()}
        self.connection=GmailConnection.objects.create(owner=self.user,email='me@example.com',
            encrypted_tokens=Fernet(settings.GMAIL_TOKEN_ENCRYPTION_KEY.encode()).encrypt(json.dumps(data).encode()).decode())
        self.item=ImportedEmail.objects.create(owner=self.user,mailbox='me@example.com',message_id='existing',**parse_message(fake_message()))
        self.payload={'destination':'existing','application':self.app.pk,'event_type':'confirmation',
            'event_date':timezone.localdate().isoformat(),'status':'','summary':'Reviewed confirmation'}

    def test_urls_and_templates(self):
        for name in ('dashboard','gmail_settings','import_list'):
            self.assertEqual(self.client.get(reverse(name)).status_code,200)
        self.assertEqual(self.client.get(reverse('dashboard'),{'status':'offer'}).status_code,200)
        self.assertEqual(self.client.get(reverse('import_review',args=[self.item.pk])).status_code,200)

    def test_accept_existing_once_and_keep_fields(self):
        url=reverse('import_review',args=[self.item.pk])
        self.assertEqual(self.client.post(url,self.payload).status_code,302)
        self.client.post(url,self.payload)
        self.assertEqual(ContactEvent.objects.count(),1)
        self.assertEqual(Application.objects.filter(owner=self.user).count(),1)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status,'offer')
        self.assertEqual(self.app.next_action,'Reply')
        self.item.refresh_from_db()
        self.assertEqual(self.item.application,self.app)

    def test_create_new_and_explicit_status(self):
        data={**self.payload,'destination':'new','company':'New Co','position':'Intern',
              'date_applied':timezone.localdate().isoformat(),'status':'interviewing'}
        self.client.post(reverse('import_review',args=[self.item.pk]),data)
        self.assertTrue(Application.objects.filter(owner=self.user,company='New Co',status='interviewing').exists())
        self.assertEqual(ContactEvent.objects.count(),1)

    def test_existing_status_only_changes_when_selected(self):
        self.client.post(reverse('import_review',args=[self.item.pk]),{**self.payload,'status':'rejected'})
        self.app.refresh_from_db(); self.assertEqual(self.app.status,'rejected')

    def test_required_new_fields(self):
        response=self.client.post(reverse('import_review',args=[self.item.pk]),{**self.payload,'destination':'new'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(ContactEvent.objects.count(),0)
        self.item.refresh_from_db(); self.assertEqual(self.item.review_state,'pending')

    def test_cannot_link_foreign_application(self):
        response=self.client.post(reverse('import_review',args=[self.item.pk]),{**self.payload,'application':self.foreign.pk})
        self.assertEqual(response.status_code,200)
        self.assertEqual(ContactEvent.objects.count(),0)

    def test_private_review_and_ignore(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('import_review',args=[self.item.pk])).status_code,404)
        self.assertEqual(self.client.post(reverse('import_ignore',args=[self.item.pk])).status_code,404)
        self.assertNotContains(self.client.get(reverse('import_list')),'recruiter@example.com')

    def test_ignore_and_revisit(self):
        self.client.post(reverse('import_ignore',args=[self.item.pk]))
        self.item.refresh_from_db(); self.assertEqual(self.item.review_state,'ignored')
        self.client.post(reverse('import_review',args=[self.item.pk]),self.payload)
        self.item.refresh_from_db(); self.assertEqual(self.item.review_state,'accepted')
        self.client.post(reverse('import_ignore',args=[self.item.pk]))
        self.item.refresh_from_db(); self.assertEqual(self.item.review_state,'accepted')

    def test_transaction_rolls_back_acceptance(self):
        with patch('applications.import_views.ContactEvent.objects.create',side_effect=RuntimeError('test')):
            with self.assertRaises(RuntimeError):
                self.client.post(reverse('import_review',args=[self.item.pk]),self.payload)
        self.item.refresh_from_db(); self.assertEqual(self.item.review_state,'pending')

    def test_post_only_login_csrf(self):
        self.assertEqual(self.client.get(reverse('gmail_sync')).status_code,405)
        self.assertEqual(self.client.get(reverse('import_ignore',args=[self.item.pk])).status_code,405)
        secure=Client(enforce_csrf_checks=True); secure.force_login(self.user)
        self.assertEqual(secure.post(reverse('gmail_sync')).status_code,403)
        self.client.logout(); self.assertEqual(self.client.get(reverse('import_list')).status_code,302)

    def test_sync_lease_and_error_release(self):
        self.connection.sync_locked_until=timezone.now()+timedelta(minutes=1); self.connection.save()
        with patch('applications.import_views.collect_batch') as fn:
            self.client.post(reverse('gmail_sync')); fn.assert_not_called()
        self.connection.sync_locked_until=None; self.connection.save()
        with patch('applications.import_views.collect_batch',side_effect=SyncError('test failure')):
            self.client.post(reverse('gmail_sync'))
        self.connection.refresh_from_db(); self.assertIsNone(self.connection.sync_locked_until)

    def test_pagination_and_repeat_sync(self):
        with patch('applications.gmail_sync.GmailReader.get',side_effect=[{'messages':[{'id':'abc1'}],'nextPageToken':'page2'},fake_message()]):
            self.assertEqual(collect_batch(self.connection),(1,True))
        self.connection.refresh_from_db(); self.assertEqual(self.connection.sync_page_token,'page2')
        with patch('applications.gmail_sync.GmailReader.get',return_value={'messages':[{'id':'abc1'}]}) as get:
            self.assertEqual(collect_batch(self.connection),(0,False))
            self.assertEqual(get.call_args.args[1]['pageToken'],'page2')
        self.connection.refresh_from_db(); self.assertEqual(self.connection.sync_page_token,'')
        self.assertEqual(ImportedEmail.objects.filter(message_id='abc1').count(),1)

    def test_partial_sync_retries_without_duplicates(self):
        with patch('applications.gmail_sync.GmailReader.get',side_effect=[{'messages':[{'id':'a'},{'id':'b'}]},fake_message('a'),SyncError('network')]):
            with self.assertRaises(SyncError): collect_batch(self.connection)
        self.assertEqual(ImportedEmail.objects.filter(message_id='a').count(),1)
        with patch('applications.gmail_sync.GmailReader.get',side_effect=[{'messages':[{'id':'a'},{'id':'b'}]},fake_message('b')]):
            self.assertEqual(collect_batch(self.connection),(1,False))
        self.assertEqual(ImportedEmail.objects.filter(message_id__in=['a','b']).count(),2)

    def test_dedup_survives_reconnect(self):
        self.item.review_state='ignored'; self.item.save()
        encrypted=self.connection.encrypted_tokens
        self.connection.delete()
        connection=GmailConnection.objects.create(owner=self.user,email='ME@example.com',encrypted_tokens=encrypted)
        with patch('applications.gmail_sync.GmailReader.get',return_value={'messages':[{'id':'existing'}]}) as get:
            self.assertEqual(collect_batch(connection),(0,False)); self.assertEqual(get.call_count,1)

    def test_refresh_stays_encrypted(self):
        reader=GmailReader(self.connection)
        response=Mock(status_code=200); response.json.return_value={'access_token':'new-secret','expires_in':3600}
        with patch('applications.gmail_sync.requests.post',return_value=response): reader.refresh()
        self.connection.refresh_from_db(); self.assertNotIn('new-secret',self.connection.encrypted_tokens)
        self.assertEqual(GmailReader(self.connection).tokens['token'],'new-secret')
        response.status_code=400
        with patch('applications.gmail_sync.requests.post',return_value=response):
            with self.assertRaises(SyncError): reader.refresh()

    def test_html_no_remote_content_or_script(self):
        msg=fake_message(text='<script>alert(1)</script><p>Thank you for applying</p><img src="https://example.com/track">')
        msg['payload']['mimeType']='text/html'
        parsed=parse_message(msg)
        self.assertNotIn('alert',parsed['preview']); self.assertNotIn('https://',parsed['preview'])
        self.assertEqual(parsed['suggested_type'],'confirmation')
        self.item.preview='<script>unsafe()</script>'; self.item.save()
        response=self.client.get(reverse('import_review',args=[self.item.pk]))
        self.assertContains(response,'&lt;script&gt;'); self.assertNotContains(response,'<script>unsafe()')

    def test_suggestions_are_conservative(self):
        self.assertEqual(suggest_type('We are unable to offer you the role',''),'rejection')
        self.assertEqual(suggest_type('Store offers',''),'other')
