# Generated for the legacy Culet style-metal import.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("culet", "0076_employee_must_change_password"),
    ]

    operations = [
        migrations.AlterField(
            model_name="stylemetal",
            name="part",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                to="culet.metalpart",
            ),
        ),
        migrations.AlterField(
            model_name="stylemetal",
            name="metal_type",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                to="culet.metaltype",
            ),
        ),
    ]