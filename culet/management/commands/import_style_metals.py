from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import IntegrityError, transaction

from culet.importer.base import BaseImportCommand
from culet.importer.database import fetch_old_rows
from culet.importer.utils import clean_text
from culet.models import (
    LegacyRecordMap,
    MetalPart,
    MetalType,
    Style,
    StyleMetal,
)


STYLE_PART_NUMBER_SQL = """
    SELECT
        spn.id,
        spn.style_id,
        spn.part_number_id,
        spn.qty,
        pn.part_number AS part_number_text
    FROM style_part_number spn
    LEFT JOIN part_number pn
        ON pn.id = spn.part_number_id
    ORDER BY spn.id
"""


STYLE_METAL_SQL = """
    SELECT
        sm.id,
        sm.style_id,
        sm.metal_type_id,
        sm.description,
        sm.weight,
        sm.qty,
        sm.part_number,
        pn.id AS matched_part_number_id
    FROM style_metal sm
    LEFT JOIN part_number pn
        ON TRIM(sm.part_number) <> ''
       AND LOWER(TRIM(pn.part_number)) = LOWER(TRIM(sm.part_number))
    ORDER BY sm.id
"""


class Command(BaseImportCommand):
    help = (
        "Import legacy style metal requirements from style_part_number and "
        "style_metal. The command preserves both source tables through "
        "LegacyRecordMap and merges exact overlaps when possible."
    )

    def run_import(self, *args, **options):
        style_part_rows = fetch_old_rows(STYLE_PART_NUMBER_SQL)
        style_metal_rows = fetch_old_rows(STYLE_METAL_SQL)

        self.style_metal_by_style_and_part = defaultdict(list)
        self.metal_type_ids_by_style = defaultdict(set)
        self.style_part_ids_by_style_and_part = defaultdict(list)

        for row in style_metal_rows:
            legacy_style_id = row.get("style_id")
            legacy_metal_type_id = row.get("metal_type_id")
            normalized_part = self.normalize_part(row.get("part_number"))

            if normalized_part:
                self.style_metal_by_style_and_part[
                    (legacy_style_id, normalized_part)
                ].append(row)

            if legacy_metal_type_id:
                self.metal_type_ids_by_style[legacy_style_id].add(
                    legacy_metal_type_id
                )

        for row in style_part_rows:
            self.style_part_ids_by_style_and_part[
                (row.get("style_id"), row.get("part_number_id"))
            ].append(row.get("id"))

        total = len(style_part_rows) + len(style_metal_rows)
        self.stdout.write(
            f"Found {len(style_part_rows):,} style_part_number rows and "
            f"{len(style_metal_rows):,} style_metal rows "
            f"({total:,} total)."
        )

        processed = 0

        # Import catalog part relationships first. A later style_metal row with
        # the same style and part can enrich this target with metal type/weight.
        for row in style_part_rows:
            processed += 1
            self.stats.processed += 1

            try:
                self.import_style_part_number(row)
            except Exception as exc:
                self.record_error(
                    f"style_part_number row {row.get('id')} could not be imported",
                    exc,
                )
                if self.fail_fast:
                    raise

            self.print_progress(processed, total, "Style metals")

        for row in style_metal_rows:
            processed += 1
            self.stats.processed += 1

            try:
                self.import_style_metal(row)
            except Exception as exc:
                self.record_error(
                    f"style_metal row {row.get('id')} could not be imported",
                    exc,
                )
                if self.fail_fast:
                    raise

            self.print_progress(processed, total, "Style metals")

    def import_style_part_number(self, row):
        legacy_id = row["id"]
        legacy_style_id = row.get("style_id")
        legacy_part_id = row.get("part_number_id")

        style = self.resolve_required_mapping(
            legacy_table="style",
            legacy_id=legacy_style_id,
            model_class=Style,
        )
        if style is None:
            self.skip_row(
                "style_part_number",
                legacy_id,
                f"No imported Style mapping for legacy style {legacy_style_id}.",
            )
            return

        part = self.resolve_required_mapping(
            legacy_table="part_number",
            legacy_id=legacy_part_id,
            model_class=MetalPart,
        )
        if part is None:
            self.skip_row(
                "style_part_number",
                legacy_id,
                f"Metal part {legacy_part_id} was not imported or was ignored.",
            )
            return

        metal_type, weight, inference_message = self.infer_style_part_details(row)
        desired = {
            "style": style,
            "part": part,
            "qty_req": self.clean_qty(row.get("qty")),
            "weight": weight,
            "metal_type": metal_type,
        }

        try:
            with transaction.atomic(using="default"):
                target = self.get_mapped_object(
                    legacy_table="style_part_number",
                    legacy_id=legacy_id,
                    model_class=StyleMetal,
                )

                if target is None:
                    target = (
                        StyleMetal.objects
                        .filter(style=style, part=part)
                        .order_by("id")
                        .first()
                    )

                target, action, changed_fields = self.save_target(
                    target=target,
                    desired=desired,
                )

                self.record_mapping(
                    legacy_table="style_part_number",
                    legacy_id=legacy_id,
                    target=target,
                    action=action,
                    message=inference_message,
                )

                self.row_message(
                    self.render_result(
                        action=action,
                        source_table="style_part_number",
                        legacy_id=legacy_id,
                        target=target,
                        changed_fields=changed_fields,
                    )
                )

        except IntegrityError as exc:
            raise ValueError(
                f"Integrity error importing style_part_number {legacy_id}."
            ) from exc

    def import_style_metal(self, row):
        legacy_id = row["id"]
        legacy_style_id = row.get("style_id")
        legacy_metal_type_id = row.get("metal_type_id")

        style = self.resolve_required_mapping(
            legacy_table="style",
            legacy_id=legacy_style_id,
            model_class=Style,
        )
        if style is None:
            self.skip_row(
                "style_metal",
                legacy_id,
                f"No imported Style mapping for legacy style {legacy_style_id}.",
            )
            return

        metal_type = self.resolve_required_mapping(
            legacy_table="metal_type",
            legacy_id=legacy_metal_type_id,
            model_class=MetalType,
        )
        if metal_type is None:
            self.skip_row(
                "style_metal",
                legacy_id,
                f"No imported MetalType mapping for legacy metal type "
                f"{legacy_metal_type_id}.",
            )
            return

        part, part_message = self.resolve_optional_style_metal_part(row)
        desired = {
            "style": style,
            "part": part,
            "qty_req": self.clean_qty(row.get("qty")),
            "weight": self.clean_weight(row.get("weight")),
            "metal_type": metal_type,
        }

        try:
            with transaction.atomic(using="default"):
                target = self.get_mapped_object(
                    legacy_table="style_metal",
                    legacy_id=legacy_id,
                    model_class=StyleMetal,
                )
                overlap_target = None

                if target is None and row.get("matched_part_number_id"):
                    overlap_target = self.get_style_part_overlap_target(row)
                    target = overlap_target

                # Do not collapse generic/free-text style_metal rows. Several
                # distinct old rows can legitimately share style + metal type.
                target, action, changed_fields = self.save_target(
                    target=target,
                    desired=desired,
                    preserve_existing_qty=overlap_target is not None,
                )

                messages = [message for message in [part_message] if message]
                if overlap_target is not None:
                    messages.append(
                        "Merged with the corresponding style_part_number "
                        "component and enriched it with metal type/weight."
                    )

                self.record_mapping(
                    legacy_table="style_metal",
                    legacy_id=legacy_id,
                    target=target,
                    action=action,
                    message=" ".join(messages),
                )

                self.row_message(
                    self.render_result(
                        action=action,
                        source_table="style_metal",
                        legacy_id=legacy_id,
                        target=target,
                        changed_fields=changed_fields,
                    )
                )

        except IntegrityError as exc:
            raise ValueError(
                f"Integrity error importing style_metal {legacy_id}."
            ) from exc

    def infer_style_part_details(self, row):
        legacy_style_id = row.get("style_id")
        normalized_part = self.normalize_part(row.get("part_number_text"))
        exact_rows = self.style_metal_by_style_and_part.get(
            (legacy_style_id, normalized_part),
            [],
        )

        if exact_rows:
            source = min(exact_rows, key=lambda item: item["id"])
            metal_type = self.resolve_required_mapping(
                legacy_table="metal_type",
                legacy_id=source.get("metal_type_id"),
                model_class=MetalType,
            )
            return (
                metal_type,
                self.clean_weight(source.get("weight")),
                f"Metal type and weight inferred from exact style_metal "
                f"row {source['id']}.",
            )

        metal_type_ids = self.metal_type_ids_by_style.get(legacy_style_id, set())
        if len(metal_type_ids) == 1:
            legacy_metal_type_id = next(iter(metal_type_ids))
            metal_type = self.resolve_required_mapping(
                legacy_table="metal_type",
                legacy_id=legacy_metal_type_id,
                model_class=MetalType,
            )
            return (
                metal_type,
                None,
                f"Metal type inferred from the style's only legacy metal "
                f"type ({legacy_metal_type_id}); no reliable weight match.",
            )

        if not metal_type_ids:
            return (
                None,
                None,
                "Legacy style_part_number had no metal type or weight source.",
            )

        return (
            None,
            None,
            "Metal type left blank because the legacy style used multiple "
            "metal types and no exact part match existed.",
        )

    def resolve_optional_style_metal_part(self, row):
        raw_part = clean_text(row.get("part_number"))
        normalized_part = self.normalize_part(raw_part)
        legacy_part_id = row.get("matched_part_number_id")

        if not normalized_part or normalized_part in {"n/a", "na"}:
            return None, "Legacy style_metal row did not specify a catalog part."

        if not legacy_part_id:
            return (
                None,
                f'Legacy free-text part "{raw_part}" did not match the '
                "part_number catalog; preserved as a generic metal requirement.",
            )

        part = self.resolve_required_mapping(
            legacy_table="part_number",
            legacy_id=legacy_part_id,
            model_class=MetalPart,
        )

        if part is None:
            return (
                None,
                f'Legacy part "{raw_part}" matched ignored/unimported '
                f"part_number {legacy_part_id}; preserved without a part link.",
            )

        return part, ""

    def get_style_part_overlap_target(self, row):
        source_ids = self.style_part_ids_by_style_and_part.get(
            (row.get("style_id"), row.get("matched_part_number_id")),
            [],
        )

        for source_id in source_ids:
            target = self.get_mapped_object(
                legacy_table="style_part_number",
                legacy_id=source_id,
                model_class=StyleMetal,
            )
            if target is not None:
                return target

        return None

    def save_target(
        self,
        *,
        target,
        desired,
        preserve_existing_qty=False,
    ):
        if target is None:
            target = StyleMetal.objects.create(**desired)
            self.stats.created += 1
            return target, LegacyRecordMap.ACTION_CREATED, []

        changed_fields = []

        for field_name, desired_value in desired.items():
            if preserve_existing_qty and field_name == "qty_req":
                continue

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

    def resolve_required_mapping(self, *, legacy_table, legacy_id, model_class):
        if not legacy_id:
            return None

        return self.get_mapped_object(
            legacy_table=legacy_table,
            legacy_id=legacy_id,
            model_class=model_class,
        )

    def clean_qty(self, value):
        try:
            qty = int(value or 0)
        except (TypeError, ValueError):
            return None

        return qty if qty > 0 else None

    def clean_weight(self, value):
        if value in (None, ""):
            return None

        try:
            weight = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None

        if weight < 0:
            return None

        return weight.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def normalize_part(self, value):
        return clean_text(value).casefold()

    def skip_row(self, legacy_table, legacy_id, reason):
        self.stats.skipped += 1
        self.record_skipped(
            legacy_table=legacy_table,
            legacy_id=legacy_id,
            message=reason,
        )
        self.row_message(f"SKIP {legacy_table} {legacy_id}: {reason}")

    def render_result(
        self,
        *,
        action,
        source_table,
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
            f"{verb} {source_table} {legacy_id} → "
            f"StyleMetal #{target.pk}: {target.style} / "
            f"{target.part or 'generic metal'} / "
            f"{target.metal_type or 'unknown type'}{detail}"
        )