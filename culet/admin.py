from django.contrib import admin

# Register your models here.
from .models import Job, Style, Activity, Employee

admin.site.register(Job)
admin.site.register(Style)
admin.site.register(Activity)
admin.site.register(Employee)