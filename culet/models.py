from django.db import models
from django.utils import timezone
from django.urls import reverse
from datetime import date, timedelta
from django.contrib.auth.models import User
# Create your models here.

class ProductChoices(models.Model):
    name = models.CharField(max_length=80, unique=True)

class Customer(models.Model):
    name = models.CharField(max_length=80,unique=True)
    address = models.CharField(max_length=150)
    email = models.EmailField(max_length=200)
    phone = models.CharField(max_length=12)
    number = models.IntegerField(unique=True)

class ComponentType(models.Model):
    name = models.CharField(max_length=80, default="Stone", unique=True)

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
    
    product_choices = [
        ("RG", "Ring"),
        ("BR", "Bracelet"),
        ("NK", "Necklace"),
    ]

    name = models.CharField(max_length=50, unique=True)
    product = models.CharField(
        max_length = 2,
        choices = product_choices,
        default = "RG"
        )
    def __str__(self):
        return str(self.name)

class Job(models.Model):

    name = models.CharField(max_length=80,default="N/A")
    customer = models.CharField(max_length=80)
    job_num = models.IntegerField(default=0, unique=True)
    customer_ref_num = models.IntegerField(null=True, unique=True)
    active = models.BooleanField(default=True)
    shipped = models.BooleanField(default=False)
    in_work = models.BooleanField(default=False)
    style = models.ForeignKey(Style, on_delete=models.CASCADE)
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

class Component(models.Model):
    component_id = models.CharField(max_length=100, unique=True)
    comp_type = models.ForeignKey(ComponentType, on_delete=models.CASCADE)
    job = models.OneToOneField(Job, on_delete=models.CASCADE)
    in_stock = models.BooleanField(default=True)

    def __str__(self):
        return 

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


class ComponentType(models.Model):

    type_choices = [
        ("ST", "stone"),
        ("CS", "casting"),
        ("CH", "chain"),
        ("OT","other"),
    ]

    name = models.TextField
    types = models.CharField(
        max_length = 2,
        choices = type_choices,
        default = "OT"
        )

# class Component(models.Model):
#     reference_num = models.IntegerField()
#     Job = models.ForeignKey(Job, on_delete=models.CASCADE)
#     comp_type = models.ManyToManyField(ComponentType)
    
