from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('culet', '0031_metalreceiptline_metal_lot'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                DROP INDEX IF EXISTS culet_metallot_lot_num_ebcf5505_like;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),

        migrations.CreateModel(
            name='MetalVendorLot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('lot_num', models.CharField(max_length=50, unique=True)),
                ('vendor', models.CharField(blank=True, max_length=120)),
                ('received_at', models.DateTimeField(default=django.utils.timezone.now)),
            ],
        ),
        migrations.CreateModel(
            name='Vendor',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=80, unique=True)),
                ('address', models.CharField(max_length=150)),
                ('email', models.EmailField(max_length=200)),
                ('phone', models.CharField(max_length=12)),
                ('number', models.IntegerField(blank=True, null=True)),
            ],
        ),
        migrations.RemoveConstraint(
            model_name='metallot',
            name='uniq_vendor_lot_part',
        ),
        migrations.RemoveConstraint(
            model_name='metalreceiptline',
            name='unique_receipt_line_per_lot',
        ),
        migrations.RemoveField(
            model_name='metallot',
            name='received_at',
        ),
        migrations.AlterField(
            model_name='metalreceiptline',
            name='metal_lot',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='receipt_lines', to='culet.metallot'),
        ),
        migrations.AlterField(
            model_name='metallot',
            name='vendor_lot',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='part_lots', to='culet.metalvendorlot'),
        ),
        migrations.AlterField(
            model_name='metalreceiptline',
            name='vendor_lot',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='receipt_lines', to='culet.metalvendorlot'),
        ),
        migrations.AddConstraint(
            model_name='metallot',
            constraint=models.UniqueConstraint(fields=('vendor_lot', 'part'), name='uniq_part_per_vendor_lot'),
        ),
        migrations.AddConstraint(
            model_name='metalreceiptline',
            constraint=models.UniqueConstraint(fields=('receipt', 'vendor_lot', 'part'), name='uniq_receipt_vendorlot_part'),
        ),
    ]