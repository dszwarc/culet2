import django.db.models.deletion
from django.db import migrations, models


def backfill_and_validate_piecework_lines(apps, schema_editor):
    PieceworkMemo = apps.get_model("culet", "PieceworkMemo")
    PieceworkMemoLine = apps.get_model("culet", "PieceworkMemoLine")

    open_lines = list(
        PieceworkMemoLine.objects.filter(memo__returned_at__isnull=True)
        .values(
            "id",
            "job_id",
            "job__barcode",
            "job__stock_num",
            "memo_id",
            "memo__memo_num",
        )
        .order_by("job_id", "memo_id", "id")
    )
    lines_by_job = {}
    for line in open_lines:
        lines_by_job.setdefault(line["job_id"], []).append(line)

    conflicts = []
    for job_id, lines in lines_by_job.items():
        if len(lines) < 2:
            continue
        first = lines[0]
        line_details = ", ".join(
            f"line_id={line['id']} memo_id={line['memo_id']} "
            f"memo_num={line['memo__memo_num']!r}"
            for line in lines
        )
        conflicts.append(
            f"job_id={job_id} barcode={first['job__barcode']!r} "
            f"stock_num={first['job__stock_num']!r}: {line_details}"
        )

    if conflicts:
        raise RuntimeError(
            "Cannot add unique_open_piecework_line_per_job because jobs have "
            "multiple open piecework lines. Resolve these records and rerun "
            "the migration:\n" + "\n".join(conflicts)
        )

    completed_memos = PieceworkMemo.objects.filter(returned_at__isnull=False)
    for memo in completed_memos.iterator():
        updates = {"returned_at": memo.returned_at}
        if memo.returned_by_id is not None:
            updates["returned_by_id"] = memo.returned_by_id
        PieceworkMemoLine.objects.filter(memo_id=memo.pk).update(**updates)


class Migration(migrations.Migration):

    dependencies = [
        ("culet", "0089_piecework_line_unique_constraint"),
    ]

    operations = [
        migrations.AddField(
            model_name="pieceworkmemoline",
            name="returned_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pieceworkmemoline",
            name="returned_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="piecework_memo_lines_returned",
                to="culet.employee",
            ),
        ),
        migrations.RunPython(
            backfill_and_validate_piecework_lines,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="pieceworkmemoline",
            constraint=models.UniqueConstraint(
                condition=models.Q(returned_at__isnull=True),
                fields=("job",),
                name="unique_open_piecework_line_per_job",
            ),
        ),
    ]
