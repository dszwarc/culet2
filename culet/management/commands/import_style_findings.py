from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction

from culet.importer.base import BaseImportCommand
from culet.importer.database import fetch_old_rows
from culet.models import FindingStock, LegacyRecordMap, Style, StyleFinding


STYLE_FINDING_SQL = """
    SELECT
        id,
        style_id,
        finding_id,
        qty,
        user_id
    FROM style_finding
    ORDER BY id
"""


class Command(BaseImportCommand):
    help = (
        "Import legacy style finding requirements from style_finding while "
        "preserving every source row through LegacyRecordMap."
    )

    def run_import(self, *args, **options):
        rows = fetch_old_rows(STYLE_FINDING_SQL)
        total = len(rows)

        self.stdout.write(f"Found {total:,} legacy style_finding rows.")

        for index, row in enumerate(rows, start=1):
            self.stats.processed += 1

            try:
                self.import_style_finding(row)
            except Exception as exc:
                self.record_error(
                    f"style_finding row {row.get('id')} could not be imported",
                    exc,
                )
                if self.fail_fast:
                    raise

            self.print_progress(index, total, "Style findings")

    def import_style_finding(self, row):
        legacy_id = row["id"]
        legacy_style_id = row.get("style_id")
        legacy_finding_id = row.get("finding_id")

        style = self.resolve_mapping(
            legacy_table="style",
            legacy_id=legacy_style_id,
            model_class=Style,
        )
        if style is None:
            self.skip_row(
                legacy_id,
                f"No imported Style mapping for legacy style {legacy_style_id}.",
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
            "style": style,
            "finding": finding,
            "qty_req": qty,
        }

        try:
            with transaction.atomic(using="default"):
                target = self.get_mapped_object(
                    legacy_table="style_finding",
                    legacy_id=legacy_id,
                    model_class=StyleFinding,
                )

                if target is None:
                    # Duplicate legacy Style rows may map to one new Style.
                    # Reuse an exact requirement rather than duplicating it.
                    target = (
                        StyleFinding.objects
                        .filter(**desired)
                        .order_by("id")
                        .first()
                    )

                target, action, changed_fields = self.save_target(
                    target=target,
                    desired=desired,
                )

                legacy_user_id = row.get("user_id")
                message = (
                    "Imported legacy style finding requirement. "
                    f"Legacy user_id={legacy_user_id!r} was audit metadata "
                    "and was not imported."
                )

                self.record_mapping(
                    legacy_table="style_finding",
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
                f"Integrity error importing style_finding {legacy_id}."
            ) from exc

    def resolve_mapping(self, *, legacy_table, legacy_id, model_class):
        if not legacy_id:
            return None

        return self.get_mapped_object(
            legacy_table=legacy_table,
            legacy_id=legacy_id,
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

        # Match StyleFinding.qty_req's three decimal places.
        return qty.quantize(Decimal("0.001"))

    def save_target(self, *, target, desired):
        if target is None:
            target = StyleFinding.objects.create(**desired)
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
            legacy_table="style_finding",
            legacy_id=legacy_id,
            message=reason,
        )
        self.row_message(f"SKIP style_finding {legacy_id}: {reason}")

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
            f'style="{target.style.name}", '
            f'finding="{target.finding.name}", '
            f"qty={target.qty_req}"
        )

        if changed_fields:
            details += f"; changed={', '.join(changed_fields)}"

        return (
            f"{verb} style_finding {legacy_id} → "
            f"StyleFinding #{target.pk} ({details})"
        )