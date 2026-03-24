from django import forms
from .models import JobMetal, JobMetalLot, JobStone, Job, Style, StyleMetal, StyleStone, MetalLot, MetalReceipt, MetalReceiptLine
from django.utils import timezone
from django.contrib.auth.forms import AuthenticationForm, UsernameField
from django.forms import inlineformset_factory, formset_factory

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

class JobMetalForm(forms.ModelForm):
    class Meta:
        model = JobMetal
        fields = ["part", "qty_req", "weight_req", "metal_type"]

class JobStoneForm(forms.ModelForm):
    class Meta:
        model = JobStone
        fields = ["stone_type", "stone_shape", "stone_size", "qty_req"]

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