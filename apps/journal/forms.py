from django import forms

from .models import JournalEntry


class JournalEntryForm(forms.ModelForm):
    class Meta:
        model = JournalEntry
        fields = ["decision", "notes", "outcome", "realized_r", "tags"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
        }
