# Generated manually on 2026-07-06

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("culet", "0070_activity_is_piecework"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="employee",
            name="department",
        ),
        migrations.RemoveField(
            model_name="employee",
            name="role",
        ),
        migrations.RenameField(
            model_name="employee",
            old_name="department_fk",
            new_name="department",
        ),
        migrations.RenameField(
            model_name="employee",
            old_name="role_fk",
            new_name="role",
        ),
    ]