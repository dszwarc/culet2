from django import forms
from .models import Job, Style
from django.utils import timezone

class JobForm(forms.ModelForm):
    class Meta:
        model = Job
        fields = ('name','customer','job_num','style','due','notes')

        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'customer': forms.TextInput(attrs={'class': 'form-control'}),
            'job_num': forms.TextInput(attrs={'class': 'form-control'}),
            'style': forms.Select(attrs={'class': 'form-control'}),
            'due': forms.DateInput(attrs={'class': 'form-control', 'placeholder':timezone.now}),
            'notes': forms.Textarea(attrs={'class': 'form-control'}),
        }

class StyleForm(forms.ModelForm):
    class Meta:
        model = Style
        fields = ('name','product')

        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'product': forms.Select(attrs={'class': 'form-control'}),
        }