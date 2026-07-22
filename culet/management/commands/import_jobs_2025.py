from __future__ import annotations

from culet.management.commands.import_jobs import Command as ImportJobsCommand


class Command(ImportJobsCommand):
    """
    Import legacy Culet projects created during calendar year 2025.

    This command intentionally inherits all behavior from import_jobs,
    including:
    - customer and style placeholder handling
    - employee assignment resolution
    - barcode and stock-number handling
    - idempotent LegacyRecordMap behavior
    - preservation of legacy shipped state

    Legacy projects where project.is_shipped is true are imported with:
        Job.shipped = True
        Job.status = "Shipped"

    All other projects are imported with:
        Job.shipped = False
        Job.status = "Imported"
    """

    help = (
        "Import old Culet projects created during 2025 as Jobs, using the "
        "same migration logic as import_jobs. Legacy projects marked shipped "
        "are imported with shipped=True and status='Shipped'."
    )

    def add_arguments(self, parser):
        # Reproduce the base import options, but use 2025 as this command's
        # fixed default date range.
        super().add_arguments(parser)

        # import_jobs already registered these options. Change only their
        # defaults and help text rather than duplicating the arguments.
        for action in parser._actions:
            if action.dest == "start_date":
                action.default = "2025-01-01"
                action.help = (
                    "Inclusive legacy project date_added boundary "
                    "(default: 2025-01-01)."
                )
            elif action.dest == "end_date":
                action.default = "2026-01-01"
                action.help = (
                    "Exclusive legacy project date_added boundary "
                    "(default: 2026-01-01)."
                )