from django.contrib import admin

# Register your models here.
from .models import TimeClock,JobStatus, Location, Role, ActivityStep,Step, Vendor, StoneShape, FindingType, FindingStock,StyleStone,StyleMetal,MetalLot,MetalPart, Job, Style, Activity, Employee, Customer, Department, MetalType, StoneType

admin.site.register(Job)
admin.site.register(Style)
admin.site.register(Activity)
admin.site.register(Employee)
admin.site.register(Customer)
admin.site.register(Department)
admin.site.register(StoneType)
admin.site.register(MetalType)
admin.site.register(MetalPart)
admin.site.register(MetalLot)
admin.site.register(StyleMetal)
admin.site.register(StyleStone)
admin.site.register(FindingType)
admin.site.register(FindingStock)
admin.site.register(StoneShape)
admin.site.register(Vendor)
admin.site.register(Step)
admin.site.register(ActivityStep)
admin.site.register(Role)
admin.site.register(Location)
admin.site.register(JobStatus)
admin.site.register(TimeClock)