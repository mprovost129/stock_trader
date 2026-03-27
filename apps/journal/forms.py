from django import forms

from .models import JournalEntry

_SELECT_ATTRS = {"class": "form-select form-select-sm"}
_INPUT_ATTRS = {"class": "form-control form-control-sm"}


class JournalEntryForm(forms.ModelForm):
    class Meta:
        model = JournalEntry
        fields = ["decision", "notes", "outcome", "realized_r", "tags"]
        widgets = {
            "decision": forms.Select(attrs=_SELECT_ATTRS),
            "outcome": forms.Select(attrs=_SELECT_ATTRS),
            "realized_r": forms.NumberInput(attrs={**_INPUT_ATTRS, "step": "0.01", "placeholder": "e.g. 1.5"}),
            "tags": forms.TextInput(attrs={**_INPUT_ATTRS, "placeholder": "optional tags"}),
            "notes": forms.Textarea(attrs={**_INPUT_ATTRS, "rows": 2, "placeholder": "Why you took or skipped this signal…"}),
        }
