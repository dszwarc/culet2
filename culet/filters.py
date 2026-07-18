import django_filters
from django_filters import DateFilter, CharFilter, DateTimeFilter
from .models import *
from django import forms


class JobFilter(django_filters.FilterSet):
    barcode = django_filters.CharFilter(
        label="Barcode:",
        field_name="barcode",
        lookup_expr="icontains",
    )

    stock_num = django_filters.CharFilter(
        label="Stock Number:",
        field_name="stock_num",
        lookup_expr="icontains",
    )

    due_date = django_filters.DateFilter(
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Due Before:",
        field_name="due",
        lookup_expr="lte",
    )

    notes = django_filters.CharFilter(
        label="Notes:",
        field_name="notes",
        lookup_expr="icontains",
    )

    customer = django_filters.ModelChoiceFilter(
        queryset=Customer.objects.order_by("name"),
        empty_label="All customers",
        widget=forms.Select(
            attrs={
                "class": "combo-box",
                "data-placeholder": "All customers",
            }
        ),
    )

    style = django_filters.ModelChoiceFilter(
        queryset=Style.objects.order_by("name"),
        empty_label="All styles",
        widget=forms.Select(
            attrs={
                "class": "combo-box",
                "data-placeholder": "All styles",
            }
        ),
    )

    assigned_to = django_filters.ModelChoiceFilter(
        queryset=Employee.objects.select_related("user").order_by(
            "user__last_name",
            "user__first_name",
        ),
        empty_label="All employees",
        widget=forms.Select(
            attrs={
                "class": "combo-box",
                "data-placeholder": "All employees",
            }
        ),
    )

    holder = django_filters.ModelChoiceFilter(
        queryset=Employee.objects.select_related("user").order_by(
            "user__last_name",
            "user__first_name",
        ),
        empty_label="All holders",
        widget=forms.Select(
            attrs={
                "class": "combo-box",
                "data-placeholder": "All holders",
            }
        ),
    )

    class Meta:
        model = Job
        fields = [
            "barcode",
            "stock_num",
            "style",
            "customer",
            "assigned_to",
            "holder",
            "location",
            "status",
            "shipped",
            "due_date",
        ]
        exclude = ['status']

class ActivityFilter(django_filters.FilterSet):
    name = CharFilter(label='Operation:',field_name='name',lookup_expr='icontains')
    style = CharFilter(label='Style:', field_name='job__style__name', lookup_expr='icontains')
    start = DateFilter(widget=forms.DateInput(attrs={'type':'date'}),label='Started after:',field_name="start", lookup_expr='gte')
    end = DateFilter(widget=forms.DateInput(attrs={'type':'date'}),label='Ended before:', field_name="end", lookup_expr='lte')
    job = CharFilter(label='Stock Num:', field_name='job__stock_num', lookup_expr='icontains')
    barcode = CharFilter(label='Barcode:', field_name='job__barcode',lookup_expr='icontains')
    class Meta:
        model = Activity
        fields = '__all__'
        exclude = ['start', 'end','active','job']
        