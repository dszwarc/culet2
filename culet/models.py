from django.db import models
from django.utils import timezone
from django.urls import reverse
from datetime import date
from django.contrib.auth.models import User
# Create your models here.

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
    active = models.BooleanField(default=False)
    style = models.ForeignKey(Style, on_delete=models.CASCADE)
    created = models.DateTimeField(default=timezone.now, editable = False)
    due = models.DateField(default=timezone.now)
    last_updated = models.DateTimeField(auto_now=True)
    assigned_to = models.ForeignKey(Employee, on_delete=models.CASCADE, null=True)
    
    @property
    def is_past_due(self):
        return date.today() > self.due

    def __str__(self):
        return str(self.job_num).zfill(5)
    
    def get_absolute_url(self):
        return reverse('culet:job_detail', kwargs={'pk': self.pk})

class Activity(models.Model):
    name = models.CharField(max_length=80)
    start = models.DateTimeField(default=timezone.now)
    end = models.DateTimeField(blank=True, null=True)
    job = models.ForeignKey(Job, blank=True, null=True, on_delete=models.CASCADE)
    active = models.BooleanField(default=True)

    def __str__(self):
        return (str(self.job.job_num) + " " + str(self.name))