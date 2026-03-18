from pathlib import Path

from django import forms

from .models import AccountRetentionPolicyOverride, AccountRetentionPolicyTemplate, HeldPosition, HoldingTransaction, ImportedBrokerSnapshot, InstrumentSelection, UserRiskProfile


class WatchlistCreateForm(forms.Form):
    name = forms.CharField(max_length=100)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.update({
            "class": "form-control",
            "placeholder": "Growth names, Earnings radar, Crypto focus...",
        })



class HoldingImportForm(forms.Form):
    account_label = forms.CharField(max_length=80, required=False, help_text="Optional account label to apply to every imported holding in this file, such as Robinhood Taxable or Fidelity IRA.")
    csv_file = forms.FileField(help_text="CSV with at least symbol, quantity, and average_entry_price columns.")
    mark_missing_positions_for_review = forms.BooleanField(
        required=False,
        initial=True,
        help_text="Flag currently open holdings that are missing from this import so they show up in a review queue instead of silently drifting out of sync.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account_label"].widget.attrs["class"] = "form-control"
        self.fields["csv_file"].widget.attrs["class"] = "form-control"
        self.fields["mark_missing_positions_for_review"].widget.attrs["class"] = "form-check-input"


class SavedFilterPresetForm(forms.Form):
    name = forms.CharField(max_length=100)
    pin_to_dashboard = forms.BooleanField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.update({
            "class": "form-control form-control-sm",
            "placeholder": "Save current filters as...",
        })
        self.fields["pin_to_dashboard"].widget.attrs.update({
            "class": "form-check-input",
        })


class HeldPositionForm(forms.ModelForm):
    class Meta:
        model = HeldPosition
        fields = [
            "instrument",
            "quantity",
            "average_entry_price",
            "opened_at",
            "account_label",
            "stop_price",
            "target_price",
            "thesis",
            "notes",
        ]
        widgets = {
            "opened_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "thesis": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "account_label": "Optional broker/account label so this tracked holding belongs to a specific account instead of the blended book.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (css + " form-control").strip()
        self.fields["instrument"].widget.attrs["class"] = "form-select"


class BrokerPositionImportForm(forms.Form):
    source_label = forms.CharField(max_length=80, required=False, initial="Broker CSV", help_text="Label this reconciliation run so later reviews are easy to identify.")
    account_label = forms.CharField(max_length=80, required=False, help_text="Optional broker account label such as Robinhood Taxable or Fidelity IRA.")
    csv_file = forms.FileField(help_text="CSV with at least symbol and quantity columns. Optional columns: market_price, market_value, average_entry_price.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source_label"].widget.attrs["class"] = "form-control"
        self.fields["account_label"].widget.attrs["class"] = "form-control"
        self.fields["csv_file"].widget.attrs["class"] = "form-control"


class BrokerPositionResolutionForm(forms.Form):
    NOTE_PRESET_CHOICES = (
        ("", "Custom note"),
        ("BROKER_TIMING", "Broker timing or settlement difference"),
        ("INTENTIONAL_TRACKING", "Intentional tracking difference"),
        ("EXPORT_SCOPE", "Broker export scope/filter difference"),
        ("CORPORATE_ACTION", "Corporate action or symbol change"),
        ("FOLLOW_UP_LATER", "Needs later follow-up"),
    )

    NOTE_PRESET_TEXT = {
        "BROKER_TIMING": "Broker timing or settlement difference; review again after the next broker refresh.",
        "INTENTIONAL_TRACKING": "Intentional tracking difference; broker and app are not expected to match exactly for this symbol.",
        "EXPORT_SCOPE": "Broker export scope/filter difference; this file likely omitted positions or used a restricted account view.",
        "CORPORATE_ACTION": "Corporate action or symbol-change context may explain this mismatch; confirm lots and history before adjusting holdings.",
        "FOLLOW_UP_LATER": "Needs later follow-up with a fresh broker export before changing tracked holdings.",
    }

    action = forms.ChoiceField(choices=(
        ("REVIEWED_OK", "Reviewed and acceptable"),
        ("CLOSE_TRACKED", "Close tracked holding"),
        ("ADD_TRACKED", "Add tracked holding"),
        ("QUANTITY_ACCEPTED", "Quantity difference accepted"),
        ("FOLLOW_UP", "Manual follow-up"),
    ))
    note_preset = forms.ChoiceField(required=False, choices=NOTE_PRESET_CHOICES)
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["action"].widget.attrs["class"] = "form-select"
        self.fields["note_preset"].widget.attrs["class"] = "form-select"
        self.fields["note"].widget.attrs.update({"class": "form-control", "placeholder": "Why this mismatch is acceptable or what you plan to do next."})

    def clean(self):
        cleaned = super().clean()
        preset = (cleaned.get("note_preset") or "").strip()
        note = (cleaned.get("note") or "").strip()
        preset_text = self.NOTE_PRESET_TEXT.get(preset, "")
        if preset_text and note:
            cleaned["combined_note"] = f"{preset_text} {note}".strip()
        else:
            cleaned["combined_note"] = preset_text or note
        return cleaned


class BrokerSnapshotForm(forms.ModelForm):
    class Meta:
        model = ImportedBrokerSnapshot
        fields = ["source_label", "account_label", "as_of", "account_equity", "cash_balance", "notes"]
        widgets = {
            "as_of": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "account_equity": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "cash_balance": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "account_label": "Optional broker/account label so this tracked holding belongs to a specific account instead of the blended book.",
        }
        help_texts = {
            "source_label": "Example: Robinhood export, Fidelity account summary, Manual broker snapshot.",
            "account_label": "Optional broker account label such as Robinhood Taxable, Fidelity IRA, or Joint Brokerage.",
            "account_equity": "Total account equity shown by the broker/export at that moment.",
            "cash_balance": "Settled or displayed cash balance from that same snapshot.",
            "notes": "Optional context such as filtered export, pending settlements, or why drift might exist.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (css + " form-control").strip()







STOP_POLICY_EVIDENCE_ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".csv", ".txt"}
STOP_POLICY_EVIDENCE_MAX_BYTES = 5 * 1024 * 1024

class StopPolicyResolutionNoteForm(forms.Form):
    tx_id = forms.IntegerField(widget=forms.HiddenInput())
    reason_code = forms.ChoiceField(
        required=False,
        choices=(("", "No reason selected"),) + tuple(HoldingTransaction.StopPolicyReasonCode.choices),
    )
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    evidence_type = forms.ChoiceField(
        required=False,
        choices=(("", "No execution evidence saved"),) + tuple(HoldingTransaction.ExecutionEvidenceType.choices),
    )
    evidence_quality = forms.ChoiceField(
        required=False,
        choices=(("", "No evidence quality selected"),) + tuple(HoldingTransaction.ExecutionEvidenceQuality.choices),
    )
    evidence_reference = forms.CharField(required=False, max_length=120)
    evidence_note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    evidence_attachment = forms.FileField(required=False)
    broker_confirmation_snapshot_id = forms.IntegerField(required=False)
    broker_confirmation_run_id = forms.IntegerField(required=False)
    broker_confirmation_resolution_id = forms.IntegerField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason_code"].widget.attrs.update({"class": "form-select form-select-sm"})
        self.fields["note"].widget.attrs.update({
            "class": "form-control form-control-sm",
            "placeholder": "Why this stop was late, deferred, or intentionally handled differently.",
        })
        self.fields["evidence_type"].widget.attrs.update({"class": "form-select form-select-sm"})
        self.fields["evidence_quality"].widget.attrs.update({"class": "form-select form-select-sm"})
        self.fields["evidence_reference"].widget.attrs.update({
            "class": "form-control form-control-sm",
            "placeholder": "Order ID, confirmation number, or import reference",
        })
        self.fields["evidence_note"].widget.attrs.update({
            "class": "form-control form-control-sm",
            "placeholder": "What evidence you saw that supports this waiting-for-confirmation exception.",
        })
        self.fields["evidence_attachment"].widget.attrs.update({
            "class": "form-control form-control-sm",
            "accept": ".pdf,.png,.jpg,.jpeg,.webp,.csv,.txt",
        })
        for field_name in ("broker_confirmation_snapshot_id", "broker_confirmation_run_id", "broker_confirmation_resolution_id"):
            self.fields[field_name].widget = forms.HiddenInput()

    def clean_evidence_attachment(self):
        attachment = self.cleaned_data.get("evidence_attachment")
        if not attachment:
            return attachment
        suffix = Path(attachment.name or "").suffix.lower()
        if suffix not in STOP_POLICY_EVIDENCE_ALLOWED_EXTENSIONS:
            raise forms.ValidationError("Allowed evidence files: PDF, PNG, JPG, WEBP, CSV, and TXT.")
        if getattr(attachment, "size", 0) > STOP_POLICY_EVIDENCE_MAX_BYTES:
            raise forms.ValidationError("Evidence attachment must be 5 MB or smaller.")
        return attachment

class UserRiskProfileForm(forms.ModelForm):
    class Meta:
        model = UserRiskProfile
        fields = [
            "account_equity",
            "risk_per_trade_pct",
            "max_position_weight_pct",
            "max_sector_weight_pct",
            "concentration_warning_buffer_pct",
            "max_high_correlation_positions",
            "high_correlation_threshold",
            "correlation_lookback_bars",
            "max_net_exposure_pct",
            "net_exposure_warning_buffer_pct",
            "require_stop_for_open_positions",
            "max_stop_loss_pct",
            "stop_warning_buffer_pct",
            "drawdown_review_pct",
            "drawdown_urgent_pct",
            "stop_policy_target_hours",
            "evidence_retention_default_days",
            "evidence_retention_verified_days",
            "evidence_retention_strong_days",
            "evidence_retention_weak_days",
            "evidence_retention_placeholder_days",
            "evidence_retention_confirmation_days",
            "evidence_retention_import_match_days",
        ]
        widgets = {
            "account_equity": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "risk_per_trade_pct": forms.NumberInput(attrs={"step": "0.0001", "min": "0"}),
            "max_position_weight_pct": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "max_sector_weight_pct": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "concentration_warning_buffer_pct": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "max_high_correlation_positions": forms.NumberInput(attrs={"step": "1", "min": "1"}),
            "high_correlation_threshold": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "1"}),
            "correlation_lookback_bars": forms.NumberInput(attrs={"step": "1", "min": "10"}),
            "max_net_exposure_pct": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100"}),
            "net_exposure_warning_buffer_pct": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100"}),
            "require_stop_for_open_positions": forms.CheckboxInput(),
            "max_stop_loss_pct": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100"}),
            "stop_warning_buffer_pct": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100"}),
            "drawdown_review_pct": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100"}),
            "drawdown_urgent_pct": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100"}),
            "stop_policy_target_hours": forms.NumberInput(attrs={"step": "1", "min": "1", "max": "168"}),
            "evidence_retention_default_days": forms.NumberInput(attrs={"step": "1", "min": "1", "max": "3650"}),
            "evidence_retention_verified_days": forms.NumberInput(attrs={"step": "1", "min": "1", "max": "3650"}),
            "evidence_retention_strong_days": forms.NumberInput(attrs={"step": "1", "min": "1", "max": "3650"}),
            "evidence_retention_weak_days": forms.NumberInput(attrs={"step": "1", "min": "1", "max": "3650"}),
            "evidence_retention_placeholder_days": forms.NumberInput(attrs={"step": "1", "min": "1", "max": "3650"}),
            "evidence_retention_confirmation_days": forms.NumberInput(attrs={"step": "1", "min": "1", "max": "3650"}),
            "evidence_retention_import_match_days": forms.NumberInput(attrs={"step": "1", "min": "1", "max": "3650"}),
        }
        help_texts = {
            "account_equity": "Used for sizing suggestions and exposure tracking.",
            "risk_per_trade_pct": "Store as a decimal: 0.0100 = 1% risk per trade.",
            "max_position_weight_pct": "Soft cap for any single holding as a percentage of account equity.",
            "max_sector_weight_pct": "Soft cap for any single sector/theme as a percentage of account equity.",
            "concentration_warning_buffer_pct": "How close to a cap counts as near-limit posture.",
            "max_high_correlation_positions": "How many currently held names a new trade may be strongly correlated with before it becomes an over-limit cluster.",
            "high_correlation_threshold": "Store as a decimal: 0.80 means returns moving together at 0.80 correlation or higher.",
            "correlation_lookback_bars": "How many daily bars to use when checking recent return correlation against current holdings.",
            "max_net_exposure_pct": "Soft cap for how much net long exposure the account should carry as a percentage of account equity. In the current long-only holdings workflow, this behaves like a portfolio deployment cap.",
            "net_exposure_warning_buffer_pct": "How close to the net exposure cap counts as near-limit posture.",
            "require_stop_for_open_positions": "If enabled, open held positions without a stop become an over-limit risk guardrail that should be fixed explicitly.",
            "max_stop_loss_pct": "Maximum allowed distance from entry to stop before the holding is treated as over-limit and needs a tighter exit plan.",
            "stop_warning_buffer_pct": "How close current price can get to the stop before the holding enters a near-stop warning posture.",
            "drawdown_review_pct": "Open positions at or worse than this loss percentage enter a review-now guardrail posture.",
            "drawdown_urgent_pct": "Open positions at or worse than this loss percentage enter an urgent guardrail posture even if no stop was hit yet.",
            "stop_policy_target_hours": "How many hours Mike should allow between a new open/add event and recording or tightening the stop before the event counts as late stop-policy follow-through.",
            "evidence_retention_default_days": "Base retention window for any attachment-backed execution evidence when no stronger preset applies.",
            "evidence_retention_verified_days": "Retention window to use when evidence quality is marked Verified.",
            "evidence_retention_strong_days": "Retention window to use when evidence quality is marked Strong.",
            "evidence_retention_weak_days": "Retention window to use when evidence quality is marked Weak.",
            "evidence_retention_placeholder_days": "Retention window to use when evidence quality is marked Placeholder / unverified.",
            "evidence_retention_confirmation_days": "Retention window to use for broker confirmations and order-reference style execution evidence.",
            "evidence_retention_import_match_days": "Retention window to use for later broker/import match evidence.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (css + " form-control").strip()








class AccountRetentionPolicyOverrideCloneForm(forms.Form):
    source_override = forms.ModelChoiceField(queryset=AccountRetentionPolicyOverride.objects.none(), empty_label=None)
    target_account_label = forms.CharField(max_length=80)
    overwrite_existing = forms.BooleanField(required=False)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user is not None:
            self.fields["source_override"].queryset = AccountRetentionPolicyOverride.objects.filter(user=user).order_by("account_label")
        self.fields["source_override"].label = "Copy from account"
        self.fields["target_account_label"].label = "New account label"
        self.fields["source_override"].help_text = "Choose an existing account override to copy."
        self.fields["target_account_label"].help_text = "Target account label that should receive the copied retention policy."
        self.fields["overwrite_existing"].help_text = "Replace an existing override on the target account instead of blocking the copy."
        self.fields["source_override"].widget.attrs["class"] = "form-select"
        self.fields["target_account_label"].widget.attrs.update({"class": "form-control", "placeholder": "Robinhood IRA, Fidelity Joint..."})
        self.fields["overwrite_existing"].widget.attrs["class"] = "form-check-input"

    def clean_target_account_label(self):
        value = (self.cleaned_data.get("target_account_label") or "").strip()
        if not value:
            raise forms.ValidationError("Enter the target account label.")
        source = self.cleaned_data.get("source_override")
        if source and value.casefold() == source.account_label.strip().casefold():
            raise forms.ValidationError("Choose a different target account label than the source override.")
        return value

    def clean(self):
        cleaned = super().clean()
        user = getattr(self, "user", None)
        target = (cleaned.get("target_account_label") or "").strip()
        overwrite = bool(cleaned.get("overwrite_existing"))
        if user is not None and target and not overwrite:
            exists = AccountRetentionPolicyOverride.objects.filter(user=user, account_label__iexact=target).exists()
            if exists:
                self.add_error("target_account_label", "An override already exists for that account. Check overwrite to replace it.")
        return cleaned


class AccountRetentionPolicyTemplateForm(forms.ModelForm):
    class Meta:
        model = AccountRetentionPolicyTemplate
        fields = [
            "family_label",
            "template_name",
            "notes",
            "evidence_retention_default_days",
            "evidence_retention_verified_days",
            "evidence_retention_strong_days",
            "evidence_retention_weak_days",
            "evidence_retention_placeholder_days",
            "evidence_retention_confirmation_days",
            "evidence_retention_import_match_days",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2}),
            "evidence_retention_default_days": forms.NumberInput(attrs={"step": "1", "min": "1", "max": "3650"}),
            "evidence_retention_verified_days": forms.NumberInput(attrs={"step": "1", "min": "1", "max": "3650"}),
            "evidence_retention_strong_days": forms.NumberInput(attrs={"step": "1", "min": "1", "max": "3650"}),
            "evidence_retention_weak_days": forms.NumberInput(attrs={"step": "1", "min": "1", "max": "3650"}),
            "evidence_retention_placeholder_days": forms.NumberInput(attrs={"step": "1", "min": "1", "max": "3650"}),
            "evidence_retention_confirmation_days": forms.NumberInput(attrs={"step": "1", "min": "1", "max": "3650"}),
            "evidence_retention_import_match_days": forms.NumberInput(attrs={"step": "1", "min": "1", "max": "3650"}),
        }
        help_texts = {
            "family_label": "Optional family or bucket label such as Retirement, Active, or Long-term.",
            "template_name": "Reusable template name for a shared account-retention policy seed.",
            "notes": "Optional internal note describing when this template should be used.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (css + " form-control").strip()


class AccountRetentionPolicyTemplateApplyForm(forms.Form):
    template = forms.ModelChoiceField(queryset=AccountRetentionPolicyTemplate.objects.none(), empty_label=None)
    account_labels = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), help_text="Comma or newline separated account labels that should receive this template.")
    overwrite_existing = forms.BooleanField(required=False, help_text="Allow the template to replace an existing override on any listed account.")

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["template"].widget.attrs["class"] = "form-select"
        self.fields["account_labels"].widget.attrs["class"] = "form-control"
        self.fields["overwrite_existing"].widget.attrs["class"] = "form-check-input"
        if user is not None:
            self.fields["template"].queryset = AccountRetentionPolicyTemplate.objects.filter(user=user).order_by("family_label", "template_name")

    def clean_account_labels(self):
        raw = self.cleaned_data.get("account_labels") or ""
        parts = []
        seen = set()
        normalized_raw = raw.replace("\r", "\n")
        for chunk in normalized_raw.replace(",", "\n").split("\n"):
            value = chunk.strip()
            if not value:
                continue
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            parts.append(value)
        if not parts:
            raise forms.ValidationError("Enter at least one account label to receive this template.")
        if any(not value for value in parts):
            raise forms.ValidationError("Account labels cannot be blank.")
        return parts

    def clean(self):
        cleaned = super().clean()
        user = self.user
        labels = cleaned.get("account_labels") or []
        overwrite = bool(cleaned.get("overwrite_existing"))
        if user is None or not labels or overwrite:
            return cleaned
        existing = {label.casefold() for label in AccountRetentionPolicyOverride.objects.filter(user=user).values_list("account_label", flat=True)}
        conflicts = [label for label in labels if label.casefold() in existing]
        if conflicts:
            raise forms.ValidationError(f"Existing overrides already cover: {', '.join(conflicts)}. Check overwrite to replace them.")
        return cleaned


class AccountRetentionPolicyOverrideForm(forms.ModelForm):
    class Meta:
        model = AccountRetentionPolicyOverride
        fields = [
            "account_label",
            "evidence_retention_default_days",
            "evidence_retention_verified_days",
            "evidence_retention_strong_days",
            "evidence_retention_weak_days",
            "evidence_retention_placeholder_days",
            "evidence_retention_confirmation_days",
            "evidence_retention_import_match_days",
        ]
        widgets = {
            "account_label": forms.TextInput(attrs={"placeholder": "Robinhood Taxable, Fidelity IRA..."}),
            "evidence_retention_default_days": forms.NumberInput(attrs={"step": "1", "min": "1", "max": "3650"}),
            "evidence_retention_verified_days": forms.NumberInput(attrs={"step": "1", "min": "1", "max": "3650"}),
            "evidence_retention_strong_days": forms.NumberInput(attrs={"step": "1", "min": "1", "max": "3650"}),
            "evidence_retention_weak_days": forms.NumberInput(attrs={"step": "1", "min": "1", "max": "3650"}),
            "evidence_retention_placeholder_days": forms.NumberInput(attrs={"step": "1", "min": "1", "max": "3650"}),
            "evidence_retention_confirmation_days": forms.NumberInput(attrs={"step": "1", "min": "1", "max": "3650"}),
            "evidence_retention_import_match_days": forms.NumberInput(attrs={"step": "1", "min": "1", "max": "3650"}),
        }
        help_texts = {
            "account_label": "Account label this override should apply to. Use the same label that holdings, snapshots, and reconciliation runs use.",
            "evidence_retention_default_days": "Optional override for generic attachment-backed evidence in this account.",
            "evidence_retention_verified_days": "Optional override when evidence quality is Verified.",
            "evidence_retention_strong_days": "Optional override when evidence quality is Strong.",
            "evidence_retention_weak_days": "Optional override when evidence quality is Weak.",
            "evidence_retention_placeholder_days": "Optional override when evidence quality is Placeholder / unverified.",
            "evidence_retention_confirmation_days": "Optional override for broker confirmations and order-reference evidence in this account.",
            "evidence_retention_import_match_days": "Optional override for later broker/import match evidence in this account.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (css + " form-control").strip()

    def clean_account_label(self):
        return (self.cleaned_data.get("account_label") or "").strip()

class AccountTransferForm(forms.Form):
    account_label = forms.CharField(
        max_length=80,
        required=False,
        help_text="Leave blank to move this holding back into the blended/unlabeled book.",
    )
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account_label"].widget.attrs.update({
            "class": "form-control",
            "placeholder": "Robinhood Taxable, Fidelity IRA, Joint Brokerage...",
        })
        self.fields["note"].widget.attrs.update({
            "class": "form-control",
            "placeholder": "Why this holding is being moved or relabeled.",
        })


class AddSharesForm(forms.Form):
    quantity = forms.DecimalField(max_digits=20, decimal_places=8, min_value=0)
    buy_price = forms.DecimalField(max_digits=20, decimal_places=8, min_value=0, required=False)
    stop_price = forms.DecimalField(max_digits=20, decimal_places=8, min_value=0, required=False)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["quantity"].widget.attrs.update({"class": "form-control", "step": "0.00000001", "min": "0"})
        self.fields["buy_price"].widget.attrs.update({"class": "form-control", "step": "0.00000001", "min": "0", "placeholder": "Defaults to last price"})
        self.fields["stop_price"].widget.attrs.update({"class": "form-control", "step": "0.00000001", "min": "0", "placeholder": "Optional: add or tighten stop with this buy"})
        self.fields["notes"].widget.attrs.update({"class": "form-control"})


class PartialSellForm(forms.Form):
    quantity = forms.DecimalField(max_digits=20, decimal_places=8, min_value=0)
    sale_price = forms.DecimalField(max_digits=20, decimal_places=8, min_value=0, required=False)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["quantity"].widget.attrs.update({"class": "form-control", "step": "0.00000001", "min": "0"})
        self.fields["sale_price"].widget.attrs.update({"class": "form-control", "step": "0.00000001", "min": "0", "placeholder": "Defaults to last price"})
        self.fields["notes"].widget.attrs.update({"class": "form-control"})


class ReconciliationResolveForm(forms.Form):
    close_price = forms.DecimalField(max_digits=20, decimal_places=8, min_value=0, required=False)
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["close_price"].widget.attrs.update({"class": "form-control", "step": "0.00000001", "min": "0", "placeholder": "Defaults to last price"})
        self.fields["note"].widget.attrs.update({"class": "form-control", "placeholder": "Why this import mismatch is okay, or why you are closing it."})




class WatchlistSelectionForm(forms.ModelForm):
    class Meta:
        model = InstrumentSelection
        fields = ["priority", "sector", "note"]
        widgets = {
            "priority": forms.Select(),
            "sector": forms.TextInput(attrs={"placeholder": "Semis, AI, Crypto, Dividend..."}),
            "note": forms.TextInput(attrs={"placeholder": "Why this symbol matters right now"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "form-check-input"
                continue
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (css + " form-control").strip()
        self.fields["priority"].widget.attrs["class"] = "form-select"

class WatchlistSymbolForm(forms.Form):
    symbol = forms.CharField(max_length=32)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["symbol"].widget.attrs.update({
            "class": "form-control",
            "placeholder": "AAPL or BTCUSD",
            "autocapitalize": "characters",
        })


class WatchlistImportForm(forms.Form):
    csv_file = forms.FileField(required=False, help_text="CSV with a symbol/ticker column, or upload a one-column symbol file.")
    symbols_text = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 8, "placeholder": "AAPL\nMSFT\nNVDA"}),
        help_text="Optional: paste symbols one per line or comma-separated.",
    )
    replace_missing = forms.BooleanField(
        required=False,
        initial=False,
        help_text="If checked, active watchlist symbols not present in this import will be deactivated after you confirm.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["csv_file"].widget.attrs["class"] = "form-control"
        self.fields["symbols_text"].widget.attrs["class"] = "form-control"
        self.fields["replace_missing"].widget.attrs["class"] = "form-check-input"

    def clean(self):
        cleaned = super().clean()
        csv_file = cleaned.get("csv_file")
        symbols_text = (cleaned.get("symbols_text") or "").strip()
        if not csv_file and not symbols_text:
            raise forms.ValidationError("Upload a CSV or paste at least one symbol.")
        return cleaned
