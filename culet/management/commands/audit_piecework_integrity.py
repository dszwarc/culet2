from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Q

from culet.models import Activity, Job, PieceworkMemo, PieceworkMemoLine


class Command(BaseCommand):
    help = "Audit line-level piecework integrity without changing data."

    def handle(self, *args, **options):
        counts = Counter()

        duplicate_open_jobs = (
            PieceworkMemoLine.objects.filter(returned_at__isnull=True)
            .values("job_id")
            .annotate(line_count=Count("id"))
            .filter(line_count__gt=1)
            .order_by("job_id")
        )
        for row in duplicate_open_jobs:
            lines = list(
                PieceworkMemoLine.objects.filter(
                    job_id=row["job_id"],
                    returned_at__isnull=True,
                ).values_list("id", "memo_id", "memo__memo_num")
            )
            counts["multiple_open_lines"] += 1
            self._serious(
                "MULTIPLE OPEN LINES",
                f"job_id={row['job_id']} lines={lines}",
            )

        stale_false_lines = PieceworkMemoLine.objects.filter(
            returned_at__isnull=True,
            job__is_piecework=False,
        ).select_related("job", "memo")
        for line in stale_false_lines:
            counts["open_line_stale_false"] += 1
            self._serious(
                "OPEN LINE / FALSE FLAG",
                self._line_identifier(line),
            )

        open_piecework_job_ids = PieceworkMemoLine.objects.filter(
            returned_at__isnull=True,
        ).values("job_id")
        stale_true_jobs = Job.objects.filter(is_piecework=True).exclude(
            pk__in=open_piecework_job_ids,
        )
        for job in stale_true_jobs:
            counts["stale_true_without_line"] += 1
            self._serious(
                "TRUE FLAG / NO OPEN LINE",
                self._job_identifier(job),
            )

        fully_returned_open_memos = (
            PieceworkMemo.objects.filter(returned_at__isnull=True)
            .annotate(
                total_lines=Count("lines"),
                open_lines=Count(
                    "lines",
                    filter=Q(lines__returned_at__isnull=True),
                ),
            )
            .filter(total_lines__gt=0, open_lines=0)
        )
        for memo in fully_returned_open_memos:
            counts["fully_returned_memo_not_closed"] += 1
            self._serious(
                "FULLY RETURNED MEMO STILL OPEN",
                f"memo_id={memo.pk} memo_num={memo.memo_num}",
            )

        completed_memo_open_lines = PieceworkMemoLine.objects.filter(
            returned_at__isnull=True,
            memo__returned_at__isnull=False,
        ).select_related("job", "memo")
        for line in completed_memo_open_lines:
            counts["open_line_on_completed_memo"] += 1
            self._serious(
                "OPEN LINE ON COMPLETED MEMO",
                self._line_identifier(line),
            )

        return_employee_without_time = PieceworkMemoLine.objects.filter(
            returned_at__isnull=True,
            returned_by__isnull=False,
        ).select_related("job", "memo")
        for line in return_employee_without_time:
            counts["returned_by_without_returned_at"] += 1
            self._serious(
                "RETURN EMPLOYEE WITHOUT RETURN TIME",
                self._line_identifier(line),
            )

        returned_without_employee = PieceworkMemoLine.objects.filter(
            returned_at__isnull=False,
            returned_by__isnull=True,
        ).select_related("job", "memo")
        for line in returned_without_employee:
            if line.memo.returned_at and line.memo.returned_by_id is None:
                counts["historical_return_missing_employee"] += 1
                self.stdout.write(
                    "HISTORICAL RETURN MISSING EMPLOYEE: "
                    + self._line_identifier(line)
                )
            else:
                counts["returned_line_missing_employee"] += 1
                self._serious(
                    "RETURNED LINE MISSING EMPLOYEE",
                    self._line_identifier(line),
                )

        shipped_open_lines = PieceworkMemoLine.objects.filter(
            returned_at__isnull=True,
            job__shipped=True,
        ).select_related("job", "memo")
        for line in shipped_open_lines:
            counts["open_piecework_shipped"] += 1
            self._serious(
                "OPEN PIECEWORK JOB SHIPPED",
                self._line_identifier(line),
            )

        active_open_lines = (
            PieceworkMemoLine.objects.filter(
                returned_at__isnull=True,
                job__activity__active=True,
                job__activity__end__isnull=True,
            )
            .select_related("job", "memo")
            .distinct()
        )
        for line in active_open_lines:
            activity_ids = list(
                Activity.objects.filter(
                    job_id=line.job_id,
                    active=True,
                    end__isnull=True,
                ).values_list("id", flat=True)
            )
            counts["open_piecework_active_work"] += 1
            self._serious(
                "OPEN PIECEWORK JOB HAS ACTIVE WORK",
                f"{self._line_identifier(line)} activity_ids={activity_ids}",
            )

        self.stdout.write(
            "DUPLICATE PIECEWORK ACTIVITIES: not reliably auditable because "
            "Activity has no PieceworkMemoLine or memo foreign key."
        )

        serious_keys = (
            "multiple_open_lines",
            "open_line_stale_false",
            "stale_true_without_line",
            "fully_returned_memo_not_closed",
            "open_line_on_completed_memo",
            "returned_by_without_returned_at",
            "returned_line_missing_employee",
            "open_piecework_shipped",
            "open_piecework_active_work",
        )
        serious_total = sum(counts[key] for key in serious_keys)

        self.stdout.write("SUMMARY")
        for key in serious_keys + ("historical_return_missing_employee",):
            self.stdout.write(f"{key}: {counts[key]}")
        self.stdout.write(f"serious_violations: {serious_total}")
        self.stdout.write("Mode: READ ONLY")

        if serious_total:
            raise CommandError(
                f"Piecework integrity audit found {serious_total} serious violation(s)."
            )

    def _serious(self, label, detail):
        self.stdout.write(f"{label}: {detail}")

    @staticmethod
    def _job_identifier(job):
        return (
            f"job_id={job.pk} barcode={job.barcode!r} "
            f"stock_num={job.stock_num!r}"
        )

    def _line_identifier(self, line):
        return (
            f"line_id={line.pk} memo_id={line.memo_id} "
            f"memo_num={line.memo.memo_num!r} "
            f"{self._job_identifier(line.job)}"
        )
