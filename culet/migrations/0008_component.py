# Generated by Django 4.2.16 on 2025-03-27 18:25

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('culet', '0007_componenttype'),
    ]

    operations = [
        migrations.CreateModel(
            name='Component',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('comp_type', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='culet.componenttype')),
                ('job', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to='culet.job')),
            ],
        ),
    ]
