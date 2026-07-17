from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction

from culet.importer.base import BaseImportCommand
from culet.importer.database import fetch_old_rows
from culet.models import (
    LegacyRecordMap,
    StoneShape,
    StoneType,
    Style,
    StyleStone,
)


STYLE_STONE_SQL = """
    SELECT
        id,
        style_id,
        stone_type_id,
        stone_shape_id,
        size,
        weight,
        qty
    FROM style_stone
    ORDER BY id
"""


class Command(BaseImportCommand):
    help = (
        "Import legacy style stone requirements from style_stone while "
        "preserving every source row through LegacyRecordMap."
    )

    def run_import(self, *args, **options):
        rows = fetch_old_rows(STYLE_STONE_SQL)
        total = len(rows)

        self.stdout.write(f"Found {total:,} legacy style_stone rows.")

        for index, row in enumerate(rows, start=1):
            self.stats.processed += 1

            try:
                self.import_style_stone(row)
            except Exception as exc:
                self.record_error(
                    f"style_stone row {row.get('id')} could not be imported",
                    exc,
                )
                if self.fail_fast:
                    raise

            self.print_progress(index, total, "Style stones")

    def import_style_stone(self, row):
        legacy_id = row["id"]
        legacy_style_id = row.get("style_id")
        legacy_stone_type_id = row.get("stone_type_id")
        legacy_stone_shape_id = row.get("stone_shape_id")

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

        stone_type = self.resolve_mapping(
            legacy_table="stone_type",
            legacy_id=legacy_stone_type_id,
            model_class=StoneType,
        )
        if stone_type is None:
            self.skip_row(
                legacy_id,
                "No imported StoneType mapping for legacy stone type "
                f"{legacy_stone_type_id}.",
            )
            return

        stone_shape = self.resolve_mapping(
            legacy_table="stone_shape",
            legacy_id=legacy_stone_shape_id,
            model_class=StoneShape,
        )
        if stone_shape is None:
            self.skip_row(
                legacy_id,
                "No imported StoneShape mapping for legacy stone shape "
                f"{legacy_stone_shape_id}.",
            )
            return

        qty = self.clean_qty(row.get("qty"))
        if qty is None:
            self.skip_row(
                legacy_id,
                f"Legacy quantity {row.get('qty')!r} was zero or invalid.",
            )
            return

        stone_size = self.clean_size(row.get("size"))
        legacy_weight = self.clean_weight_for_message(row.get("weight"))

        desired = {
            "style": style,
            "stone_type": stone_type,
            "stone_shape": stone_shape,
            "stone_size": stone_size,
            "qty_req": qty,
        }

        try:
            with transaction.atomic(using="default"):
                target = self.get_mapped_object(
                    legacy_table="style_stone",
                    legacy_id=legacy_id,
                    model_class=StyleStone,
                )

                if target is None:
                    # Styles with duplicate legacy names may map to one new
                    # Style. Reuse an exact recipe component instead of
                    # creating duplicate rows after that merge.
                    target = (
                        StyleStone.objects
                        .filter(**desired)
                        .order_by("id")
                        .first()
                    )

                target, action, changed_fields = self.save_target(
                    target=target,
                    desired=desired,
                )

                message = (
                    "Legacy style_stone weight was "
                    f"{legacy_weight}; StyleStone has no weight field, so the "
                    "value was not imported."
                )

                self.record_mapping(
                    legacy_table="style_stone",
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
                f"Integrity error importing style_stone {legacy_id}."
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
        try:
            qty = int(value)
        except (TypeError, ValueError):
            return None

        return qty if qty > 0 else None

    def clean_size(self, value):
        """
        Preserve the legacy two-decimal millimeter size as text.

        Examples:
            1.20 -> "1.20"
            0.00 -> "0.00"
        """
        if value in (None, ""):
            return None

        try:
            size = Decimal(str(value)).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError):
            return None

        text = format(size, "f")
        max_length = StyleStone._meta.get_field("stone_size").max_length
        return text[:max_length]

    def clean_weight_for_message(self, value):
        if value in (None, ""):
            return "blank"

        try:
            return format(Decimal(str(value)), "f")
        except (InvalidOperation, TypeError, ValueError):
            return repr(value)

    def save_target(self, *, target, desired):
        if target is None:
            target = StyleStone.objects.create(**desired)
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
            legacy_table="style_stone",
            legacy_id=legacy_id,
            message=reason,
        )
        self.row_message(f"SKIP style_stone {legacy_id}: {reason}")

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
        }[action]

        detail = ""
        if changed_fields:
            detail = f" ({', '.join(changed_fields)})"

        return (
            f"{verb} style_stone {legacy_id} → StyleStone #{target.pk}: "
            f"{target.style} / {target.stone_type} / "
            f"{target.stone_shape} / {target.stone_size} mm × "
            f"{target.qty_req}{detail}"
        )