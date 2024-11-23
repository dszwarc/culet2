from django.db import models
from django.utils import timezone
from django.urls import reverse
from datetime import datetime
# Create your models here.

class Style(models.Model):
    
    product_choices = [
        ("RG", "Ring"),
        ("BR", "Bracelet"),
        ("NK", "Necklace"),
    ]

    name = models.CharField(max_length=80, unique=True)
    product = models.CharField(
        max_length = 2,
        choices = product_choices,
        default = "RG"
        )
    def __str__(self):
        return str(self.name)

class Department(models.Model):
    name = models.CharField(max_length=80)

class Job(models.Model):

    name = models.CharField(max_length=80)
    customer = models.CharField(max_length=80)
    job_num = models.IntegerField(default=0)
    active = models.BooleanField(default=False)
    # style = models.CharField(
    #     max_length = 2,
    #     choices = {
    #         RING : "Ring",
    #         BRACELET : "Bracelet",
    #         NECKLACE : "Necklace",
    #         },
    #     default = RING
    #     )

    style = models.ForeignKey(Style, on_delete=models.CASCADE)
    created = models.DateTimeField(default=timezone.now, editable = False)
    due = models.DateField(default=timezone.now)
    last_updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return str(self.job_num).zfill(5)
    
    def get_absolute_url(self):
        return reverse('culet:job_detail', kwargs={'pk': self.pk})
    # def get_absolute_url(self):
    #     return reverse('culet:job_detail', kwargs={'pk': self.pk})

class Activity(models.Model):
    name = models.CharField(max_length=80)
    start = models.DateTimeField()
    end = models.DateTimeField(blank=True, null=True)
    job = models.ForeignKey(Job, blank=True, null=True, on_delete=models.CASCADE)

    def __str__(self):
        return (str(self.job.job_num) + " " + str(self.name))