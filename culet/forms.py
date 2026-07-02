from django import forms
from django.contrib.auth.forms import AuthenticationForm, UsernameField
from django.forms import inlineformset_factory, formset_factory
from django.utils import timezone
from decimal import Decimal
from django.core.exceptions import ValidationError

from .models import (
    FailureType,
    QualityInspection,
    QualityInspectionFailure,
    Location,
    PieceworkMemo,
    Customer,
    Department,
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
    Activity,
    ActivityStep,
    Employee,
    TimeClock,
    StyleFinding,
    JobFinding,
    JobTransferMemo
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

from .models import Activity, ActivityStep


class ActivityStartForm(forms.ModelForm):
    class Meta:
        model = Activity
        fields = ["step"]

    def __init__(self, *args, employee=None, **kwargs):
        super().__init__(*args, **kwargs)

        qs = ActivityStep.objects.all()

        if employee and employee.department_fk:
            qs = qs.filter(
                departments=employee.department_fk
            ).distinct()

        self.fields["step"].queryset = qs
        self.fields["step"].empty_label = "Choose activity step"

class JobWeightForm(forms.ModelForm):
    class Meta:
        model = JobWeight
        fields = ["step","weight", "sprue_weight", "dust_weight"]
        widgets = {
            "weight": forms.NumberInput(attrs={"step": "0.001", "min": "0"}),
            "sprue_weight": forms.NumberInput(attrs={"step": "0.001", "min": "0"}),
            "dust_weight": forms.NumberInput(attrs={"step": "0.001", "min": "0"}),
        }
        labels = {
            "weight": "Piece Weight",
            "sprue_weight": "Sprue Weight",
            "dust_weight": "Dust Weight",
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

class JobShipLineForm(forms.Form):
    barcode = forms.CharField(
        required=False,
        label="Barcode",
        widget=forms.TextInput(attrs={
            "class": BASE_INPUT_CLASS,
            "placeholder": "Scan barcode",
        }),
    )


JobShipLineFormSet = formset_factory(
    JobShipLineForm,
    extra=3,
    can_delete=False,
)


class BulkJobShipForm(forms.Form):
    notes = forms.CharField(
        required=False,
        label="Shipping Notes",
        widget=forms.Textarea(attrs={
            "class": BASE_INPUT_CLASS,
            "rows": 3,
        }),
    )

class JobWeightLookupForm(forms.Form):
    barcode = forms.IntegerField(required=False, label="Barcode")
    stock_num = forms.IntegerField(required=False, label="Stock Number")

    def clean(self):
        cleaned_data = super().clean()
        barcode = cleaned_data.get("barcode")
        stock_num = cleaned_data.get("stock_num")

        if not barcode and not stock_num:
            raise ValidationError("Enter either a barcode number or a stock number.")

        return cleaned_data

class JobForm(forms.ModelForm):
    class Meta:
        model = Job
        fields = [
            "repair_reasons",
            "customer",
            "stock_num",
            "style",
            "size",
            "stamp",
            "due",
            "notes",
        ]
        widgets = {
            "repair_reasons": forms.CheckboxSelectMultiple(),
            "customer": select_widget(),
            "stock_num": number_widget("Stock #", min_value="0"),
            "style": forms.HiddenInput(),
            "size": text_widget("Size"),
            "stamp": text_widget("Stamp / hallmark"),
            "due": date_widget(),
            "notes": textarea_widget("Add notes for the shop...", rows=4),
        }
        labels = {
            "repair_reasons": "Repair Reason(s)",
            "stock_num": "Stock #",
            "assigned_to": "Assigned To",
        }

    def __init__(self, *args, **kwargs):
        is_repair = kwargs.pop("is_repair", False)
        super().__init__(*args, **kwargs)

        self.fields["customer"].required = False
        self.fields["stock_num"].required = False
        self.fields["notes"].required = False
        self.fields["size"].required = False
        self.fields["stamp"].required = False
        self.fields["due"].required = False

        if "style" in self.fields:
            self.fields["style"].empty_label = "Select a style"
        if "customer" in self.fields:
            self.fields["customer"].empty_label = "Select a customer"
        if "assigned_to" in self.fields:
            self.fields["assigned_to"].empty_label = "Unassigned"
        if "location" in self.fields:
            self.fields["location"].empty_label = "No current location"

        if is_repair:
            self.fields["repair_reasons"].required = True
            self.fields["repair_reasons"].queryset = FailureType.objects.all().order_by("name")
        else:
            self.fields["repair_reasons"].required = False
            self.fields["repair_reasons"].widget = forms.HiddenInput()

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
        fields = ("name", "customer", "style", "size", "stamp", "due", "notes")
        widgets = {
            "name": text_widget("Job name"),
            "customer": select_widget(),
            "style": select_widget(),
            "size": text_widget("Size"),
            "stamp": text_widget("Stamp / hallmark"),
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
    cost = forms.DecimalField(
        max_digits=15,
        decimal_places=2,
        required=False,
        min_value=0,
        widget=table_number_widget(
            step="0.01",
            min_value="0",
            placeholder="Cost",
        ),
    )
    class Meta:
        model = MetalReceiptLine
        fields = ["part", "qty_received","weight_received", "cost"]
        widgets = {
            "part": table_select_widget(),
            "qty_received": table_number_widget(
                step="0.001",
                min_value="0",
                placeholder="Qty received",
            ),
            "weight_received": table_number_widget(
                step="0.001",
                min_value="0",
                placeholder="Weight received",
            ),
        }
        labels = {
            "qty_received": "Qty Received",
            "weight_received":"Weight Received",
            "cost":"Cost",
        }


class MetalLotForm(forms.ModelForm):
    class Meta:
        model = MetalLot
        fields = ["part", "vendor_lot", "qty_on_hand", "weight_on_hand", "cost"]
        widgets = {
            "part": select_widget(),
            "vendor_lot": select_widget(),
            "qty_on_hand": number_widget("Qty on hand", step="0.001", min_value="0"),
            "weight_on_hand": number_widget("Weight on hand", step="0.001", min_value="0"),
            "cost": number_widget("Cost", step="0.01", min_value="0"),
        }
        labels = {
            "qty_on_hand": "Qty On Hand",
            "weight_on_hand": "Weight On Hand",
            "cost": "Cost",
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

StyleFindingFormSet = inlineformset_factory(
    Style,
    StyleFinding,
    fields=("finding", "qty_req"),
    extra=1,
    can_delete=True,
)

class JobFindingForm(forms.ModelForm):
    class Meta:
        model = JobFinding
        fields = ["finding", "qty_req", "qty_used"]
        widgets = {
            "finding": table_select_widget(),
            "qty_req": table_number_widget(min_value="0", placeholder="Qty required"),
            "qty_used": table_number_widget(min_value="0", placeholder="Qty used"),
        }

JobFindingFormSet = inlineformset_factory(
    Job,
    JobFinding,
    form=JobFindingForm,
    extra=1,
    can_delete=True,
)

def get_job_finding_formset(extra=0):
    return inlineformset_factory(
        Job,
        JobFinding,
        form=JobFindingForm,
        extra=extra,
        can_delete=True,
    )

class InactiveJobsReportForm(forms.Form):
    days = forms.IntegerField(
        min_value=1,
        initial=7,
        label="Inactive for at least this many days",
        widget=forms.NumberInput(attrs={
            "class": "form-control job-input",
            "min": "1",
        }),
    )

class WeightLossByStyleReportForm(forms.Form):
    style = forms.ModelChoiceField(
        queryset=Style.objects.all().order_by("name"),
        required=False,
        empty_label="All styles",
        widget=forms.Select(attrs={
            "class": "form-control job-input",
        }),
    )

class EmployeeActivityReportForm(forms.Form):
    employee = forms.ModelChoiceField(
        queryset=Employee.objects.select_related("user").order_by(
            "user__last_name",
            "user__first_name",
        ),
        required=True,
        label="Employee",
        widget=select_widget(),
    )

    style = forms.ModelChoiceField(
        queryset=Style.objects.order_by("name"),
        required=False,
        empty_label="All styles",
        label="Style",
        widget=select_widget(),
    )

    start_date = forms.DateField(
        required=True,
        label="Start Date",
        widget=date_widget(),
    )

    end_date = forms.DateField(
        required=True,
        label="End Date",
        widget=date_widget(),
    )

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")

        if start_date and end_date and end_date < start_date:
            raise forms.ValidationError("End date cannot be before start date.")

        return cleaned_data
    
class TimeClockReportForm(forms.Form):
    employee = forms.ModelChoiceField(
        queryset=Employee.objects.select_related("user")
        .filter(role_fk__requires_clock_in=True)
        .order_by("user__last_name", "user__first_name"),
        required=False,
        empty_label="All employees",
        label="Employee",
        widget=select_widget(),
    )

    start_date = forms.DateField(
        required=True,
        label="Week / Start Date",
        widget=date_widget(),
    )

    end_date = forms.DateField(
        required=True,
        label="End Date",
        widget=date_widget(),
    )

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")

        if start_date and end_date and end_date < start_date:
            raise forms.ValidationError("End date cannot be before start date.")

        return cleaned_data
    
class TimeClockEditForm(forms.ModelForm):
    class Meta:
        model = TimeClock
        fields = ["employee", "clock_in", "clock_out"]
        widgets = {
            "employee": select_widget(),
            "clock_in": forms.DateTimeInput(
                attrs={
                    "type": "datetime-local",
                    "class": "form-control job-input",
                }
            ),
            "clock_out": forms.DateTimeInput(
                attrs={
                    "type": "datetime-local",
                    "class": "form-control job-input",
                }
            ),
        }

class JobsByHolderReportForm(forms.Form):
    department = forms.ModelChoiceField(
        queryset=Department.objects.order_by("name"),
        required=False,
        label="Department",
        widget=select_widget(),
    )

    employee = forms.ModelChoiceField(
        queryset=Employee.objects
            .select_related("user")
            .order_by("user__last_name", "user__first_name"),
        required=False,
        label="Employee",
        widget=select_widget(),
    )


class JobTransferMemoForm(forms.ModelForm):
    scanned_jobs = forms.CharField(
        label="Scan Jobs",
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 10,
            "placeholder": "Scan one barcode per line"
        }),
        help_text="Scan or enter one job barcode per line."
    )

    class Meta:
        model = JobTransferMemo
        fields = ["from_location", "to_location", "memo_to", "notes"]
        widgets = {
            "from_location": forms.Select(attrs={"class": "form-control"}),
            "to_location": forms.Select(attrs={"class": "form-control"}),
            "memo_to": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Customer, building, department, etc."
            }),
            "notes": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 3
            }),
        }

class MetalPartInventoryFilterForm(forms.Form):
    q = forms.CharField(
        required=False,
        label="Part",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Search part SKU or description",
        })
    )

class JobShippedReportForm(forms.Form):
    start_date = forms.DateField(
        required=False,
        widget=date_widget(),
        label="Start Date",
    )
    end_date = forms.DateField(
        required=False,
        widget=date_widget(),
        label="End Date",
    )
    style = forms.ModelChoiceField(
        queryset=Style.objects.all().order_by("name"),
        required=False,
        empty_label="All Styles",
        widget=select_widget(),
    )
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.all().order_by("name"),
        required=False,
        empty_label="All Customers",
        widget=select_widget(),
    )

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")

        if start_date and end_date and start_date > end_date:
            raise forms.ValidationError("Start date cannot be after end date.")

        return cleaned_data
    
class StyleStepTimeReportForm(forms.Form):
    style = forms.ModelChoiceField(
        queryset=Style.objects.all().order_by("name"),
        required=False,
        empty_label="All Styles",
        widget=select_widget(),
    )

class PieceworkMemoCreateForm(forms.ModelForm):
    class Meta:
        model = PieceworkMemo
        fields = ["assigned_to", "due_back", "notes"]

        widgets = {
            "due_back": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }


class PieceworkScanForm(forms.Form):
    scans = forms.CharField(
        label="Scan jobs",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 10,
                "placeholder": "Scan one barcode per line",
                "autofocus": "autofocus",
            }
        ),
        help_text="Scan or enter one job barcode per line.",
    )

class MemoFilterForm(forms.Form):
    MEMO_TYPE_CHOICES = [
        ("", "All Memo Types"),
        ("transfer", "Transfer"),
        ("piecework", "Piecework"),
    ]

    memo_type = forms.ChoiceField(
        choices=MEMO_TYPE_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    from_location = forms.ModelChoiceField(
        queryset=Location.objects.all().order_by("name"),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    to_location = forms.ModelChoiceField(
        queryset=Location.objects.all().order_by("name"),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    created_start = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )

    created_end = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )

class QualityInspectionForm(forms.Form):
    barcode = forms.CharField(
        label="Job Barcode",
        widget=text_widget("Scan job barcode"),
    )

    result = forms.ChoiceField(
        label="Inspection Result",
        choices=QualityInspection.RESULT_CHOICES,
        widget=select_widget(),
    )

    failure_types = forms.ModelMultipleChoiceField(
        label="Failure Type(s)",
        queryset=FailureType.objects.filter(active=True),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    notes = forms.CharField(
        label="Notes",
        required=False,
        widget=textarea_widget("Optional QC notes", rows=3),
    )

    def clean_barcode(self):
        barcode = self.cleaned_data["barcode"].strip()

        try:
            return int(barcode)
        except ValueError:
            raise forms.ValidationError("Barcode must be a number.")

    def clean(self):
        cleaned_data = super().clean()
        result = cleaned_data.get("result")
        failure_types = cleaned_data.get("failure_types")

        if result == QualityInspection.RESULT_FAIL and not failure_types:
            self.add_error(
                "failure_types",
                "Choose at least one failure type when a job fails QC.",
            )

        if result == QualityInspection.RESULT_PASS and failure_types:
            self.add_error(
                "failure_types",
                "Passed inspections should not have failure types.",
            )

        return cleaned_data


class QualityFailureReportForm(forms.Form):
    start_date = forms.DateField(required=False, widget=date_widget())
    end_date = forms.DateField(required=False, widget=date_widget())

    failure_type = forms.ModelChoiceField(
        queryset=FailureType.objects.filter(active=True),
        required=False,
        empty_label="All failure types",
        widget=select_widget(),
    )

    style = forms.ModelChoiceField(
        queryset=Style.objects.all().order_by("name"),
        required=False,
        empty_label="All styles",
        widget=select_widget(),
    )

    customer = forms.ModelChoiceField(
        queryset=Customer.objects.all().order_by("name"),
        required=False,
        empty_label="All customers",
        widget=select_widget(),
    )

class RepairCreateForm(forms.Form):
    stock_num = forms.CharField(
        label="Scan original job stock number",
        max_length=100,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "autofocus": "autofocus",
            "placeholder": "Scan stock number",
        })
    )

class RepairLookupForm(forms.Form):
    stock_num = forms.CharField(
        label="Scan Original Job",
        max_length=100,
        widget=forms.TextInput(attrs={
            "autofocus": "autofocus",
            "class": "form-control",
            "placeholder": "Scan stock number or barcode",
        })
    )