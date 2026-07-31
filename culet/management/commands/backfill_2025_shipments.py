from datetime import date, datetime, time

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from culet.models import (
    Activity,
    Employee,
    Job,
    JobShip,
    JobStatus,
)


class Command(BaseCommand):
    help = (
        "Marks jobs due before a cutoff date as shipped and creates "
        "historical JobShip records."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--shipped-by",
            required=True,
            help=(
                "Username of the employee who should be recorded as "
                "shipped_by."
            ),
        )

        parser.add_argument(
            "--cutoff",
            default="2025-12-31",
            help=(
                "Jobs due before this date will be shipped. "
                "Default: 2025-12-31."
            ),
        )

        parser.add_argument(
            "--shipped-date",
            default="2025-12-31",
            help=(
                "Date to use for JobShip.shipped_at. "
                "Default: 2025-12-31."
            ),
        )

        parser.add_argument(
            "--close-open-activities",
            action="store_true",
            help=(
                "Close any open activities belonging to the selected jobs. "
                "Without this option, jobs with open activities are skipped."
            ),
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would change without modifying the database.",
        )

    def handle(self, *args, **options):
        try:
            cutoff_date = date.fromisoformat(options["cutoff"])
            shipped_date = date.fromisoformat(options["shipped_date"])
        except ValueError as exc:
            raise CommandError(
                "Dates must use YYYY-MM-DD format."
            ) from exc

        employee = (
            Employee.objects
            .select_related("user")
            .filter(user__username=options["shipped_by"])
            .first()
        )

        if employee is None:
            raise CommandError(
                "No Employee was found for username "
                f"'{options['shipped_by']}'."
            )

        shipped_status = JobStatus.objects.filter(
            name__iexact="Shipped",
        ).first()

        if shipped_status is None:
            raise CommandError(
                'No JobStatus named "Shipped" was found.'
            )

        # Noon avoids possible date changes caused by timezone conversion.
        naive_shipped_at = datetime.combine(
            shipped_date,
            time(hour=12),
        )

        if timezone.is_naive(naive_shipped_at):
            shipped_at = timezone.make_aware(
                naive_shipped_at,
                timezone.get_current_timezone(),
            )
        else:
            shipped_at = naive_shipped_at

        jobs = (
            Job.objects
            .filter(due__lt=cutoff_date)
            .select_related("status")
            .order_by("due", "pk")
        )

        total_found = jobs.count()

        open_activity_job_ids = set(
            Activity.objects.filter(
                job__in=jobs,
                active=True,
                end__isnull=True,
            ).values_list("job_id", flat=True)
        )

        skipped_open_count = 0

        if not options["close_open_activities"]:
            skipped_open_count = len(open_activity_job_ids)
            jobs = jobs.exclude(pk__in=open_activity_job_ids)

        jobs_to_process = list(jobs)
        process_count = len(jobs_to_process)

        existing_shipments = JobShip.objects.filter(
            job__in=jobs_to_process,
        ).count()

        new_shipments = process_count - existing_shipments

        self.stdout.write("")
        self.stdout.write(
            f"Cutoff date: due before {cutoff_date}"
        )
        self.stdout.write(
            f"Historical shipped date: {shipped_at}"
        )
        self.stdout.write(
            f"Recorded shipped by: {employee}"
        )
        self.stdout.write(
            f"Jobs matching cutoff: {total_found}"
        )
        self.stdout.write(
            f"Jobs to process: {process_count}"
        )
        self.stdout.write(
            f"New JobShip records: {new_shipments}"
        )
        self.stdout.write(
            f"Existing JobShip records to update: {existing_shipments}"
        )

        if skipped_open_count:
            self.stdout.write(
                self.style.WARNING(
                    f"Jobs skipped due to open activities: "
                    f"{skipped_open_count}"
                )
            )

        if options["dry_run"]:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "Dry run only. No database changes were made."
                )
            )
            return

        if not jobs_to_process:
            self.stdout.write(
                self.style.WARNING(
                    "There are no jobs to update."
                )
            )
            return

        closed_activity_count = 0
        created_shipment_count = 0
        updated_shipment_count = 0

        with transaction.atomic():
            if options["close_open_activities"]:
                open_activities = Activity.objects.filter(
                    job__in=jobs_to_process,
                    active=True,
                    end__isnull=True,
                )

                for activity in open_activities.iterator():
                    # Do not create a negative activity duration.
                    if activity.start and activity.start <= shipped_at:
                        activity.end = shipped_at
                    else:
                        activity.end = timezone.now()

                    activity.active = False
                    activity.save(
                        update_fields=[
                            "end",
                            "active",
                            "duration",
                        ]
                    )
                    closed_activity_count += 1

            for job in jobs_to_process:
                job.shipped = True
                job.active = False
                job.in_work = False
                job.is_piecework = False
                job.piecework_assigned_at = None
                job.assigned_to = None
                job.holder = None
                job.status = shipped_status

                job.save(
                    update_fields=[
                        "shipped",
                        "active",
                        "in_work",
                        "is_piecework",
                        "piecework_assigned_at",
                        "assigned_to",
                        "holder",
                        "status",
                        "last_updated",
                    ]
                )

                shipment, created = JobShip.objects.update_or_create(
                    job=job,
                    defaults={
                        "shipped_by": employee,
                        "shipped_at": shipped_at,
                        "notes": (
                            "Historical shipment backfill for imported "
                            "jobs due before "
                            f"{cutoff_date.isoformat()}."
                        ),
                    },
                )

                if created:
                    created_shipment_count += 1
                else:
                    updated_shipment_count += 1

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {process_count} jobs."
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Created {created_shipment_count} JobShip records."
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Updated {updated_shipment_count} existing "
                "JobShip records."
            )
        )

        if closed_activity_count:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Closed {closed_activity_count} open activities."
                )
            )