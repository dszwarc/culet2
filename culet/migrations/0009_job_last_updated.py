# Generated by Django 5.1.1 on 2024-10-31 17:14

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('culet', '0008_job_job_num'),
    ]

    operations = [
        migrations.AddField(
            model_name='job',
            name='last_updated',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
    ]
