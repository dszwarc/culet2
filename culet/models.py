from django.db import models

# Create your models here.
class Job(models.Model):
    name = models.CharField(max_length=80)
    customer = models.CharField(max_length=80)
    job_num = models.IntegerField