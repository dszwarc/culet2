import csv
from datetime import date, datetime, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Exists, Max, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from culet.models import Activity, Employee, Job, JobMovement, MovementType
from culet.services import move_job


CSV_FIELDS = [
    "job_id",
    "stock_num",
    "barcode",
    "previous_assigned_to_id",
    "previous_assigned_to_name",
    "previous_holder_id",
    "previous_holder_name",
    "target_manager_id",
    "target_manager_name",
    "last_activity_at",
    "last_movement_at",
    "status",
    "result",
    "message",
]


class Command(BaseCommand):
    help = "Reset inactive legacy job assignments to an existing manager."

    def add_arguments(self, parser):
        parser.add_argument("--manager-id", required=True, type=int)
        parser.add_argument("--launch-date", required=True)
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Analyze and report without changing the database.",
        )
        parser.add_argument(
            "--output",
            help="Optional CSV path for the reviewed jobs and results.",
        )

    def handle(self, *args, **options):
        manager = self._get_manager(options["manager_id"])
        launch_cutoff = self._parse_launch_cutoff(options["launch_date"])
        movement_types = self._get_movement_types()
        dry_run = options["dry_run"]
        now = timezone.now()
        inactive_cutoff = now - timedelta(days=7)

        active_unshipped_count = Job.objects.filter(
            active=True,
            shipped=False,
        ).count()
        inactive_before_ids = set(
            self._inactive_jobs(inactive_cutoff).values_list("pk", flat=True)
        )
        candidates = list(self._candidate_jobs(launch_cutoff))
        candidate_ids = {job.pk for job in candidates}
        inactive_overlap = len(candidate_ids & inactive_before_ids)
        csv_rows = [self._csv_row(job, manager) for job in candidates]

        if dry_run:
            for row in csv_rows:
                row["status"] = "DRY_RUN"
                row["result"] = "WOULD_RESET"
            self._write_report(
                dry_run=True,
                launch_cutoff=launch_cutoff,
                manager=manager,
                inactive_cutoff=inactive_cutoff,
                active_unshipped_count=active_unshipped_count,
                eligible_count=len(candidates),
                inactive_before=len(inactive_before_ids),
                inactive_overlap=inactive_overlap,
            )
            self._write_csv(options.get("output"), csv_rows)
            return

        reset_count = 0
        no_longer_eligible_count = 0
        error_count = 0

        for job, row in zip(candidates, csv_rows):
            try:
                result = self._reset_job(
                    job_id=job.pk,
                    manager=manager,
                    launch_cutoff=launch_cutoff,
                    assignment_type=movement_types["assigned"],
                    holder_type=movement_types["received"],
                )
            except Exception as exc:  # Keep unrelated eligible jobs moving.
                error_count += 1
                row["status"] = "ERROR"
                row["result"] = "SKIPPED_ERROR"
                row["message"] = str(exc)
                self.stderr.write(
                    self.style.ERROR(f"Job {job.pk} skipped: {exc}")
                )
                continue

            if result == "SKIPPED_NO_LONGER_ELIGIBLE":
                no_longer_eligible_count += 1
                row["status"] = "SKIPPED"
                row["result"] = result
                row["message"] = "Eligibility changed before processing."
            else:
                reset_count += 1
                row["status"] = "RESET"
                row["result"] = "RESET"

        inactive_after = self._inactive_jobs(timezone.now() - timedelta(days=7)).count()
        self._write_execution_summary(
            eligible_count=len(candidates),
            reset_count=reset_count,
            no_longer_eligible_count=no_longer_eligible_count,
            error_count=error_count,
            inactive_before=len(inactive_before_ids),
            inactive_after=inactive_after,
        )
        self._write_csv(options.get("output"), csv_rows)

    def _get_manager(self, manager_id):
        try:
            return Employee.objects.select_related("user").get(pk=manager_id)
        except Employee.DoesNotExist as exc:
            raise CommandError(
                f"No Employee was found with id {manager_id}."
            ) from exc

    def _parse_launch_cutoff(self, value):
        try:
            launch_date = date.fromisoformat(value)
        except ValueError as exc:
            raise CommandError("--launch-date must use YYYY-MM-DD format.") from exc
        return timezone.make_aware(
            datetime.combine(launch_date, time.min),
            timezone.get_current_timezone(),
        )

    def _get_movement_types(self):
        movement_types = {
            movement_type.code: movement_type
            for movement_type in MovementType.objects.filter(
                code__in=["assigned", "received"]
            )
        }
        missing = {"assigned", "received"} - movement_types.keys()
        if missing:
            raise CommandError(
                "Missing required MovementType code(s): " + ", ".join(sorted(missing))
            )
        if (
            movement_types["assigned"].job_field
            != MovementType.JobField.ASSIGNED_TO
            or movement_types["received"].job_field
            != MovementType.JobField.HOLDER
        ):
            raise CommandError(
                "Movement types assigned/received do not target the expected job fields."
            )
        return movement_types

    def _recent_activity_exists(self, cutoff):
        return Activity.objects.filter(job_id=OuterRef("pk")).filter(
            Q(start__gte=cutoff) | Q(end__gte=cutoff)
        )

    def _candidate_jobs(self, cutoff):
        last_activity_at = (
            Activity.objects.filter(job_id=OuterRef("pk"))
            .annotate(activity_at=Coalesce("end", "start"))
            .order_by("-activity_at")
            .values("activity_at")[:1]
        )
        last_movement_at = (
            JobMovement.objects.filter(job_id=OuterRef("pk"))
            .order_by("-created_at")
            .values("created_at")[:1]
        )
        return (
            Job.objects.filter(active=True, shipped=False)
            .annotate(
                has_recent_activity=Exists(self._recent_activity_exists(cutoff)),
                has_recent_movement=Exists(
                    JobMovement.objects.filter(
                        job_id=OuterRef("pk"),
                        created_at__gte=cutoff,
                    )
                ),
                last_activity_at=Subquery(last_activity_at),
                last_movement_at=Subquery(last_movement_at),
            )
            .filter(has_recent_activity=False, has_recent_movement=False)
            .select_related(
                "assigned_to__user",
                "holder__user",
            )
            .order_by("pk")
        )

    def _inactive_jobs(self, cutoff):
        return (
            Job.objects.filter(active=True, shipped=False)
            .annotate(last_movement=Max("movements__created_at"))
            .annotate(inactive_since=Coalesce("last_movement", "created"))
            .filter(inactive_since__lt=cutoff)
        )

    def _is_still_eligible(self, job, cutoff):
        return (
            job.active
            and not job.shipped
            and not Activity.objects.filter(job_id=job.pk).filter(
                Q(start__gte=cutoff) | Q(end__gte=cutoff)
            ).exists()
            and not JobMovement.objects.filter(
                job_id=job.pk,
                created_at__gte=cutoff,
            ).exists()
        )

    @transaction.atomic
    def _reset_job(
        self,
        *,
        job_id,
        manager,
        launch_cutoff,
        assignment_type,
        holder_type,
    ):
        job = Job.objects.select_for_update(of=("self",)).get(pk=job_id)
        if not self._is_still_eligible(job, launch_cutoff):
            return "SKIPPED_NO_LONGER_ELIGIBLE"

        job, assignment_movement = move_job(
            job=job,
            movement_type=assignment_type,
            to_employee=manager,
            performed_by=manager,
        )
        job, holder_movement = move_job(
            job=job,
            movement_type=holder_type,
            to_employee=manager,
            performed_by=manager,
        )

        if assignment_movement is None and holder_movement is None:
            JobMovement.objects.create(
                job=job,
                movement_type=assignment_type,
                from_employee=manager,
                to_employee=manager,
                performed_by=manager,
            )
        return "RESET"

    def _csv_row(self, job, manager):
        return {
            "job_id": job.pk,
            "stock_num": job.stock_num or "",
            "barcode": job.barcode or "",
            "previous_assigned_to_id": job.assigned_to_id or "",
            "previous_assigned_to_name": str(job.assigned_to) if job.assigned_to else "",
            "previous_holder_id": job.holder_id or "",
            "previous_holder_name": str(job.holder) if job.holder else "",
            "target_manager_id": manager.pk,
            "target_manager_name": str(manager),
            "last_activity_at": (
                job.last_activity_at.isoformat() if job.last_activity_at else ""
            ),
            "last_movement_at": (
                job.last_movement_at.isoformat() if job.last_movement_at else ""
            ),
            "status": "PENDING",
            "result": "",
            "message": "",
        }

    def _write_csv(self, output_path, rows):
        if not output_path:
            return
        try:
            with open(output_path, "w", newline="", encoding="utf-8") as output_file:
                writer = csv.DictWriter(output_file, fieldnames=CSV_FIELDS)
                writer.writeheader()
                writer.writerows(rows)
        except OSError as exc:
            raise CommandError(f"Could not write CSV output: {exc}") from exc
        self.stdout.write(f"CSV written to {output_path}")

    def _write_report(
        self,
        *,
        dry_run,
        launch_cutoff,
        manager,
        inactive_cutoff,
        active_unshipped_count,
        eligible_count,
        inactive_before,
        inactive_overlap,
    ):
        projected = inactive_before - inactive_overlap
        self.stdout.write("Legacy Job Assignment Reset — DRY RUN" if dry_run else "Legacy Job Assignment Reset")
        self.stdout.write(f"Launch cutoff: {timezone.localtime(launch_cutoff)}")
        self.stdout.write(f"Target manager: {manager} (id={manager.pk})")
        self.stdout.write(f"Inactive cutoff: {timezone.localtime(inactive_cutoff)}")
        self.stdout.write("")
        self.stdout.write("ACTIVE / UNSHIPPED JOBS")
        self.stdout.write(f"Total: {active_unshipped_count}")
        self.stdout.write("")
        self.stdout.write("LEGACY RESET")
        self.stdout.write(f"No movement AND no activity since launch: {eligible_count}")
        self.stdout.write(f"Would be reassigned to manager: {eligible_count}")
        self.stdout.write("")
        self.stdout.write("CURRENT INACTIVE JOBS")
        self.stdout.write(f"No movement in last 7 days: {inactive_before}")
        self.stdout.write("")
        self.stdout.write("PROJECTED AFTER RESET")
        self.stdout.write(f"Jobs receiving a new movement: {eligible_count}")
        self.stdout.write(f"Inactive jobs cleared by reset: {inactive_overlap}")
        self.stdout.write(f"Inactive jobs remaining afterward: {projected}")
        self.stdout.write("")
        self.stdout.write("No database changes made.")

    def _write_execution_summary(
        self,
        *,
        eligible_count,
        reset_count,
        no_longer_eligible_count,
        error_count,
        inactive_before,
        inactive_after,
    ):
        self.stdout.write("Legacy Job Assignment Reset")
        self.stdout.write(f"Eligible at initial scan: {eligible_count}")
        self.stdout.write(f"Successfully reset: {reset_count}")
        self.stdout.write(f"Skipped - no longer eligible: {no_longer_eligible_count}")
        self.stdout.write(f"Skipped - errors: {error_count}")
        self.stdout.write("")
        self.stdout.write(f"Inactive jobs before reset: {inactive_before}")
        self.stdout.write(f"Inactive jobs after reset: {inactive_after}")
        self.stdout.write(f"Inactive jobs cleared: {inactive_before - inactive_after}")
