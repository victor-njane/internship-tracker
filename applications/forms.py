from django import forms

from .models import Application, ContactEvent
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import (
    UserCreationForm,
    AuthenticationForm,
    UsernameField,
)
from django.contrib.auth.signals import user_login_failed


class ApplicationForm(forms.ModelForm):
    class Meta:
        model = Application

        fields = [
            "company",
            "position",
            "application_url",
            "source",
            "date_applied",
            "application_deadline",
            "status",
            "next_action",
            "next_action_date",
            "notes",
        ]

        widgets = {
            "date_applied": forms.DateInput(
                format="%Y-%m-%d",
                attrs={"type": "date"},
            ),
            "next_action_date": forms.DateInput(
                format="%Y-%m-%d",
                attrs={"type": "date"},
            ),
            "notes": forms.Textarea(attrs={"rows": 4}),
            "application_deadline": forms.DateInput(
                format="%Y-%m-%d",
                attrs={"type": "date"},
            ),
        }
        help_texts = {
            "application_deadline": (
                "Optional: the employer's application closing date. "
                "Separate from your next action; check the posting "
                "for its exact closing time."
            ),
        }

        labels = {
            "application_url": "Job or application URL",
            "source": "Where did you find this internship?",
        }
class ContactEventForm(forms.ModelForm):
    class Meta:
        model = ContactEvent
        fields = ["date", "event_type", "summary"]

        widgets = {
            "date": forms.DateInput(
                format="%Y-%m-%d",
                attrs={"type": "date"},
            ),
            "summary": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": (
                        "Who contacted you, what did they say, "
                        "and what happens next?"
                    ),
                }
            ),
        }

        labels = {
            "date": "Communication date",
            "event_type": "Communication type",
            "summary": "Details",
        }
class SignUpForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ("username", "email")

class UsernameOrEmailAuthenticationForm(AuthenticationForm):
    username = UsernameField(
        label="Username or email",
        max_length=254,
        widget=forms.TextInput(attrs={
            "autofocus": True,
            "autocomplete": "username",
        }),
        help_text=(
            "If your email is shared by multiple accounts, "
            "use your username."
        ),
    )

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": (
            "Unable to log in with those details. Check your password. "
            "If you entered an email address, try your username instead."
        ),
    }

    def clean(self):
        identifier = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")

        if identifier and password:
            User = get_user_model()

            # Exact usernames take priority, even if they look like emails.
            exact_username = User.objects.filter(
                username=identifier,
            ).exists()

            if not exact_username:
                matches = list(
                    User.objects.filter(email__iexact=identifier)
                    .values_list("username", flat=True)[:2]
                )

                if len(matches) != 1:
                    # Perform password hashing even for unsuccessful lookups.
                    User().set_password(password)

                    user_login_failed.send(
                        sender=__name__,
                        credentials={"username": identifier},
                        request=self.request,
                    )

                    raise self.get_invalid_login_error()

                self.cleaned_data["username"] = matches[0]

        # Keep Django's normal password and active-account checks.
        return super().clean()