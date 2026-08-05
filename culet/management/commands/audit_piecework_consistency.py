from collections import Counter, defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from culet.models import Job, PieceworkMemo, PieceworkMemoLine


class Command(BaseCommand):
    help = "Report and optionally repair unambiguous piecework inconsistencies."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix",
            action="store_true",
            help="Repair only records with one unambiguous open memo.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        fix = options["fix"]
        counts = Counter()

        open_memos = list(
            PieceworkMemo.objects.filter(returned_at__isnull=True)
            .select_related("assigned_to__user")
            .prefetch_related("lines__job")
            .order_by("pk")
        )
        open_lines = list(
            PieceworkMemoLine.objects.filter(memo__returned_at__isnull=True)
            .select_related("memo__assigned_to__user", "job")
            .order_by("memo_id", "job_id", "pk")
        )

        lines_by_job = defaultdict(list)
        lines_by_pair = defaultdict(list)
        for line in open_lines:
            lines_by_job[line.job_id].append(line)
            lines_by_pair[(line.memo_id, line.job_id)].append(line)

        for memo in open_memos:
            if not memo.lines.all():
                counts["open_memos_without_lines"] += 1
                self._report(f"OPEN EMPTY MEMO: {memo.memo_num}")

        duplicate_line_ids = set()
        for (memo_id, job_id), lines in lines_by_pair.items():
            if len(lines) < 2:
                continue
            counts["duplicate_memo_lines"] += len(lines) - 1
            duplicate_line_ids.update(line.pk for line in lines[1:])
            self._report(
                f"DUPLICATE LINES: memo_id={memo_id}, job_id={job_id}, "
                f"line_ids={[line.pk for line in lines]}"
            )

        ambiguous_job_ids = {
            job_id
            for job_id, lines in lines_by_job.items()
            if len({line.memo_id for line in lines}) > 1
        }
        for job_id in sorted(ambiguous_job_ids):
            lines = lines_by_job[job_id]
            counts["multiple_open_memos"] += 1
            self._report(
                f"MANUAL REVIEW: job_id={job_id} is on open memos "
                f"{[line.memo.memo_num for line in lines]}"
            )

        for job_id, lines in lines_by_job.items():
            job = lines[0].job
            memo = lines[0].memo
            if not job.is_piecework:
                counts["open_lines_false_flag"] += 1
                self._report(f"FALSE FLAG: job {job} on {memo.memo_num}")
            if job.assigned_to_id != memo.assigned_to_id or job.holder_id != memo.assigned_to_id:
                counts["employee_conflicts"] += 1
                self._report(
                    f"EMPLOYEE CONFLICT: job {job} assigned_to_id={job.assigned_to_id}, "
                    f"holder_id={job.holder_id}, memo employee_id={memo.assigned_to_id}"
                )
            if (
                not job.is_piecework
                or job.assigned_to_id != memo.assigned_to_id
                or not job.active
                or job.shipped
            ):
                counts["missing_from_legacy_my_piecework"] += 1
                self._report(
                    f"LEGACY VIEW MISS: job {job} would have been hidden from "
                    f"{memo.assigned_to}'s My Piecework page"
                )

            if fix and job_id not in ambiguous_job_ids:
                changes = []
                if not job.is_piecework:
                    job.is_piecework = True
                    changes.append("is_piecework=True")
                if job.piecework_assigned_at is None:
                    job.piecework_assigned_at = memo.created_at
                    changes.append(f"piecework_assigned_at={memo.created_at.isoformat()}")
                if job.assigned_to_id != memo.assigned_to_id:
                    job.assigned_to_id = memo.assigned_to_id
                    changes.append(f"assigned_to_id={memo.assigned_to_id}")
                if job.holder_id != memo.assigned_to_id:
                    job.holder_id = memo.assigned_to_id
                    changes.append(f"holder_id={memo.assigned_to_id}")
                if changes:
                    job.save(update_fields=[
                        "is_piecework", "piecework_assigned_at", "assigned_to",
                        "holder", "last_updated"
                    ])
                    counts["jobs_repaired"] += 1
                    self._report(f"FIXED job {job}: {', '.join(changes)}")

        stale_jobs = Job.objects.filter(is_piecework=True).exclude(
            pieceworkmemoline__memo__returned_at__isnull=True
        ).distinct()
        for job in stale_jobs:
            counts["flags_without_open_memo"] += 1
            returned_memos = PieceworkMemo.objects.filter(
                lines__job=job, returned_at__isnull=False
            ).exists()
            if returned_memos:
                counts["returned_memo_stale_flags"] += 1
            self._report(f"STALE FLAG: job {job} has no open piecework memo")
            if fix:
                job.is_piecework = False
                job.piecework_assigned_at = None
                job.save(update_fields=[
                    "is_piecework", "piecework_assigned_at", "last_updated"
                ])
                counts["jobs_repaired"] += 1
                self._report(f"FIXED job {job}: cleared stale piecework state")

        if fix and duplicate_line_ids:
            deleted, _ = PieceworkMemoLine.objects.filter(
                pk__in=duplicate_line_ids
            ).delete()
            counts["duplicate_lines_removed"] = deleted
            self._report(f"FIXED: removed {deleted} duplicate memo line(s)")

        self.stdout.write("SUMMARY")
        keys = (
            "open_memos_without_lines", "open_lines_false_flag",
            "employee_conflicts", "flags_without_open_memo",
            "multiple_open_memos", "returned_memo_stale_flags",
            "duplicate_memo_lines", "missing_from_legacy_my_piecework",
            "jobs_repaired", "duplicate_lines_removed",
        )
        for key in keys:
            self.stdout.write(f"{key}: {counts[key]}")
        self.stdout.write("Mode: FIX" if fix else "Mode: REPORT ONLY (no data changed)")

    def _report(self, message):
        self.stdout.write(message)
