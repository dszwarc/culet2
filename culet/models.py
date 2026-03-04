from django.db import models
from django.utils import timezone
from django.urls import reverse
from datetime import date, timedelta
from django.contrib.auth.models import User
# Create your models here.

class StoneType(models.Model):
    name = models.CharField(max_length=80, unique=True)
    def __str__(self):
        return self.name
    
class StoneShape(models.Model):
    name = models.CharField(max_length=80, unique=True)
    def __str__(self):
        return self.name

class MetalType(models.Model):
    name = models.CharField(max_length=80, unique=True)
    def __str__(self):
        return self.name

class Customer(models.Model):
    name = models.CharField(max_length=80,unique=True)
    address = models.CharField(max_length=150)
    email = models.EmailField(max_length=200)
    phone = models.CharField(max_length=12)
    number = models.IntegerField(blank=True, null=True)

    def __str__(self):
        return self.name

class Department(models.Model):
    name = models.CharField(max_length=80, default="Production")

    def __str__(self):
        return self.name

class Employee(models.Model):

    employee_choices = [
        ("PD", "Production"),
        ("MR", "Manager"),
        ("OF", "Office"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    department = models.CharField(max_length=80, default="Product Management")
    role = models.CharField(max_length = 2,choices=employee_choices, default = "PD")
    clocked_in = models.BooleanField(default=False)
    

    def __str__(self):
        return (str(self.user.first_name) + " " + str(self.user.last_name))

class Style(models.Model):
    name = models.CharField(max_length=50, unique=True)
    customer = models.ForeignKey(Customer, blank=True, on_delete=models.PROTECT, null=True)
    stamp = models.CharField(blank=True, max_length=80)
    description = models.TextField(max_length=500, null=True)
    product = models.CharField(max_length=20, blank=True, null=True)

    def __str__(self):
        return self.name

class Job(models.Model):

    name = models.CharField(max_length=80,default="N/A")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, null=True)
    job_num = models.IntegerField(default=0, unique=True)
    customer_ref_num = models.IntegerField(null=True, unique=True)
    active = models.BooleanField(default=True)
    shipped = models.BooleanField(default=False)
    in_work = models.BooleanField(default=False)
    style = models.ForeignKey(Style, on_delete=models.PROTECT)
    created = models.DateTimeField(default=timezone.now, editable = False)
    due = models.DateField(default=timezone.now)
    last_updated = models.DateTimeField(auto_now=True)
    assigned_to = models.ForeignKey(Employee, on_delete=models.CASCADE, null=True, related_name='job_assignment')
    location = models.ForeignKey(Employee, on_delete=models.CASCADE, null=True, related_name='job_location')
    notes = models.TextField(default="", null=True)

    @property
    def is_past_due(self):
        return date.today() > self.due

    @property
    def is_near_due(self):
        return date.today() > self.due - timedelta(days=30)

    def __str__(self):
        return str(self.job_num).zfill(5)
    
    def get_absolute_url(self):
        return reverse('culet:job_detail', kwargs={'pk': self.pk})
    
class Metal(models.Model):
    lot_num = models.IntegerField(unique=True, blank=True)
    job = models.ForeignKey(Job, on_delete=models.CASCADE, null=True, blank=True)
    metal_type = models.ForeignKey(MetalType, on_delete=models.PROTECT, null=True, blank=True)
    weight = models.CharField(max_length=10)

class MetalPart(models.Model):
    sku = models.CharField(max_length=50, unique=True)
    description = models.TextField(max_length=200, blank=True, null=True)
    def __str__(self):
        return self.sku

class MetalLot(models.Model):
    lot_num = models.CharField(max_length=50, unique=True)
    part = models.ForeignKey(MetalPart, on_delete=models.PROTECT)
    qty_on_hand = models.PositiveIntegerField(default=0)
    received_at = models.DateField(auto_now_add=True)

class StyleMetal(models.Model):
    style = models.ForeignKey(Style, on_delete=models.CASCADE)
    part = models.ForeignKey(MetalPart, on_delete=models.PROTECT)
    qty_req = models.PositiveIntegerField(null=True, blank=True)
    weight = models.PositiveIntegerField(null=True, blank=True)
    metal_type = models.ForeignKey(MetalType, on_delete=models.PROTECT, blank=True)
    def __str__(self):
        return f"{self.style} - {self.part}"

class Stone(models.Model):
    lot_num = models.IntegerField(unique=True)
    job = models.ForeignKey(Job, on_delete=models.CASCADE, null=True)
    stone_type = models.ForeignKey(StoneType, on_delete=models.PROTECT, null=True)
    size = models.CharField(blank=True, default="")
    weight = models.PositiveIntegerField(blank=True, default=1)

class StyleStone(models.Model):
    style = models.ForeignKey(Style, on_delete=models.CASCADE)
    stone_type = models.ForeignKey(StoneType, on_delete=models.PROTECT, null=True)
    stone_shape = models.ForeignKey(StoneShape, on_delete=models.PROTECT, null=True)
    stone_size = models.CharField(max_length=10, blank=True, null=True)
    qty_req = models.PositiveIntegerField()
    def __str__(self):
        return f"{self.stone_size} {self.stone_shape} {self.stone_type} - {self.style}"

class FindingType(models.Model):
    name = models.CharField(max_length=80, unique=True)
    unit = models.CharField(max_length=10, default="pcs")

class FindingStock(models.Model):
    finding_type = models.ForeignKey(FindingType,on_delete=models.CASCADE)
    name = models.CharField(max_length=50, unique=True)
    qty_in_stock = models.DecimalField(max_digits=12, decimal_places=3, default=0)

class Activity(models.Model):
    name = models.CharField(max_length=80)
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    start = models.DateTimeField(default=timezone.now)
    end = models.DateTimeField(blank=True, null=True)
    job = models.ForeignKey(Job, blank=True, null=True, on_delete=models.CASCADE)
    active = models.BooleanField(default=True)
    @property
    def duration(self):
        if self.end:
            duration = (self.end - self.start).total_seconds()/3600
            return duration

    @property
    def duration_hours(self):
        if self.end:
            duration = (self.end - self.start).total_seconds()//3600
            return int(duration)
        
    @property 
    def duration_min(self):
        if self.end:
            duration = (self.end - self.start).total_seconds()%3600
            duration = duration//60
            return int(duration)

    def __str__(self):
        return (str(self.job.job_num) + " " + str(self.name))
    
class TimeClock(models.Model):
    clock_in = models.DateTimeField(null=True)
    clock_out = models.DateTimeField(null=True)
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)