# Generated by Django 4.2.16 on 2024-11-19 19:38

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('culet', '0013_department_alter_style_name_activity'),
    ]

    operations = [
        migrations.AddField(
            model_name='job',
            name='active',
            field=models.BooleanField(default=False),
        ),
    ]
