from django.db import models
from django.utils import timezone
from django.urls import reverse
# Create your models here.

class Style(models.Model):
    
    RING = "RG"
    BRACELET = "BR"
    NECKLACE = "NK"
    
    name=models.CharField(max_length=80)
    product = models.CharField(
        max_length = 2,
        choices = {
            RING : "Ring",
            BRACELET : "Bracelet",
            NECKLACE : "Necklace",
            },
        default = RING
        )


class Job(models.Model):

    RING = "RG"
    BRACELET = "BR"
    NECKLACE = "NK"
    

    name = models.CharField(max_length=80)
    customer = models.CharField(max_length=80)
    job_num = models.IntegerField(default=0)
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