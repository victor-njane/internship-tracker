# Internship Tracker

A Django web application for organizing internship applications,
deadlines, follow-ups, and communication history.

## Features

- Create and edit internship applications.
- Search by company or position and filter by application status.
- Track application deadlines and upcoming actions.
- Record communication history and completed follow-ups.
- Export application records as CSV.
- Sign in using a username or an unambiguous email address.
- Reset passwords and delete an account with password confirmation.
- Limit repeated login failures, signup submissions, and password-reset requests.
- Connect Gmail to find potential application-related messages for review.

## Gmail integration

Gmail importing uses keyword and pattern matching, not an AI model.

- Searches received messages from the last 90 days.
- Saves plain-text previews without downloading attachments.
- Suggests communication types and, for supported subject formats,
  company and position.
- Requires review before an email changes application records.
- Remembers accepted and ignored messages to reduce duplicate imports.
- Supports manual syncing and a background management command.

Suggestions can be incorrect. Users should confirm them before saving.

## Technology

- Python and Django
- SQLite for local development
- Django templates, HTML, and CSS
- Google OAuth and Gmail API
- Fernet encryption for stored Gmail tokens
- Django Axes for failed-login protection

Exact installed dependency versions are recorded in `requirements.txt`.

## Local setup on Windows

The development environment uses Python 3.15.2 and Django 6.1.2.
Other Python versions have not been verified for this complete project.

Open Command Prompt in the project folder.

### 1. Create and activate a virtual environment

```bat
python -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python -m pip check
```

### 2. Configure environment variables

Copy the example file on a fresh checkout:

```bat
copy .env.example .env
```

Do not overwrite an existing `.env` containing your local configuration.

Generate a Django secret key:

```bat
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Place the generated value after `DJANGO_SECRET_KEY=` in `.env`.
Do not publish it.

For Gmail integration, configure a Google Cloud OAuth web application,
enable the Gmail API, and use this authorized redirect URI:

```text
http://127.0.0.1:8000/accounts/google/callback/
```

Add the client ID and client secret to `.env`.

Generate the Gmail token-encryption key:

```bat
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Place it after `GMAIL_TOKEN_ENCRYPTION_KEY=` in `.env`.
Keep the same encryption key while using saved Gmail connections.

### 3. Prepare the database and start the application

```bat
python manage.py migrate
python manage.py check
python manage.py runserver
```

Open http://127.0.0.1:8000/ and create an account.

### 4. Optional background Gmail sync

After connecting Gmail, open a second terminal in the project folder:

```bat
.venv\Scripts\activate.bat
python manage.py sync_gmail --username YOUR_USERNAME --watch --interval 300
```

Replace `YOUR_USERNAME` with your tracker username.

The process checks periodically while it remains running.
Stop it with Ctrl+C. Closing the terminal or shutting down the
computer stops local background syncing.

## Tests

Run the tests included in this checkout:

```bat
python manage.py test applications
```

Review the output for the current test count and results.

## Security and local data

Never commit:

- `.env` or real credentials
- OAuth token files
- The local SQLite database or its backups
- Personal email previews or exported application records

Password-reset emails currently use the local console configuration.
For local testing, retrieve the reset link from the server terminal.
Public deployment requires configuring an actual email-delivery service.

## Project status

This is a local-development project being prepared for deployment.

Remaining launch work includes:

- Production hosting and HTTPS configuration
- Production email delivery and database backups
- Background-worker deployment
- Hosting-specific client-IP handling for rate limits
- Google OAuth production requirements
- Review of Gmail-derived data retention after disconnect or revocation

The local Django development server is not the production hosting setup.