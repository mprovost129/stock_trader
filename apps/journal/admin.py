from django.contrib import admin

from .models import JournalEntry


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ("decided_at", "user", "decision", "outcome", "signal")
    list_filter = ("decision", "outcome")
    search_fields = ("user__username", "signal__instrument__symbol", "notes", "tags")
    ordering = ("-decided_at",)
