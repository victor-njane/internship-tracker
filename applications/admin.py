from django.contrib import admin

from .models import Application, ContactEvent


class ContactEventInline(admin.TabularInline):
    model = ContactEvent
    extra = 1


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = (
        "company",
        "position",
        "date_applied",
        "status",
        "next_action_date",
    )
    list_filter = ("status", "date_applied")
    search_fields = ("company", "position", "notes")
    inlines = [ContactEventInline]
