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
        widget=forms.DateInput(
            attrs={
                "type": "date",
            }
        ),
        label="Due Before:",
        field_name="due",
        lookup_expr="lte",
    )

    due_after = django_filters.DateFilter(
        widget=forms.DateInput(
            attrs={
                "type": "date",
            }
        ),
        label="Due After:",
        field_name="due",
        lookup_expr="gte",
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
    status = django_filters.ModelChoiceFilter(
        queryset=JobStatus.objects.order_by("name"),
        empty_label="All statuses",
        widget=forms.Select(
            attrs={
                "class": "combo-box",
                "data-placeholder": "All statuses",
            }
        ),
    )
    in_work = django_filters.ChoiceFilter(
        label="In Work",
        choices=(
            ("", "All"),
            ("true", "Yes"),
            ("false", "No"),
        ),
        method="filter_in_work",
    )
    assigned_to = django_filters.ModelChoiceFilter(
        queryset=(
            Employee.objects
            .select_related(
                "user",
                "department",
            )
            .filter(user__is_active=True)
            .order_by(
                "user__last_name",
                "user__first_name",
            )
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
        queryset=(
            Employee.objects
            .select_related(
                "user",
                "department",
            )
            .filter(user__is_active=True)
            .order_by(
                "user__last_name",
                "user__first_name",
            )
        ),
        empty_label="All holders",
        widget=forms.Select(
            attrs={
                "class": "combo-box",
                "data-placeholder": "All holders",
            }
        ),
    )

    holder_department = django_filters.ModelChoiceFilter(
        label="Holder Department:",
        field_name="holder__department",
        queryset=Department.objects.filter(
            active=True
        ).order_by("name"),
        empty_label="All holder departments",
        widget=forms.Select(
            attrs={
                "class": "combo-box",
                "data-placeholder": "All holder departments",
            }
        ),
    )

    shipped = django_filters.ChoiceFilter(
        label="Shipped",
        choices=(
            ("", "All"),
            ("true", "Yes"),
            ("false", "No"),
        ),
        method="filter_shipped",
    )

    def filter_shipped(
        self,
        queryset,
        name,
        value,
    ):
        if value == "":
            return queryset

        return queryset.filter(
            shipped=value == "true",
        )

    def filter_in_work(
        self,
        queryset,
        name,
        value,
    ):
        if value == "":
            return queryset

        if value == "true":
            return queryset.filter(
                in_work=True,
            )

        if value == "false":
            return queryset.filter(
                in_work=False,
            )

        return queryset

    class Meta:
        model = Job

        fields = [
            "barcode",
            "stock_num",
            "style",
            "customer",
            "assigned_to",
            "in_work",
            "holder",
            "holder_department",
            "shipped",
            "due_after",
            "due_date",
        ]
        
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
        
class StyleFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(
        field_name="name",
        lookup_expr="icontains",
        label="Style Name:",
        widget=forms.TextInput(
            attrs={
                "placeholder": "Search style name",
            }
        ),
    )

    customer = django_filters.ModelChoiceFilter(
        field_name="customer",
        queryset=Customer.objects.order_by("name"),
        label="Customer:",
        empty_label="All customers",
        widget=forms.Select(
            attrs={
                "class": "combo-box",
                "data-placeholder": "All customers",
            }
        ),
    )

    stamp = django_filters.CharFilter(
        field_name="stamp",
        lookup_expr="icontains",
        label="Stamp:",
        widget=forms.TextInput(
            attrs={
                "placeholder": "Search stamp",
            }
        ),
    )

    description = django_filters.CharFilter(
        field_name="description",
        lookup_expr="icontains",
        label="Description:",
        widget=forms.TextInput(
            attrs={
                "placeholder": "Search description",
            }
        ),
    )

    class Meta:
        model = Style
        fields = [
            "name",
            "customer",
            "stamp",
            "description",
        ]

class JobReportFilter(django_filters.FilterSet):
    stock_num = django_filters.CharFilter(
        label="Stock Number:",
        field_name="stock_num",
        lookup_expr="icontains",
        widget=forms.TextInput(
            attrs={
                "placeholder": "Search stock number",
            }
        ),
    )

    barcode = django_filters.CharFilter(
        label="Barcode:",
        field_name="barcode",
        lookup_expr="icontains",
        widget=forms.TextInput(
            attrs={
                "placeholder": "Search barcode",
            }
        ),
    )

    customer = django_filters.ModelChoiceFilter(
        label="Customer:",
        field_name="customer",
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
        label="Style:",
        field_name="style",
        queryset=Style.objects.order_by("name"),
        empty_label="All styles",
        widget=forms.Select(
            attrs={
                "class": "combo-box",
                "data-placeholder": "All styles",
            }
        ),
    )

    status = django_filters.ModelChoiceFilter(
        label="Status:",
        field_name="status",
        queryset=JobStatus.objects.filter(
            active=True,
        ).order_by(
            "sort_order",
            "name",
        ),
        empty_label="All statuses",
        widget=forms.Select(
            attrs={
                "class": "combo-box",
                "data-placeholder": "All statuses",
            }
        ),
    )

    assigned_to = django_filters.ModelChoiceFilter(
        label="Assigned To:",
        field_name="assigned_to",
        queryset=(
            Employee.objects
            .select_related(
                "user",
                "department",
            )
            .filter(user__is_active=True)
            .order_by(
                "user__last_name",
                "user__first_name",
            )
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
        label="Holder:",
        field_name="holder",
        queryset=(
            Employee.objects
            .select_related(
                "user",
                "department",
            )
            .filter(user__is_active=True)
            .order_by(
                "user__last_name",
                "user__first_name",
            )
        ),
        empty_label="All holders",
        widget=forms.Select(
            attrs={
                "class": "combo-box",
                "data-placeholder": "All holders",
            }
        ),
    )

    holder_department = django_filters.ModelChoiceFilter(
        label="Holder Department:",
        field_name="holder__department",
        queryset=Department.objects.filter(
            active=True,
        ).order_by("name"),
        empty_label="All holder departments",
        widget=forms.Select(
            attrs={
                "class": "combo-box",
                "data-placeholder": "All holder departments",
            }
        ),
    )

    location = django_filters.ModelChoiceFilter(
        label="Location:",
        field_name="location",
        queryset=Location.objects.order_by("name"),
        empty_label="All locations",
        widget=forms.Select(
            attrs={
                "class": "combo-box",
                "data-placeholder": "All locations",
            }
        ),
    )

    due_before = django_filters.DateFilter(
        label="Due Before:",
        field_name="due",
        lookup_expr="lte",
        widget=forms.DateInput(
            attrs={
                "type": "date",
            }
        ),
    )

    due_after = django_filters.DateFilter(
        label="Due After:",
        field_name="due",
        lookup_expr="gte",
        widget=forms.DateInput(
            attrs={
                "type": "date",
            }
        ),
    )

    class Meta:
        model = Job
        fields = [
            "stock_num",
            "barcode",
            "customer",
            "style",
            "status",
            "assigned_to",
            "holder",
            "holder_department",
            "location",
            "due_before",
            "due_after",
        ]

class MetalVendorLotFilter(django_filters.FilterSet):
    lot_num = django_filters.CharFilter(
        field_name="lot_num",
        lookup_expr="icontains",
        label="Lot Number:",
        widget=forms.TextInput(
            attrs={
                "placeholder": "Search lot number",
            }
        ),
    )

    vendor = django_filters.ModelChoiceFilter(
        field_name="vendor",
        queryset=Vendor.objects.order_by("name"),
        label="Vendor:",
        empty_label="All vendors",
        widget=forms.Select(
            attrs={
                "class": "combo-box",
                "data-placeholder": "All vendors",
            }
        ),
    )

    received_after = django_filters.DateFilter(
        field_name="received_at",
        lookup_expr="date__gte",
        label="Received After:",
        widget=forms.DateInput(
            attrs={
                "type": "date",
            }
        ),
    )

    received_before = django_filters.DateFilter(
        field_name="received_at",
        lookup_expr="date__lte",
        label="Received Before:",
        widget=forms.DateInput(
            attrs={
                "type": "date",
            }
        ),
    )

    class Meta:
        model = MetalVendorLot
        fields = [
            "lot_num",
            "vendor",
            "received_after",
            "received_before",
        ]

class OpenPieceworkFilter(django_filters.FilterSet):
    assigned_to = django_filters.ModelChoiceFilter(
        label="Assigned To",
        field_name="memo__assigned_to",
        queryset=(
            Employee.objects
            .select_related(
                "user",
                "department",
            )
            .filter(user__is_active=True)
            .order_by(
                "user__last_name",
                "user__first_name",
            )
        ),
        empty_label="All employees",
        widget=forms.Select(
            attrs={
                "class": "combo-box",
                "data-placeholder": "All employees",
            }
        ),
    )

    stock_num = django_filters.CharFilter(
        label="Stock Number",
        field_name="job__stock_num",
        lookup_expr="icontains",
        widget=forms.TextInput(
            attrs={
                "placeholder": "Enter stock number",
            }
        ),
    )

    memo_num = django_filters.CharFilter(
        label="Memo Number",
        field_name="memo__memo_num",
        lookup_expr="icontains",
        widget=forms.TextInput(
            attrs={
                "placeholder": "Enter memo number",
            }
        ),
    )

    customer = django_filters.ModelChoiceFilter(
        label="Customer",
        field_name="job__customer",
        queryset=Customer.objects.order_by("name"),
        empty_label="All customers",
        widget=forms.Select(
            attrs={
                "class": "combo-box",
                "data-placeholder": "All customers",
            }
        ),
    )

    due_back = django_filters.DateFilter(
        label="Due Back On or Before",
        field_name="memo__due_back",
        lookup_expr="lte",
        widget=forms.DateInput(
            attrs={
                "type": "date",
            }
        ),
    )

    class Meta:
        model = PieceworkMemoLine
        fields = []

class JobShipFilter(django_filters.FilterSet):
    shipped_after = django_filters.DateFilter(
        field_name="shipped_at",
        lookup_expr="date__gte",
        label="Shipped From",
        widget=forms.DateInput(
            attrs={
                "type": "date",
            }
        ),
    )

    shipped_before = django_filters.DateFilter(
        field_name="shipped_at",
        lookup_expr="date__lte",
        label="Shipped Through",
        widget=forms.DateInput(
            attrs={
                "type": "date",
            }
        ),
    )

    stock_num = django_filters.CharFilter(
        field_name="job__stock_num",
        lookup_expr="icontains",
        label="Stock Number",
        widget=forms.TextInput(
            attrs={
                "placeholder": "Search stock number",
            }
        ),
    )

    barcode = django_filters.CharFilter(
        field_name="job__barcode",
        lookup_expr="icontains",
        label="Barcode",
        widget=forms.TextInput(
            attrs={
                "placeholder": "Search barcode",
                "inputmode": "numeric",
            }
        ),
    )

    due_after = django_filters.DateFilter(
        field_name="job__due",
        lookup_expr="gte",
        label="Due From",
        widget=forms.DateInput(
            attrs={
                "type": "date",
            }
        ),
    )

    due_before = django_filters.DateFilter(
        field_name="job__due",
        lookup_expr="lte",
        label="Due Through",
        widget=forms.DateInput(
            attrs={
                "type": "date",
            }
        ),
    )

    style = django_filters.ModelChoiceFilter(
        field_name="job__style",
        queryset=Style.objects.order_by("name"),
        empty_label="All styles",
        label="Style",
        widget=forms.Select(
            attrs={
                "class": "combo-box",
                "data-placeholder": "All styles",
            }
        ),
    )

    customer = django_filters.ModelChoiceFilter(
        field_name="job__customer",
        queryset=Customer.objects.order_by("name"),
        empty_label="All customers",
        label="Customer",
        widget=forms.Select(
            attrs={
                "class": "combo-box",
                "data-placeholder": "All customers",
            }
        ),
    )

    shipped_by = django_filters.ModelChoiceFilter(
        field_name="shipped_by",
        queryset=Employee.objects.filter(
            active=True,
        ).select_related(
            "user",
        ).order_by(
            "user__last_name",
            "user__first_name",
        ),
        empty_label="All employees",
        label="Shipped By",
        widget=forms.Select(
            attrs={
                "class": "combo-box",
                "data-placeholder": "All employees",
            }
        ),
    )

    class Meta:
        model = JobShip
        fields = []