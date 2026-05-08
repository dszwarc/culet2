from django import forms
from django.contrib.auth.forms import AuthenticationForm, UsernameField
from django.forms import inlineformset_factory, formset_factory
from django.utils import timezone
from decimal import Decimal
from django.core.exceptions import ValidationError

from .models import (
    JobWeight,
    JobMetal,
    JobMetalLot,
    JobStone,
    Job,
    Style,
    StyleMetal,
    StyleStone,
    MetalLot,
    MetalReceipt,
    MetalReceiptLine,
)


class DateInput(forms.DateInput):
    input_type = "date"


# -------------------------------------------------
# Shared widget helpers
# -------------------------------------------------

BASE_INPUT_CLASS = "form-control job-input"
TABLE_INPUT_CLASS = "job-table-input"


def text_widget(placeholder="", extra_classes=""):
    return forms.TextInput(
        attrs={
            "class": f"{BASE_INPUT_CLASS} {extra_classes}".strip(),
            "placeholder": placeholder,
        }
    )


def textarea_widget(placeholder="", rows=4, extra_classes=""):
    return forms.Textarea(
        attrs={
            "class": f"{BASE_INPUT_CLASS} {extra_classes}".strip(),
            "placeholder": placeholder,
            "rows": rows,
        }
    )


def select_widget(extra_classes=""):
    return forms.Select(
        attrs={
            "class": f"{BASE_INPUT_CLASS} {extra_classes}".strip(),
        }
    )


def number_widget(placeholder="", step=None, min_value=None, extra_classes=""):
    attrs = {
        "class": f"{BASE_INPUT_CLASS} {extra_classes}".strip(),
        "placeholder": placeholder,
    }
    if step is not None:
        attrs["step"] = step
    if min_value is not None:
        attrs["min"] = min_value
    return forms.NumberInput(attrs=attrs)


def date_widget(extra_classes=""):
    return forms.DateInput(
        attrs={
            "class": f"{BASE_INPUT_CLASS} {extra_classes}".strip(),
            "type": "date",
        }
    )


def table_select_widget():
    return forms.Select(attrs={"class": TABLE_INPUT_CLASS})


def table_text_widget(placeholder=""):
    return forms.TextInput(
        attrs={
            "class": TABLE_INPUT_CLASS,
            "placeholder": placeholder,
        }
    )


def table_number_widget(step=None, min_value=None, placeholder=""):
    attrs = {
        "class": TABLE_INPUT_CLASS,
        "placeholder": placeholder,
    }
    if step is not None:
        attrs["step"] = step
    if min_value is not None:
        attrs["min"] = min_value
    return forms.NumberInput(attrs=attrs)


# -------------------------------------------------
# Forms
# -------------------------------------------------

class JobWeightForm(forms.ModelForm):
    class Meta:
        model = JobWeight
        fields = ["step","weight", "sprue_weight", "dust_weight"]
        widgets = {
            "weight": forms.NumberInput(attrs={"step": "0.001", "min": "0"}),
            "sprue_weight": forms.NumberInput(attrs={"step": "0.001", "min": "0"}),
            "dust_weight": forms.NumberInput(attrs={"step": "0.001", "min": "0"}),
        }

    def clean(self):
        cleaned_data = super().clean()

        weight = cleaned_data.get("weight") or Decimal("0")
        sprue_weight = cleaned_data.get("sprue_weight") or Decimal("0")
        dust_weight = cleaned_data.get("dust_weight") or Decimal("0")

        for field_name, value in {
            "weight": weight,
            "sprue_weight": sprue_weight,
            "dust_weight": dust_weight,
        }.items():
            if value < 0:
                self.add_error(field_name, "Weight values cannot be negative.")

        if (weight + sprue_weight + dust_weight) <= 0:
            raise ValidationError("At least one weight value must be greater than 0.")

        return cleaned_data

class JobWeightLookupForm(forms.Form):
    job_num = forms.IntegerField(required=False, label="Job Number")
    customer_ref_num = forms.IntegerField(required=False, label="Customer Reference Number")

    def clean(self):
        cleaned_data = super().clean()
        job_num = cleaned_data.get("job_num")
        customer_ref_num = cleaned_data.get("customer_ref_num")

        if not job_num and not customer_ref_num:
            raise ValidationError("Enter either a job number or a customer reference number.")

        return cleaned_data

class JobForm(forms.ModelForm):
    class Meta:
        model = Job
        fields = [
            "name",
            "customer",
            "customer_ref_num",
            "style",
            "due",
            "assigned_to",
            "location",
            "notes",
        ]
        widgets = {
            "name": text_widget("Job name", "form-control-lg"),
            "customer": select_widget(),
            "customer_ref_num": number_widget("Customer reference #", min_value="0"),
            "style": select_widget(),
            "due": date_widget(),
            "assigned_to": select_widget(),
            "location": select_widget(),
            "notes": textarea_widget("Add notes for the shop...", rows=4),
        }
        labels = {
            "customer_ref_num": "Customer Ref #",
            "assigned_to": "Assigned To",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["name"].required = False
        self.fields["customer"].required = False
        self.fields["customer_ref_num"].required = False
        self.fields["assigned_to"].required = False
        self.fields["location"].required = False
        self.fields["notes"].required = False

        if "style" in self.fields:
            self.fields["style"].empty_label = "Select a style"
        if "customer" in self.fields:
            self.fields["customer"].empty_label = "Select a customer"
        if "assigned_to" in self.fields:
            self.fields["assigned_to"].empty_label = "Unassigned"
        if "location" in self.fields:
            self.fields["location"].empty_label = "No current location"


class JobMetalForm(forms.ModelForm):
    class Meta:
        model = JobMetal
        fields = ["part", "qty_req", "weight_req", "metal_type"]
        widgets = {
            "part": table_select_widget(),
            "qty_req": table_number_widget(min_value="0", placeholder="Qty"),
            "weight_req": table_number_widget(step="0.001", min_value="0", placeholder="Weight"),
            "metal_type": table_select_widget(),
        }
        labels = {
            "qty_req": "Qty Required",
            "weight_req": "Weight Required",
        }


class JobStoneForm(forms.ModelForm):
    class Meta:
        model = JobStone
        fields = ["stone_type", "stone_shape", "stone_size", "qty_req"]
        widgets = {
            "stone_type": table_select_widget(),
            "stone_shape": table_select_widget(),
            "stone_size": table_text_widget("e.g. 2.5mm"),
            "qty_req": table_number_widget(min_value="0", placeholder="Qty"),
        }
        labels = {
            "qty_req": "Qty Required",
        }


class JobMetalLotForm(forms.ModelForm):
    class Meta:
        model = JobMetalLot
        fields = ["metal_lot", "qty_used", "weight_used"]
        widgets = {
            "metal_lot": select_widget(),
            "qty_used": number_widget("Qty used", min_value="0"),
            "weight_used": number_widget("Weight used", step="0.001", min_value="0"),
        }
        labels = {
            "qty_used": "Qty Used",
            "weight_used": "Weight Used",
        }

    def __init__(self, *args, **kwargs):
        job_metal = kwargs.pop("job_metal", None)
        super().__init__(*args, **kwargs)

        if job_metal:
            self.fields["metal_lot"].queryset = MetalLot.objects.filter(
                part=job_metal.part,
                qty_on_hand__gt=0,
            ).order_by("vendor_lot__lot_num")

        if "metal_lot" in self.fields:
            self.fields["metal_lot"].empty_label = "Select a metal lot"


class JobUpdateForm(forms.ModelForm):
    class Meta:
        model = Job
        fields = ("name", "customer", "style", "due", "notes")
        widgets = {
            "name": text_widget("Job name"),
            "customer": select_widget(),
            "style": select_widget(),
            "due": date_widget(),
            "notes": textarea_widget("Add notes...", rows=4),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if "customer" in self.fields:
            self.fields["customer"].empty_label = "Select a customer"
        if "style" in self.fields:
            self.fields["style"].empty_label = "Select a style"


class StyleForm(forms.ModelForm):
    class Meta:
        model = Style
        fields = ("name", "customer", "stamp", "description")
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "form-control form-control-lg job-input",
                    "placeholder": "Style name",
                }
            ),
            "customer": forms.Select(
                attrs={
                    "class": "form-control job-input",
                }
            ),
            "stamp": forms.TextInput(
                attrs={
                    "class": "form-control job-input",
                    "placeholder": "Stamp / hallmark",
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "form-control job-input",
                    "rows": 4,
                    "placeholder": "Style description...",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].required = False
        self.fields["customer"].empty_label = "Select a customer"


class StyleMetalForm(forms.ModelForm):
    class Meta:
        model = StyleMetal
        fields = ["part", "qty_req", "weight", "metal_type"]
        widgets = {
            "part": forms.Select(attrs={"class": "job-table-input"}),
            "qty_req": forms.NumberInput(
                attrs={
                    "class": "job-table-input",
                    "min": "0",
                    "placeholder": "Qty",
                }
            ),
            "weight": forms.NumberInput(
                attrs={
                    "class": "job-table-input",
                    "min": "0",
                    "step": "0.001",
                    "placeholder": "Weight",
                }
            ),
            "metal_type": forms.Select(attrs={"class": "job-table-input"}),
        }
        labels = {
            "qty_req": "Qty Required",
            "metal_type": "Metal Type",
        }


class StyleStoneForm(forms.ModelForm):
    class Meta:
        model = StyleStone
        fields = ["stone_type", "stone_shape", "stone_size", "qty_req"]
        widgets = {
            "stone_type": forms.Select(attrs={"class": "job-table-input"}),
            "stone_shape": forms.Select(attrs={"class": "job-table-input"}),
            "stone_size": forms.TextInput(
                attrs={
                    "class": "job-table-input",
                    "placeholder": "e.g. 2.5mm",
                }
            ),
            "qty_req": forms.NumberInput(
                attrs={
                    "class": "job-table-input",
                    "min": "0",
                    "placeholder": "Qty",
                }
            ),
        }
        labels = {
            "qty_req": "Qty Required",
        }

StyleMetalFormSet = inlineformset_factory(
    parent_model=Style,
    model=StyleMetal,
    form=StyleMetalForm,
    extra=1,
    can_delete=True,
)

StyleStoneFormSet = inlineformset_factory(
    Style,
    StyleStone,
    form=StyleStoneForm,
    extra=1,
    can_delete=True,
)


class MetalReceiptForm(forms.ModelForm):
    lot_num = forms.CharField(
        max_length=50,
        widget=text_widget("Vendor lot number"),
        label="Lot Number",
    )

    class Meta:
        model = MetalReceipt
        fields = ["vendor", "reference", "notes", "lot_num"]
        widgets = {
            "vendor": select_widget(),
            "reference": text_widget("PO, invoice, or reference"),
            "notes": textarea_widget("Receipt notes...", rows=3),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if "vendor" in self.fields:
            self.fields["vendor"].empty_label = "Select a vendor"


class MetalReceiptLineForm(forms.ModelForm):
    class Meta:
        model = MetalReceiptLine
        fields = ["part", "qty_received"]
        widgets = {
            "part": table_select_widget(),
            "qty_received": table_number_widget(
                step="0.001",
                min_value="0",
                placeholder="Qty received",
            ),
        }
        labels = {
            "qty_received": "Qty Received",
        }


class MetalLotForm(forms.ModelForm):
    class Meta:
        model = MetalLot
        fields = ["part", "vendor_lot", "qty_on_hand"]
        widgets = {
            "part": select_widget(),
            "vendor_lot": select_widget(),
            "qty_on_hand": number_widget("Qty on hand", step="0.001", min_value="0"),
        }
        labels = {
            "qty_on_hand": "Qty On Hand",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if "part" in self.fields:
            self.fields["part"].empty_label = "Select a part"
        if "vendor_lot" in self.fields:
            self.fields["vendor_lot"].empty_label = "Select a vendor lot"


MetalLotFormSet = formset_factory(
    MetalLotForm,
    extra=5,
    can_delete=True,
)

MetalReceiptLineFormSet = inlineformset_factory(
    MetalReceipt,
    MetalReceiptLine,
    form=MetalReceiptLineForm,
    extra=3,
    can_delete=True,
)

JobMetalFormSet = inlineformset_factory(
    Job,
    JobMetal,
    form=JobMetalForm,
    extra=0,
    can_delete=True,
)

JobStoneFormSet = inlineformset_factory(
    Job,
    JobStone,
    form=JobStoneForm,
    extra=0,
    can_delete=True,
)

JobMetalLotFormSet = inlineformset_factory(
    JobMetal,
    JobMetalLot,
    form=JobMetalLotForm,
    extra=1,
    can_delete=True,
)


def get_job_metal_formset(extra=0):
    return inlineformset_factory(
        Job,
        JobMetal,
        form=JobMetalForm,
        extra=extra,
        can_delete=True,
    )


def get_job_stone_formset(extra=0):
    return inlineformset_factory(
        Job,
        JobStone,
        form=JobStoneForm,
        extra=extra,
        can_delete=True,
    )