# Generated by Django 5.1.1 on 2024-10-10 20:33

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('culet', '0003_job_name'),
    ]

    operations = [
        migrations.RenameField(
            model_name='job',
            old_name='customer_name',
            new_name='customer',
        ),
    ]
