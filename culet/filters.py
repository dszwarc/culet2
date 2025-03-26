import django_filters
from django_filters import DateFilter, CharFilter
from .models import *

class JobFilter(django_filters.FilterSet):
    start_date = DateFilter(field_name="created", lookup_expr='gte')
    end_date = DateFilter(field_name="created", lookup_expr='lte')
    
    customer = CharFilter(field_name='customer', lookup_expr='icontains')
    
    class Meta:
        model = Job
        fields = '__all__'
        exclude = ['date','name']
