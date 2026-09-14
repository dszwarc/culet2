import csv
from collections import Counter
from contextlib import nullcontext

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from culet.models import ActivityStep, Job, PieceworkMemo, PieceworkMemoLine
from culet.piecework_activities import classify_piecework_activity, create_piecework_activity


class Command(BaseCommand):
    help = "Audit/backfill completed piecework periods; ambiguous records are never changed."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Read only; do not create Activities or links.")
        parser.add_argument("--output", help="Create an audit CSV; refuses to overwrite an existing file.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        with connection.cursor() as cursor:
            columns = connection.introspection.get_table_description(cursor, PieceworkMemoLine._meta.db_table)
        has_link = any(column.name == "activity_id" for column in columns)
        if not has_link and not dry_run:
            raise CommandError("Apply migration 0092 before running a repair. --dry-run works before migration.")
        fields = ["memo_id", "memo_number", "line_id", "job_id", "stock_number", "barcode",
                  "employee_id", "employee", "line_created_at", "start", "returned_at",
                  "existing_activity_ids", "new_activity_id", "action", "reason"]
        output = None
        try:
            if options["output"]:
                output = open(options["output"], "x", newline="", encoding="utf-8")
            writer = csv.DictWriter(output, fieldnames=fields) if output else None
            if writer:
                writer.writeheader()
            counts = Counter()
            lines = PieceworkMemoLine.objects.filter(returned_at__isnull=False).order_by("pk")
            for line_id, memo_id in lines.values_list("pk", "memo_id").iterator():
                # Same lock order as returns. Each committed line is safe to retry
                # if a later line or CSV write fails.
                with (nullcontext() if dry_run else transaction.atomic()):
                    if not dry_run:
                        PieceworkMemo.objects.select_for_update().get(pk=memo_id)
                    query = PieceworkMemoLine.objects.all()
                    if not has_link:
                        query = query.defer("activity")
                    if not dry_run:
                        query = query.select_for_update()
                    line = query.get(pk=line_id)
                    if not dry_run:
                        Job.objects.select_for_update().get(pk=line.job_id)
                    step = ActivityStep.objects.filter(code="piecework").first()
                    action, activities, reason = classify_piecework_activity(line, step, has_activity_link=has_link)
                    existing_ids = ";".join(str(a.pk) for a in activities)
                    new_id = ""
                    if not dry_run and action in ("existing", "would_create"):
                        activity = activities[0] if activities else create_piecework_activity(line, step)
                        if action == "would_create":
                            action, new_id = "created", activity.pk
                        if line.activity_id != activity.pk:
                            line.activity = activity
                            line.save(update_fields=["activity"])
                            counts["linked"] += 1
                    row = dict(zip(fields, [
                        line.memo_id, line.memo.memo_num, line.pk, line.job_id,
                        line.job.stock_num, line.job.barcode, line.memo.assigned_to_id,
                        str(line.memo.assigned_to), "", line.memo.created_at, line.returned_at,
                        existing_ids, new_id, action, reason,
                    ]))
                counts["inspected"] += 1
                counts[action] += 1
                if writer:
                    writer.writerow(row)
            for key in ("inspected", "existing", "would_create", "created", "ambiguous", "skipped", "duplicate_suspected", "linked"):
                self.stdout.write(f"{key}: {counts[key]}")
        except OSError as exc:
            raise CommandError(str(exc)) from exc
        finally:
            if output:
                output.close()
