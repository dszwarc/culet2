from django import forms
from .models import JobWeight, JobMetal, JobMetalLot, JobStone, Job, Style, StyleMetal, StyleStone, MetalLot, MetalReceipt, MetalReceiptLine
from django.utils import timezone
from django.contrib.auth.forms import AuthenticationForm, UsernameField
from django.forms import inlineformset_factory, formset_factory
from decimal import Decimal
from django.core.exceptions import ValidationError

class DateInput(forms.DateInput):
    input_type = 'date'

# class JobForm(forms.ModelForm):
#     class Meta:
#         model = Job
#         fields = ('name','customer','job_num','style','due','notes')

#         widgets = {
#             'name': forms.TextInput(attrs={'class': 'form-control'}),
#             'customer': forms.TextInput(attrs={'class': 'form-control'}),
#             'job_num': forms.TextInput(attrs={'class': 'form-control'}),
#             'style': forms.Select(attrs={'class': 'form-control'}),
#             'due': forms.DateInput(attrs={'class': 'form-control datepicker', 'type':'date'}),
#             'notes': forms.Textarea(attrs={'class': 'form-control'}),
#         }

class JobWeightForm(forms.ModelForm):
    class Meta:
        model = JobWeight
        fields = ["weight", "sprue_weight", "dust_weight"]
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
            "name": forms.TextInput(
                attrs={
                    "class": "form-control form-control-lg job-input",
                    "placeholder": "Job name",
                }
            ),
            "customer": forms.Select(
                attrs={
                    "class": "form-control job-input",
                }
            ),
            "customer_ref_num": forms.NumberInput(
                attrs={
                    "class": "form-control job-input",
                    "placeholder": "Customer reference #",
                    "min": "0",
                }
            ),
            "style": forms.Select(
                attrs={
                    "class": "form-control job-input",
                    "id": "id_style",
                }
            ),
            "due": forms.DateInput(
                attrs={
                    "class": "form-control job-input",
                    "type": "date",
                }
            ),
            "assigned_to": forms.Select(
                attrs={
                    "class": "form-control job-input",
                }
            ),
            "location": forms.Select(
                attrs={
                    "class": "form-control job-input",
                }
            ),
            "notes": forms.Textarea(
                attrs={
                    "class": "form-control job-input",
                    "rows": 4,
                    "placeholder": "Add notes for the shop...",
                }
            ),
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

        self.fields["style"].empty_label = "Select a style"
        self.fields["customer"].empty_label = "Select a customer"
        self.fields["assigned_to"].empty_label = "Unassigned"
        self.fields["location"].empty_label = "No current location"

class JobMetalForm(forms.ModelForm):
    class Meta:
        model = JobMetal
        fields = ["part", "qty_req", "weight_req", "metal_type"]
        widgets = {
            "part": forms.Select(attrs={"class": "job-table-input"}),
            "qty_req": forms.NumberInput(attrs={"class": "job-table-input", "min": "0"}),
            "weight_req": forms.NumberInput(attrs={"class": "job-table-input", "min": "0"}),
            "metal_type": forms.Select(attrs={"class": "job-table-input"}),
        }


class JobStoneForm(forms.ModelForm):
    class Meta:
        model = JobStone
        fields = ["stone_type", "stone_shape", "stone_size", "qty_req"]
        widgets = {
            "stone_type": forms.Select(attrs={"class": "job-table-input"}),
            "stone_shape": forms.Select(attrs={"class": "job-table-input"}),
            "stone_size": forms.TextInput(attrs={"class": "job-table-input", "placeholder": "e.g. 2.5mm"}),
            "qty_req": forms.NumberInput(attrs={"class": "job-table-input", "min": "0"}),
        }

class JobMetalLotForm(forms.ModelForm):
    class Meta:
        model = JobMetalLot
        fields = ["metal_lot", "qty_used", "weight_used"]

    def __init__(self, *args, **kwargs):
        job_metal = kwargs.pop("job_metal", None)
        super().__init__(*args, **kwargs)

        if job_metal:
            self.fields["metal_lot"].queryset = MetalLot.objects.filter(
                part=job_metal.part,
                qty_on_hand__gt=0,
            ).order_by("vendor_lot__lot_num")

class JobUpdateForm(forms.ModelForm):
    class Meta:
        model = Job
        fields = ('name','customer','job_num','style','due','notes')

        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'customer': forms.TextInput(attrs={'class': 'form-control'}),
            'job_num': forms.TextInput(attrs={'class': 'form-control'}),
            'style': forms.Select(attrs={'class': 'form-control'}),
            'due': forms.DateInput(attrs={'class': 'form-control','type':'date'}),
            'notes': forms.Textarea(attrs={'class': 'form-control'}),
        }

class StyleForm(forms.ModelForm):
    class Meta:
        model = Style
        fields = ('name','customer', 'stamp', 'description')

        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'product': forms.Select(attrs={'class': 'form-control'}),
        }

class StyleMetalForm(forms.ModelForm):
    class Meta:
        model = StyleMetal
        fields = ["part", "qty_req", "weight", "metal_type"]

StyleMetalFormSet = inlineformset_factory(
    parent_model = Style,
    model = StyleMetal,
    form = StyleMetalForm,
    extra = 1,
    can_delete = True
)

class StyleStoneForm(forms.ModelForm):
    class Meta:
        model = StyleStone
        fields = ["stone_type", "stone_shape", "stone_size", "qty_req"]

StyleStoneFormSet = inlineformset_factory(
    Style,
    StyleStone,
    form=StyleStoneForm,
    extra=1,
    can_delete=True
)

class MetalReceiptForm(forms.ModelForm):
    lot_num = forms.CharField(max_length=50)

    class Meta:
        model = MetalReceipt
        fields = ["vendor", "reference", "notes", "lot_num"]

class MetalReceiptLineForm(forms.ModelForm):
    class Meta:
        model = MetalReceiptLine
        fields = ["part", "qty_received"]

class MetalLotForm(forms.ModelForm):
    class Meta:
        model = MetalLot
        fields = ["part","vendor_lot","qty_on_hand"]
        widgets = {
            "qty_on_hand": forms.NumberInput(attrs={"min":0}),
        }

MetalLotFormSet = formset_factory(
    MetalLotForm,
    extra=5,
    can_delete=True
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

from django.forms import inlineformset_factory

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