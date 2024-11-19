# Generated by Django 4.2.16 on 2024-11-15 17:26

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('culet', '0012_alter_style_product'),
    ]

    operations = [
        migrations.CreateModel(
            name='Department',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=80)),
            ],
        ),
        migrations.AlterField(
            model_name='style',
            name='name',
            field=models.CharField(max_length=80, unique=True),
        ),
        migrations.CreateModel(
            name='Activity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=80)),
                ('start', models.DateTimeField()),
                ('end', models.DateTimeField()),
                ('job', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='culet.job')),
            ],
        ),
    ]
