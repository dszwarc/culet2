from django.contrib import admin

# Register your models here.
from .models import Job, Style, Activity, Employee, Customer, Department

admin.site.register(Job)
admin.site.register(Style)
admin.site.register(Activity)
admin.site.register(Employee)
admin.site.register(Customer)
admin.site.register(Department)