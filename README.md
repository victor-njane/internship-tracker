# Internship Tracker

A Django web application that organizes internship applications,
deadlines, follow-ups, and communication history in one place.

Built to address a practical part of the internship search: keeping
application records and email updates organized without relying on
a spreadsheet alone.

## Features

- **Application dashboard:** Create and edit applications, search by
  company or position, and filter by status.
- **Deadlines and follow-ups:** Track application deadlines and next
  actions, mark follow-ups complete, and preserve communication history.
- **CSV export:** Download application records, including records hidden
  by the current dashboard filters.
- **Account management:** Register, sign in with a username or an
  unambiguous email address, reset passwords, and delete an account
  with password confirmation.
- **Request limits:** Limit repeated login failures, signup submissions,
  and password-reset requests.
- **Optional Gmail integration:** Find potential application-related
  emails and review them before saving changes.

## Gmail import workflow

The importer uses keyword and pattern matching rather than an AI model.

1. Connect a Gmail account through Google OAuth.
2. Search received messages from the last 90 days.
3. Review plain-text previews and suggested application details.
4. Accept relevant messages or ignore unrelated ones.

Attachments are not downloaded. Accepted and ignored messages are
remembered to reduce duplicate imports. Suggestions can be incorrect,
so application records change only after user review.

Gmail tokens are encrypted using Fernet. Syncing can run manually or
through a local background management command.

## Technology

- Python and Django
- SQLite
- Django templates, HTML, and CSS
- Google OAuth and Gmail API
- Fernet token encryption
- Django Axes for failed-login protection
- Git and GitHub

Dependency versions are pinned in `requirements.txt`.

## Project status

The application currently runs locally. Public hosting is optional
and is not configured in this repository.

The project has been set up successfully from a clean repository clone.
The latest local test run passed all 17 tests in the applications suite.
This does not imply complete test coverage of every feature.

## Local setup — Windows

The verified development environment uses Python 3.15.2 and Django 6.1.2.
Other Python versions have not been verified for the complete project.

Open Command Prompt in the project folder.

### 1. Install dependencies

```bat
python -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python -m pip check
```

### 2. Configure the environment

On a fresh checkout:

```bat
copy .env.example .env
```

Do not overwrite an existing `.env`.

Generate a Django secret key:

```bat
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Set these values in `.env`, replacing the placeholder with the generated
key. Keep the secret key inside quotes.

```dotenv
DJANGO_SECRET_KEY='YOUR_GENERATED_SECRET_KEY'
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost
```

Gmail credentials can remain blank when using the core application
without Gmail integration.

### 3. Create the database and start the app

```bat
python manage.py migrate
python manage.py check
python manage.py runserver
```

Open http://127.0.0.1:8000/ and create an account.

For local password-reset testing, find the reset email and link in the
server terminal. Actual email delivery is not configured.

## Optional Gmail setup

1. Enable the Gmail API in a Google Cloud project.
2. Configure the OAuth consent screen and a web application OAuth client.
   If the OAuth app is in Testing mode, add the connecting account as
   a test user.
3. Register this authorized redirect URI:

```text
http://127.0.0.1:8000/accounts/google/callback/
```

4. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and
   `GOOGLE_REDIRECT_URI` in `.env`.
5. Generate a token-encryption key:

```bat
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

6. Set `GMAIL_TOKEN_ENCRYPTION_KEY` in `.env`, restart the server,
   and connect Gmail from the application's Connected email page.

Keep the encryption key consistent while using saved Gmail connections.

### Background syncing

After connecting Gmail, open a second terminal in the project folder:

```bat
.venv\Scripts\activate.bat
python manage.py sync_gmail --username YOUR_USERNAME --watch --interval 300
```

Replace `YOUR_USERNAME` with your tracker username.

The command checks periodically while the process remains running.
Press Ctrl+C to stop it. Closing the terminal or shutting down the
computer also stops syncing.

## Tests

```bat
python manage.py test applications
```

The terminal output reports the current test count and results.

## Credentials and personal data

Do not commit or publish:

- `.env` or real credentials
- OAuth tokens or encryption keys
- Local SQLite databases or database backups
- Personal email previews or exported application records

Use fictional or redacted data in screenshots and demonstrations.

## Before public deployment

A public deployment needs additional configuration and review:

- Production hosting, HTTPS, and static-file serving
- Production email delivery and database backups
- Background-worker hosting if automatic syncing is enabled
- Hosting-specific client-IP handling for rate limits
- Google OAuth production requirements
- Gmail-derived data retention after disconnect or revocation

Django's local development server is intended for development.