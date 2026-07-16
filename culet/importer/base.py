import logging
import subprocess
import time
from dataclasses import asdict, dataclass
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db import models, transaction
from django.utils import timezone

from culet.models import LegacyImportRun, LegacyRecordMap


logger = logging.getLogger(__name__)


class DryRunRollback(Exception):
    """
    Internal exception used to roll back imported data after a successful
    dry run.
    """


@dataclass
class ImportStats:
    processed: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: int = 0


class BaseImportCommand(BaseCommand):
    """
    Shared foundation for old Culet migration commands.

    Subclasses implement run_import().
    """

    help = "Base command for importing old Culet data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Perform the import inside a transaction, display the result, "
                "and then roll back all imported records and mappings."
            ),
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display additional row-level import information.",
        )
        parser.add_argument(
            "--fail-fast",
            action="store_true",
            help="Stop immediately when the first invalid row is encountered.",
        )

    def handle(self, *args, **options):
        self.dry_run = options["dry_run"]
        self.verbose = options["verbose"]
        self.fail_fast = options["fail_fast"]
        self.stats = ImportStats()

        started_at = time.monotonic()
        command_name = self.__module__.split(".")[-1]

        # The run itself is created outside the import transaction.
        # This lets us retain the run summary even when a dry run or failure
        # rolls back imported data.
        self.import_run = LegacyImportRun.objects.create(
            command=command_name,
            dry_run=self.dry_run,
            status=LegacyImportRun.STATUS_RUNNING,
            git_commit=self.get_git_commit(),
        )

        mode = "DRY RUN" if self.dry_run else "IMPORT"

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"{command_name}: {mode}"
            )
        )

        try:
            with transaction.atomic(using="default"):
                self.run_import(*args, **options)

                if self.dry_run:
                    raise DryRunRollback

        except DryRunRollback:
            elapsed = time.monotonic() - started_at

            self.finish_import_run(
                status=LegacyImportRun.STATUS_COMPLETED,
                elapsed=elapsed,
                extra_summary={
                    "rolled_back": True,
                    "message": (
                        "Dry run completed successfully. Imported records "
                        "and legacy mappings were rolled back."
                    ),
                },
            )

            self.stdout.write(
                self.style.WARNING(
                    "Dry run complete. All imported data and mappings "
                    "were rolled back."
                )
            )

        except Exception as exc:
            elapsed = time.monotonic() - started_at

            logger.exception("Legacy import failed.")

            self.finish_import_run(
                status=LegacyImportRun.STATUS_FAILED,
                elapsed=elapsed,
                error_message=str(exc),
                extra_summary={"rolled_back": True},
            )

            self.print_summary(elapsed)

            if isinstance(exc, CommandError):
                raise

            raise CommandError(str(exc)) from exc

        else:
            elapsed = time.monotonic() - started_at

            self.finish_import_run(
                status=LegacyImportRun.STATUS_COMPLETED,
                elapsed=elapsed,
                extra_summary={"rolled_back": False},
            )

        self.print_summary(elapsed)

    def run_import(self, *args, **options):
        raise NotImplementedError(
            "Import commands must implement run_import()."
        )

    def get_git_commit(self) -> str:
        """
        Return the current Git commit hash when available.
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                check=True,
                text=True,
                timeout=5,
            )
            return result.stdout.strip()[:40]
        except (
            FileNotFoundError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
        ):
            return ""

    def finish_import_run(
        self,
        *,
        status: str,
        elapsed: float,
        error_message: str = "",
        extra_summary: dict[str, Any] | None = None,
    ):
        summary = asdict(self.stats)
        summary["elapsed_seconds"] = round(elapsed, 3)

        if extra_summary:
            summary.update(extra_summary)

        self.import_run.status = status
        self.import_run.finished_at = timezone.now()
        self.import_run.summary = summary
        self.import_run.error_message = error_message
        self.import_run.save(
            update_fields=[
                "status",
                "finished_at",
                "summary",
                "error_message",
            ]
        )

    def record_mapping(
        self,
        *,
        legacy_table: str,
        legacy_id: int,
        target: models.Model,
        action: str,
        message: str = "",
    ) -> LegacyRecordMap:
        """
        Create or update the authoritative mapping from an old MySQL row
        to its new Django object.
        """
        content_type = ContentType.objects.get_for_model(
            target,
            for_concrete_model=False,
        )

        mapping, _ = LegacyRecordMap.objects.update_or_create(
            legacy_table=legacy_table,
            legacy_id=legacy_id,
            content_type=content_type,
            defaults={
                "import_run": self.import_run,
                "object_id": target.pk,
                "action": action,
                "message": message,
            },
        )

        return mapping

    def record_skipped(
        self,
        *,
        legacy_table: str,
        legacy_id: int,
        message: str,
    ) -> LegacyRecordMap:
        """
        Record that an old row was intentionally skipped and has no target.
        """
        mapping = (
            LegacyRecordMap.objects
            .filter(
                legacy_table=legacy_table,
                legacy_id=legacy_id,
                content_type__isnull=True,
            )
            .first()
        )

        if mapping is None:
            mapping = LegacyRecordMap(
                legacy_table=legacy_table,
                legacy_id=legacy_id,
                content_type=None,
            )

        mapping.import_run = self.import_run
        mapping.object_id = None
        mapping.action = LegacyRecordMap.ACTION_SKIPPED
        mapping.message = message
        mapping.save()

        return mapping

    def get_mapped_object(
        self,
        *,
        legacy_table: str,
        legacy_id: int,
        model_class: type[models.Model],
    ) -> models.Model | None:
        """
        Resolve a previously imported legacy row to its Django object.

        This will be used heavily when importing styles, jobs, and components.
        """
        content_type = ContentType.objects.get_for_model(
            model_class,
            for_concrete_model=False,
        )

        mapping = (
            LegacyRecordMap.objects
            .filter(
                legacy_table=legacy_table,
                legacy_id=legacy_id,
                content_type=content_type,
                object_id__isnull=False,
            )
            .first()
        )

        if mapping is None:
            return None

        return model_class.objects.filter(pk=mapping.object_id).first()

    def row_message(self, message: str):
        if self.verbose:
            self.stdout.write(message)

    def record_error(
        self,
        message: str,
        exception: Exception | None = None,
    ):
        self.stats.errors += 1

        if exception:
            logger.exception(message)
            rendered = f"{message}: {exception}"
        else:
            logger.error(message)
            rendered = message

        self.stderr.write(self.style.ERROR(rendered))

        if self.fail_fast:
            raise CommandError(rendered)

    def print_progress(self, current: int, total: int, label: str):
        if total <= 0:
            return

        if current == total or current == 1 or current % 100 == 0:
            self.stdout.write(
                f"{label}: {current:,} / {total:,}"
            )

    def print_summary(self, elapsed: float):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Import summary"))
        self.stdout.write(f"Run ID:     {self.import_run.pk}")
        self.stdout.write(f"Processed:  {self.stats.processed:,}")
        self.stdout.write(
            self.style.SUCCESS(f"Created:    {self.stats.created:,}")
        )
        self.stdout.write(f"Updated:    {self.stats.updated:,}")
        self.stdout.write(f"Unchanged:  {self.stats.unchanged:,}")
        self.stdout.write(
            self.style.WARNING(f"Skipped:    {self.stats.skipped:,}")
        )

        if self.stats.errors:
            self.stdout.write(
                self.style.ERROR(f"Errors:     {self.stats.errors:,}")
            )
        else:
            self.stdout.write(f"Errors:     {self.stats.errors:,}")

        self.stdout.write(f"Elapsed:    {elapsed:.2f} seconds")