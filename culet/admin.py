from django.contrib import admin

# Register your models here.
from .models import FindingType, FindingStock, StoneLot,StyleStone,StyleMetal,MetalLot,MetalPart, Job, Style, Activity, Employee, Customer, Department, MetalType, StoneType

admin.site.register(Job)
admin.site.register(Style)
admin.site.register(Activity)
admin.site.register(Employee)
admin.site.register(Customer)
admin.site.register(Department)
admin.site.register(StoneType)
admin.site.register(MetalType)
admin.site.register(StoneType)
admin.site.register(MetalPart)
admin.site.register(MetalLot)
admin.site.register(StyleMetal)
admin.site.register(StyleStone)
admin.site.register(StoneLot)
admin.site.register(FindingType)
admin.site.register(FindingStock)
