from datetime import date

from django.core.management.base import CommandError

from culet.importer.database import fetch_old_rows
from culet.management.commands.import_job_stones import (
    Command as BaseJobStoneCommand,
)


JOB_STONE_2025_SQL = """
    SELECT
        s.id,
        s.project_id,
        s.stone_type_id,
        s.stone_shape_id,
        s.size,
        s.weight,
        s.qty,
        s.date_release,
        s.date_added,
        s.user_id,
        s.employee_id
    FROM stone AS s
    INNER JOIN project AS p
        ON p.id = s.project_id
    WHERE p.date_added >= %s
      AND p.date_added < %s
    ORDER BY s.id
"""


class Command(BaseJobStoneCommand):
    """
    Import only job-stone rows belonging to legacy projects created in 2025.

    The underlying row-import logic is inherited unchanged from
    import_job_stones. The SQL date restriction prevents rows belonging to
    2026 projects from being selected or updated.
    """

    help = (
        "Import legacy job stone requirements for projects created during "
        "2025 without touching components belonging to 2026 projects."
    )

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--start-date",
            default="2025-01-01",
            help="Inclusive legacy project date_added boundary (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--end-date",
            default="2026-01-01",
            help="Exclusive legacy project date_added boundary (YYYY-MM-DD).",
        )

    def run_import(self, *args, **options):
        start_date = self.parse_date(options["start_date"], "--start-date")
        end_date = self.parse_date(options["end_date"], "--end-date")

        if end_date <= start_date:
            raise CommandError("--end-date must be later than --start-date.")

        rows = fetch_old_rows(
            JOB_STONE_2025_SQL,
            [start_date.isoformat(), end_date.isoformat()],
        )
        total = len(rows)

        self.stdout.write(
            f"Found {total:,} legacy stone rows for projects from "
            f"{start_date} through {end_date} (exclusive)."
        )

        for index, row in enumerate(rows, start=1):
            self.stats.processed += 1

            try:
                self.import_job_stone(row)
            except Exception as exc:
                self.record_error(
                    f"stone row {row.get('id')} could not be imported",
                    exc,
                )
                if self.fail_fast:
                    raise

            self.print_progress(index, total, "2025 job stones")

    @staticmethod
    def parse_date(value, option_name):
        try:
            return date.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise CommandError(
                f"{option_name} must use YYYY-MM-DD format."
            ) from exc