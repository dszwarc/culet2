from django import forms
from .models import Job, Style, StyleMetal, StyleStone, MetalLot
from django.utils import timezone
from django.contrib.auth.forms import AuthenticationForm, UsernameField
from django.forms import inlineformset_factory, formset_factory

class DateInput(forms.DateInput):
    input_type = 'date'

class JobForm(forms.ModelForm):
    class Meta:
        model = Job
        fields = ('name','customer','job_num','style','due','notes')

        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'customer': forms.TextInput(attrs={'class': 'form-control'}),
            'job_num': forms.TextInput(attrs={'class': 'form-control'}),
            'style': forms.Select(attrs={'class': 'form-control'}),
            'due': forms.DateInput(attrs={'class': 'form-control datepicker', 'type':'date'}),
            'notes': forms.Textarea(attrs={'class': 'form-control'}),
        }

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

class MetalLotForm(forms.ModelForm):
    class Meta:
        model = MetalLot
        fields = ["part","lot_num","qty_on_hand"]
        widgets = {
            "qty_on_hand": forms.NumberInput(attrs={"min":0}),
        }

MetalLotFormSet = formset_factory(
    MetalLotForm,
    extra=5,
    can_delete=True
)