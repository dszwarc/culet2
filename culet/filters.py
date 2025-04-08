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

class ActivityFilter(django_filters.FilterSet):
    name = CharFilter(label='Operation',field_name='name',lookup_expr='icontains')
    style = CharFilter(label='Style', field_name='job__style__name', lookup_expr='icontains')
    start = DateFilter(label='Started after:',field_name="start", lookup_expr='gte')
    end = DateFilter(label='Ended before:', field_name="end", lookup_expr='lte')
    

    class Meta:
        model = Activity
        fields = '__all__'
        exclude = ['start', 'end','active']
        