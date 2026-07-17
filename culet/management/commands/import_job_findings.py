from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction

from culet.importer.base import BaseImportCommand
from culet.importer.database import fetch_old_rows
from culet.models import FindingStock, Job, JobFinding, LegacyRecordMap


JOB_FINDING_SQL = """
    SELECT
        id,
        project_id,
        finding_id,
        qty,
        date_added
    FROM project_finding
    ORDER BY id
"""


class Command(BaseImportCommand):
    help = (
        "Import legacy job finding requirements from project_finding while "
        "preserving every source row through LegacyRecordMap."
    )

    def run_import(self, *args, **options):
        rows = fetch_old_rows(JOB_FINDING_SQL)
        total = len(rows)

        self.stdout.write(f"Found {total:,} legacy project_finding rows.")

        for index, row in enumerate(rows, start=1):
            self.stats.processed += 1

            try:
                self.import_job_finding(row)
            except Exception as exc:
                self.record_error(
                    f"project_finding row {row.get('id')} could not be imported",
                    exc,
                )
                if self.fail_fast:
                    raise

            self.print_progress(index, total, "Job findings")

    def import_job_finding(self, row):
        legacy_id = int(row["id"])
        legacy_project_id = row.get("project_id")
        legacy_finding_id = row.get("finding_id")

        job = self.resolve_mapping(
            legacy_table="project",
            legacy_id=legacy_project_id,
            model_class=Job,
        )
        if job is None:
            self.skip_row(
                legacy_id,
                f"No imported Job mapping for legacy project {legacy_project_id}.",
            )
            return

        finding = self.resolve_mapping(
            legacy_table="finding",
            legacy_id=legacy_finding_id,
            model_class=FindingStock,
        )
        if finding is None:
            self.skip_row(
                legacy_id,
                "No imported FindingStock mapping for legacy finding "
                f"{legacy_finding_id}.",
            )
            return

        qty = self.clean_qty(row.get("qty"))
        if qty is None:
            self.skip_row(
                legacy_id,
                f"Legacy quantity {row.get('qty')!r} was zero or invalid.",
            )
            return

        desired = {
            "job": job,
            "finding": finding,
            "qty_req": qty,
            "qty_used": Decimal("0.000"),
        }

        try:
            with transaction.atomic(using="default"):
                target = self.get_mapped_object(
                    legacy_table="project_finding",
                    legacy_id=legacy_id,
                    model_class=JobFinding,
                )

                if target is None:
                    # Reuse an exact existing requirement on reruns or when
                    # duplicate legacy rows describe the same job component.
                    target = (
                        JobFinding.objects
                        .filter(**desired)
                        .order_by("id")
                        .first()
                    )

                target, action, changed_fields = self.save_target(
                    target=target,
                    desired=desired,
                )

                date_added = row.get("date_added") or "blank"
                message = (
                    "Imported legacy project finding requirement. "
                    f"Legacy date_added={date_added}; JobFinding has no "
                    "source-created timestamp field, so it was not imported."
                )

                self.record_mapping(
                    legacy_table="project_finding",
                    legacy_id=legacy_id,
                    target=target,
                    action=action,
                    message=message,
                )

                self.row_message(
                    self.render_result(
                        action=action,
                        legacy_id=legacy_id,
                        target=target,
                        changed_fields=changed_fields,
                    )
                )

        except IntegrityError as exc:
            raise ValueError(
                f"Integrity error importing project_finding {legacy_id}."
            ) from exc

    def resolve_mapping(self, *, legacy_table, legacy_id, model_class):
        if not legacy_id:
            return None

        return self.get_mapped_object(
            legacy_table=legacy_table,
            legacy_id=int(legacy_id),
            model_class=model_class,
        )

    def clean_qty(self, value):
        if value in (None, ""):
            return None

        try:
            qty = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None

        if not qty.is_finite() or qty <= 0:
            return None

        return qty.quantize(Decimal("0.001"))

    def save_target(self, *, target, desired):
        if target is None:
            target = JobFinding.objects.create(**desired)
            self.stats.created += 1
            return target, LegacyRecordMap.ACTION_CREATED, []

        changed_fields = []

        for field_name, desired_value in desired.items():
            current_value = getattr(target, field_name)
            if current_value != desired_value:
                setattr(target, field_name, desired_value)
                changed_fields.append(field_name)

        if changed_fields:
            target.save(update_fields=changed_fields)
            self.stats.updated += 1
            action = LegacyRecordMap.ACTION_UPDATED
        else:
            self.stats.unchanged += 1
            action = LegacyRecordMap.ACTION_UNCHANGED

        return target, action, changed_fields

    def skip_row(self, legacy_id, reason):
        self.stats.skipped += 1
        self.record_skipped(
            legacy_table="project_finding",
            legacy_id=legacy_id,
            message=reason,
        )
        self.row_message(f"SKIP project_finding {legacy_id}: {reason}")

    def render_result(
        self,
        *,
        action,
        legacy_id,
        target,
        changed_fields,
    ):
        verb = {
            LegacyRecordMap.ACTION_CREATED: "CREATE",
            LegacyRecordMap.ACTION_UPDATED: "UPDATE",
            LegacyRecordMap.ACTION_UNCHANGED: "UNCHANGED",
        }.get(action, action.upper())

        details = (
            f'job="{target.job.stock_num}", '
            f'finding="{target.finding.name}", '
            f"qty_req={target.qty_req}, qty_used={target.qty_used}"
        )

        if changed_fields:
            details += f"; changed={', '.join(changed_fields)}"

        return (
            f"{verb} project_finding {legacy_id} → "
            f"JobFinding #{target.pk} ({details})"
        )