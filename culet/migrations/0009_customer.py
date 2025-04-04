# Generated by Django 4.2.16 on 2025-04-04 17:45

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('culet', '0008_component'),
    ]

    operations = [
        migrations.CreateModel(
            name='Customer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=80, unique=True)),
                ('address', models.CharField(max_length=150)),
                ('email', models.EmailField(max_length=200)),
                ('phone', models.CharField(max_length=12)),
                ('number', models.IntegerField(max_length=15)),
            ],
        ),
    ]
