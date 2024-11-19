from django.contrib import admin

# Register your models here.
from .models import Job, Style, Activity, Department

admin.site.register(Job)
admin.site.register(Style)
admin.site.register(Activity)
admin.site.register(Department)