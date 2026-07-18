from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation

from django.db import transaction

from culet.importer.base import BaseImportCommand
from culet.importer.database import fetch_old_rows
from culet.models import FindingStock, LegacyRecordMap, Style, StyleFinding


HISTORICAL_PROJECT_FINDING_SQL = """
    SELECT
        pf.id,
        pf.project_id,
        p.style_id,
        pf.finding_id,
        pf.qty,
        p.date_added AS project_date,
        pf.date_added AS finding_date
    FROM project_finding pf
    INNER JOIN project p ON p.id = pf.project_id
    WHERE pf.qty > 0
    ORDER BY p.date_added, pf.id
"""


class Command(BaseImportCommand):
    help = (
        "Infer missing StyleFinding recipes from historical project_finding "
        "usage. The most common positive quantity is selected for each mapped "
        "Style/Finding pair. Existing StyleFinding rows are preserved by default."
    )

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--min-observations",
            type=int,
            default=1,
            help=(
                "Minimum number of historical project rows required before a "
                "Style/Finding requirement is inferred. Defaults to 1."
            ),
        )
        parser.add_argument(
            "--update-existing",
            action="store_true",
            help=(
                "Update the quantity on an existing StyleFinding when it differs "
                "from the inferred quantity. Without this flag, existing rows are "
                "left unchanged."
            ),
        )

    def run_import(self, *args, **options):
        self.min_observations = options["min_observations"]
        self.update_existing = options["update_existing"]

        if self.min_observations < 1:
            raise ValueError("--min-observations must be at least 1.")

        rows = fetch_old_rows(HISTORICAL_PROJECT_FINDING_SQL)
        self.stdout.write(
            f"Found {len(rows):,} positive historical project_finding rows."
        )

        observations = defaultdict(list)
        unresolved_styles = 0
        unresolved_findings = 0

        for row in rows:
            self.stats.processed += 1

            style = self.resolve_mapping(
                legacy_table="style",
                legacy_id=row.get("style_id"),
                model_class=Style,
            )
            if style is None:
                unresolved_styles += 1
                self.stats.skipped += 1
                self.record_observation_skip(
                    row,
                    f"No imported Style mapping for legacy style {row.get('style_id')}.",
                )
                continue

            finding = self.resolve_mapping(
                legacy_table="finding",
                legacy_id=row.get("finding_id"),
                model_class=FindingStock,
            )
            if finding is None:
                unresolved_findings += 1
                self.stats.skipped += 1
                self.record_observation_skip(
                    row,
                    "No imported FindingStock mapping for legacy finding "
                    f"{row.get('finding_id')}.",
                )
                continue

            qty = self.clean_qty(row.get("qty"))
            if qty is None:
                self.stats.skipped += 1
                self.record_observation_skip(
                    row,
                    f"Historical quantity {row.get('qty')!r} was zero or invalid.",
                )
                continue

            observations[(style.pk, finding.pk)].append(
                {
                    "legacy_id": row["id"],
                    "project_id": row.get("project_id"),
                    "qty": qty,
                    "date": row.get("finding_date") or row.get("project_date"),
                }
            )

        self.stdout.write(
            f"Resolved {len(observations):,} unique Style/Finding combinations."
        )

        created_groups = 0
        updated_groups = 0
        existing_groups = 0
        low_evidence_groups = 0
        disagreement_groups = 0

        total_groups = len(observations)

        for index, ((style_id, finding_id), evidence) in enumerate(
            sorted(observations.items()),
            start=1,
        ):
            if len(evidence) < self.min_observations:
                low_evidence_groups += 1
                self.stats.skipped += 1
                self.row_message(
                    "SKIP inferred requirement: "
                    f"Style #{style_id}, FindingStock #{finding_id}; "
                    f"only {len(evidence)} observation(s)."
                )
                continue

            style = Style.objects.get(pk=style_id)
            finding = FindingStock.objects.get(pk=finding_id)
            qty_counts = Counter(item["qty"] for item in evidence)

            if len(qty_counts) > 1:
                disagreement_groups += 1

            inferred_qty = self.choose_quantity(qty_counts, evidence)
            supporting_count = qty_counts[inferred_qty]

            with transaction.atomic(using="default"):
                target = (
                    StyleFinding.objects
                    .filter(style=style, finding=finding)
                    .order_by("id")
                    .first()
                )

                if target is None:
                    target = StyleFinding.objects.create(
                        style=style,
                        finding=finding,
                        qty_req=inferred_qty,
                    )
                    action = LegacyRecordMap.ACTION_CREATED
                    self.stats.created += 1
                    created_groups += 1
                    changed_fields = []

                elif self.update_existing and target.qty_req != inferred_qty:
                    old_qty = target.qty_req
                    target.qty_req = inferred_qty
                    target.save(update_fields=["qty_req"])
                    action = LegacyRecordMap.ACTION_UPDATED
                    self.stats.updated += 1
                    updated_groups += 1
                    changed_fields = [f"qty_req {old_qty} → {inferred_qty}"]

                else:
                    action = LegacyRecordMap.ACTION_UNCHANGED
                    self.stats.unchanged += 1
                    existing_groups += 1
                    changed_fields = []

                message = self.build_provenance_message(
                    evidence=evidence,
                    qty_counts=qty_counts,
                    inferred_qty=inferred_qty,
                    supporting_count=supporting_count,
                    existing_preserved=(
                        action == LegacyRecordMap.ACTION_UNCHANGED
                        and target.qty_req != inferred_qty
                    ),
                )

                # Each source project_finding row is retained as evidence for the
                # inferred StyleFinding. Multiple mappings to one target are valid.
                for item in evidence:
                    self.record_mapping(
                        legacy_table="inferred_style_finding",
                        legacy_id=item["legacy_id"],
                        target=target,
                        action=action,
                        message=message,
                    )

                verb = {
                    LegacyRecordMap.ACTION_CREATED: "CREATE",
                    LegacyRecordMap.ACTION_UPDATED: "UPDATE",
                    LegacyRecordMap.ACTION_UNCHANGED: "KEEP",
                }[action]

                detail = (
                    f'{verb} StyleFinding #{target.pk}: style="{style.name}", '
                    f'finding="{finding.name}", qty={target.qty_req}; '
                    f"inferred={inferred_qty} from {len(evidence)} observation(s), "
                    f"support={supporting_count}/{len(evidence)}"
                )
                if changed_fields:
                    detail += f"; {', '.join(changed_fields)}"
                self.row_message(detail)

            self.print_progress(index, total_groups, "Inferred style findings")

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Inference details"))
        self.stdout.write(f"Historical positive rows:      {len(rows):,}")
        self.stdout.write(f"Resolved combinations:        {len(observations):,}")
        self.stdout.write(f"Created StyleFinding rows:    {created_groups:,}")
        self.stdout.write(f"Updated StyleFinding rows:    {updated_groups:,}")
        self.stdout.write(f"Existing rows preserved:      {existing_groups:,}")
        self.stdout.write(f"Quantity disagreements:       {disagreement_groups:,}")
        self.stdout.write(f"Below evidence threshold:     {low_evidence_groups:,}")
        self.stdout.write(f"Unresolved style rows:        {unresolved_styles:,}")
        self.stdout.write(f"Unresolved finding rows:      {unresolved_findings:,}")

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

        return qty.quantize(Decimal("0.001"))

    def choose_quantity(self, qty_counts, evidence):
        """
        Choose the modal quantity. For a tie, prefer the quantity appearing in
        the most recent observation; if still tied, prefer the smaller quantity.
        """
        latest_by_qty = {}

        for item in evidence:
            qty = item["qty"]
            date = item["date"]
            if date is None:
                continue
            current = latest_by_qty.get(qty)
            if current is None or date > current:
                latest_by_qty[qty] = date

        max_count = max(qty_counts.values())
        candidates = [qty for qty, count in qty_counts.items() if count == max_count]

        candidates.sort(
            key=lambda qty: (
                latest_by_qty.get(qty) is not None,
                latest_by_qty.get(qty),
                -qty,
            ),
            reverse=True,
        )
        return candidates[0]

    def build_provenance_message(
        self,
        *,
        evidence,
        qty_counts,
        inferred_qty,
        supporting_count,
        existing_preserved,
    ):
        distribution = ", ".join(
            f"{qty}:{count}"
            for qty, count in sorted(qty_counts.items(), key=lambda item: item[0])
        )
        project_ids = sorted(
            {item["project_id"] for item in evidence if item.get("project_id")}
        )
        project_sample = ", ".join(str(pk) for pk in project_ids[:10])
        if len(project_ids) > 10:
            project_sample += ", ..."

        message = (
            "Style finding inferred from historical project_finding usage. "
            f"Selected qty={inferred_qty} with support "
            f"{supporting_count}/{len(evidence)}. Distribution: {distribution}. "
            f"Legacy projects: {project_sample or 'none'}."
        )

        if existing_preserved:
            message += " Existing StyleFinding quantity was preserved."

        return message

    def record_observation_skip(self, row, reason):
        self.record_skipped(
            legacy_table="inferred_style_finding",
            legacy_id=row["id"],
            message=reason,
        )
        self.row_message(
            f"SKIP project_finding {row['id']}: {reason}"
        )