# Generated by Django 4.2.16 on 2025-02-18 15:39

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('culet', '0004_job_assigned_to'),
    ]

    operations = [
        migrations.AlterField(
            model_name='job',
            name='assigned_to',
            field=models.OneToOneField(null=True, on_delete=django.db.models.deletion.CASCADE, to='culet.employee'),
        ),
    ]
