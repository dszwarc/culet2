from collections import Counter

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from culet.models import (
    Job,
    JobFinding,
    JobMetal,
    JobStone,
    LegacyRecordMap,
    StyleFinding,
    StyleMetal,
    StyleStone,
)


class Command(BaseCommand):
    help = (
        "Populate missing JobMetal, JobStone, and JobFinding requirements "
        "from each imported Job's Style recipe. Existing job requirements "
        "are preserved."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be created, then roll back all changes.",
        )
        parser.add_argument(
            "--all-jobs",
            action="store_true",
            help=(
                "Process every Job instead of only Jobs mapped from the "
                "legacy project table."
            ),
        )
        parser.add_argument(
            "--job-id",
            type=int,
            action="append",
            dest="job_ids",
            help=(
                "Process only this Job primary key. May be supplied more "
                "than once."
            ),
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Print each requirement that is created or skipped.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        all_jobs = options["all_jobs"]
        job_ids = options.get("job_ids") or []
        verbose = options["verbose"]

        jobs = self._get_jobs(all_jobs=all_jobs, job_ids=job_ids)
        total_jobs = jobs.count()

        if total_jobs == 0:
            raise CommandError("No matching jobs were found.")

        scope = "all jobs" if all_jobs else "legacy-imported jobs"
        if job_ids:
            scope += f" filtered to job IDs: {', '.join(map(str, job_ids))}"

        self.stdout.write(f"Requirement backfill scope: {scope}")
        self.stdout.write(f"Jobs selected: {total_jobs}")
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN: changes will be rolled back."))

        stats = Counter()

        with transaction.atomic():
            for job in jobs.iterator(chunk_size=500):
                stats["jobs_seen"] += 1
                self._populate_metals(job, stats, verbose)
                self._populate_stones(job, stats, verbose)
                self._populate_findings(job, stats, verbose)

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Requirement backfill summary"))
        self.stdout.write(f"Jobs examined:              {stats['jobs_seen']}")
        self.stdout.write(f"Metal requirements created: {stats['metals_created']}")
        self.stdout.write(f"Metal requirements existing:{stats['metals_existing']:>5}")
        self.stdout.write(f"Metal recipe rows skipped:  {stats['metals_invalid']}")
        self.stdout.write(f"Stone requirements created: {stats['stones_created']}")
        self.stdout.write(f"Stone requirements existing:{stats['stones_existing']:>5}")
        self.stdout.write(f"Finding requirements created: {stats['findings_created']}")
        self.stdout.write(f"Finding requirements existing:{stats['findings_existing']:>4}")

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run complete; no records were saved."))
        else:
            created = (
                stats["metals_created"]
                + stats["stones_created"]
                + stats["findings_created"]
            )
            self.stdout.write(self.style.SUCCESS(f"Backfill complete. Created {created} requirements."))

    def _get_jobs(self, *, all_jobs, job_ids):
        jobs = Job.objects.select_related("style").order_by("pk")

        if not all_jobs:
            job_content_type = ContentType.objects.get_for_model(Job)
            imported_job_ids = (
                LegacyRecordMap.objects.filter(
                    legacy_table="project",
                    content_type=job_content_type,
                    object_id__isnull=False,
                )
                .exclude(action=LegacyRecordMap.ACTION_SKIPPED)
                .values_list("object_id", flat=True)
            )
            jobs = jobs.filter(pk__in=imported_job_ids)

        if job_ids:
            jobs = jobs.filter(pk__in=job_ids)

        return jobs.distinct()

    def _populate_metals(self, job, stats, verbose):
        style_metals = StyleMetal.objects.filter(style_id=job.style_id).order_by("pk")

        for recipe in style_metals:
            # JobMetal.part is required, so incomplete StyleMetal rows cannot be copied.
            if recipe.part_id is None:
                stats["metals_invalid"] += 1
                if verbose:
                    self.stdout.write(
                        self.style.WARNING(
                            f"SKIP metal: Job {job.pk} ({job.stock_num or job.barcode}) "
                            f"StyleMetal {recipe.pk} has no part."
                        )
                    )
                continue

            lookup = {
                "job_id": job.pk,
                "part_id": recipe.part_id,
                "metal_type_id": recipe.metal_type_id,
            }
            defaults = {
                "qty_req": recipe.qty_req,
                "weight_req": recipe.weight,
            }
            _, created = JobMetal.objects.get_or_create(**lookup, defaults=defaults)

            key = "metals_created" if created else "metals_existing"
            stats[key] += 1
            if verbose:
                action = "CREATE" if created else "KEEP"
                self.stdout.write(
                    f"{action} metal: Job {job.pk} ({job.stock_num or job.barcode}) "
                    f"part={recipe.part_id} metal_type={recipe.metal_type_id}"
                )

    def _populate_stones(self, job, stats, verbose):
        style_stones = StyleStone.objects.filter(style_id=job.style_id).order_by("pk")

        for recipe in style_stones:
            normalized_size = (recipe.stone_size or "").strip()
            lookup = {
                "job_id": job.pk,
                "stone_type_id": recipe.stone_type_id,
                "stone_shape_id": recipe.stone_shape_id,
                "stone_size": normalized_size,
            }
            defaults = {"qty_req": recipe.qty_req}
            _, created = JobStone.objects.get_or_create(**lookup, defaults=defaults)

            key = "stones_created" if created else "stones_existing"
            stats[key] += 1
            if verbose:
                action = "CREATE" if created else "KEEP"
                self.stdout.write(
                    f"{action} stone: Job {job.pk} ({job.stock_num or job.barcode}) "
                    f"type={recipe.stone_type_id} shape={recipe.stone_shape_id} "
                    f"size={normalized_size!r}"
                )

    def _populate_findings(self, job, stats, verbose):
        style_findings = StyleFinding.objects.filter(style_id=job.style_id).order_by("pk")

        for recipe in style_findings:
            lookup = {
                "job_id": job.pk,
                "finding_id": recipe.finding_id,
            }
            defaults = {
                "qty_req": recipe.qty_req,
                "qty_used": 0,
            }
            _, created = JobFinding.objects.get_or_create(**lookup, defaults=defaults)

            key = "findings_created" if created else "findings_existing"
            stats[key] += 1
            if verbose:
                action = "CREATE" if created else "KEEP"
                self.stdout.write(
                    f"{action} finding: Job {job.pk} ({job.stock_num or job.barcode}) "
                    f"finding={recipe.finding_id}"
                )