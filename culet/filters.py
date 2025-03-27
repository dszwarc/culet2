import django_filters
from django_filters import DateFilter, CharFilter, DateTimeFilter
from .models import *

class JobFilter(django_filters.FilterSet):
    start_date = DateFilter(label='Created after:',field_name="created", lookup_expr='gte')
    end_date = DateFilter(label='Created before:', field_name="created", lookup_expr='lte')
    due_date = DateTimeFilter(label='Due Before:',field_name='due', lookup_expr='lte')

    customer = CharFilter(label='Customer',field_name='customer', lookup_expr='icontains')
    
    class Meta:
        model = Job
        fields = '__all__'
        exclude = ['date','name','created','last_updated','due']