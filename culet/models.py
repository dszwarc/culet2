from django.db import models

# Create your models here.
class Job(models.Model):

    RING = "RG"
    BRACELET = "BR"
    NECKLACE = "NK"
    

    name = models.CharField(max_length=80)
    customer = models.CharField(max_length=80)
    job_num = models.IntegerField
    style = models.CharField(
        max_length = 2,
        choices = {
            RING : "Ring",
            BRACELET : "Bracelet",
            NECKLACE : "Necklace",
            },
        default = RING
        )
        