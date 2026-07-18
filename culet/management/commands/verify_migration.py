from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Q

from culet.models import (
    Job,
    JobFinding,
    JobMetal,
    JobStone,
    LegacyImportRun,
    LegacyRecordMap,
    StyleFinding,
    StyleMetal,
    StyleStone,
)


EXPECTED_COMMANDS = (
    "import_customers",
    "import_employees",
    "import_vendors",
    "import_stone_types",
    "import_stone_shapes",
    "import_metal_types",
    "import_metal_parts",
    "import_findings",
    "import_styles",
    "import_style_metals",
    "import_style_stones",
    "import_style_findings",
    "import_jobs",
    "import_job_metals",
    "import_job_stones",
    "import_job_findings",
)


@dataclass
class Finding:
    severity: str
    section: str
    message: str
    details: list[str] = field(default_factory=list)


class Command(BaseCommand):
    help = (
        "Perform a read-only integrity audit of the old Culet migration. "
        "The command does not create, update, or delete application data."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--start-date",
            default="2026-01-01",
            help="Inclusive imported Job.created boundary (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--end-date",
            default="2027-01-01",
            help="Exclusive imported Job.created boundary (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display sample record details for warnings and errors.",
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Return a failing exit status when warnings are found.",
        )
        parser.add_argument(
            "--sample-size",
            type=int,
            default=20,
            help="Maximum detail rows shown per finding in verbose mode.",
        )

    def handle(self, *args, **options):
        self.verbose = options["verbose"]
        self.strict = options["strict"]
        self.sample_size = max(1, options["sample_size"])
        self.findings: list[Finding] = []

        start_date = self.parse_date(options["start_date"], "--start-date")
        end_date = self.parse_date(options["end_date"], "--end-date")
        if end_date <= start_date:
            raise CommandError("--end-date must be later than --start-date.")

        self.start_date = start_date
        self.end_date = end_date

        self.stdout.write(self.style.MIGRATE_HEADING("=" * 68))
        self.stdout.write(self.style.MIGRATE_HEADING("CULET MIGRATION VERIFICATION"))
        self.stdout.write(
            f"Imported job period: {start_date.isoformat()} through "
            f"{end_date.isoformat()} (exclusive)"
        )
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 68))

        self.job_ct = ContentType.objects.get_for_model(Job)
        self.job_metal_ct = ContentType.objects.get_for_model(JobMetal)
        self.job_stone_ct = ContentType.objects.get_for_model(JobStone)
        self.job_finding_ct = ContentType.objects.get_for_model(JobFinding)

        self.project_maps = LegacyRecordMap.objects.filter(
            legacy_table="project",
            content_type=self.job_ct,
            object_id__isnull=False,
        )
        mapped_job_ids = set(self.project_maps.values_list("object_id", flat=True))

        self.imported_jobs = Job.objects.filter(
            pk__in=mapped_job_ids,
            created__date__gte=start_date,
            created__date__lt=end_date,
        ).select_related("customer", "style", "style__customer", "assigned_to", "holder")
        self.imported_job_ids = set(self.imported_jobs.values_list("pk", flat=True))

        self.check_import_runs()
        self.check_mapping_integrity()
        self.check_project_job_mappings()
        self.check_jobs()
        self.check_job_metals()
        self.check_job_stones()
        self.check_job_findings()
        self.check_style_component_completeness()
        self.print_report()

        errors = sum(1 for finding in self.findings if finding.severity == "ERROR")
        warnings = sum(1 for finding in self.findings if finding.severity == "WARN")

        if errors or (self.strict and warnings):
            reason = f"Verification failed with {errors} error(s) and {warnings} warning(s)."
            if self.strict and not errors:
                reason += " Strict mode treats warnings as failures."
            raise CommandError(reason)

    @staticmethod
    def parse_date(value: str, option_name: str) -> date:
        try:
            return date.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise CommandError(f"{option_name} must use YYYY-MM-DD format.") from exc

    def add_finding(self, severity: str, section: str, message: str, details=None):
        self.findings.append(
            Finding(
                severity=severity,
                section=section,
                message=message,
                details=list(details or [])[: self.sample_size],
            )
        )

    def pass_check(self, section: str, message: str):
        self.add_finding("PASS", section, message)

    def info(self, section: str, message: str, details=None):
        self.add_finding("INFO", section, message, details)

    def warn(self, section: str, message: str, details=None):
        self.add_finding("WARN", section, message, details)

    def error(self, section: str, message: str, details=None):
        self.add_finding("ERROR", section, message, details)

    def check_import_runs(self):
        section = "Import runs"
        missing = []
        failed = []
        running = []
        summaries = []

        for command_name in EXPECTED_COMMANDS:
            latest = (
                LegacyImportRun.objects.filter(command=command_name, dry_run=False)
                .order_by("-started_at", "-id")
                .first()
            )
            if latest is None:
                missing.append(command_name)
                continue

            summary = latest.summary or {}
            summaries.append(
                f"{command_name}: {latest.status}; "
                f"processed={summary.get('processed', 0)}, "
                f"created={summary.get('created', 0)}, "
                f"updated={summary.get('updated', 0)}, "
                f"unchanged={summary.get('unchanged', 0)}, "
                f"skipped={summary.get('skipped', 0)}, "
                f"errors={summary.get('errors', 0)}"
            )
            if latest.status == LegacyImportRun.STATUS_FAILED:
                failed.append(command_name)
            elif latest.status == LegacyImportRun.STATUS_RUNNING:
                running.append(command_name)

        if missing:
            self.warn(section, f"{len(missing)} expected command(s) have no completed real run.", missing)
        else:
            self.pass_check(section, f"All {len(EXPECTED_COMMANDS)} expected commands have real runs.")

        if failed:
            self.error(section, f"{len(failed)} latest import run(s) failed.", failed)
        else:
            self.pass_check(section, "No latest import runs are marked failed.")

        if running:
            self.error(section, f"{len(running)} latest import run(s) remain marked running.", running)
        else:
            self.pass_check(section, "No latest import runs remain marked running.")

        self.info(section, "Latest real-run summaries are available in verbose output.", summaries)

    def check_mapping_integrity(self):
        section = "Mapping integrity"
        broken_state = LegacyRecordMap.objects.filter(
            Q(action=LegacyRecordMap.ACTION_SKIPPED) &
            (Q(content_type__isnull=False) | Q(object_id__isnull=False))
        ) | LegacyRecordMap.objects.filter(
            ~Q(action=LegacyRecordMap.ACTION_SKIPPED) &
            (Q(content_type__isnull=True) | Q(object_id__isnull=True))
        )
        broken_state = broken_state.distinct()

        if broken_state.exists():
            details = [str(mapping) for mapping in broken_state[: self.sample_size]]
            self.error(section, f"{broken_state.count()} mappings have inconsistent action/target state.", details)
        else:
            self.pass_check(section, "All skipped and targeted mappings have consistent state.")

        missing_targets = []
        target_maps = LegacyRecordMap.objects.filter(
            content_type__isnull=False,
            object_id__isnull=False,
        ).select_related("content_type")

        grouped = defaultdict(list)
        for mapping in target_maps.iterator(chunk_size=2000):
            grouped[mapping.content_type_id].append(mapping)

        for mappings in grouped.values():
            model_class = mappings[0].content_type.model_class()
            if model_class is None:
                missing_targets.extend(
                    f"{m.legacy_table} #{m.legacy_id}: invalid content type"
                    for m in mappings
                )
                continue
            ids = {m.object_id for m in mappings}
            existing_ids = set(
                model_class._default_manager.filter(pk__in=ids).values_list("pk", flat=True)
            )
            missing_targets.extend(
                f"{m.legacy_table} #{m.legacy_id} -> "
                f"{m.content_type.app_label}.{m.content_type.model} #{m.object_id}"
                for m in mappings
                if m.object_id not in existing_ids
            )

        if missing_targets:
            self.error(section, f"{len(missing_targets)} mappings point to missing target objects.", missing_targets)
        else:
            self.pass_check(section, "All targeted mappings resolve to existing objects.")

    def check_project_job_mappings(self):
        section = "Imported job mappings"
        all_period_project_maps = self.project_maps.filter(
            object_id__in=Job.objects.filter(
                created__date__gte=self.start_date,
                created__date__lt=self.end_date,
            ).values("pk")
        )

        missing_count = all_period_project_maps.exclude(object_id__in=self.imported_job_ids).count()
        if missing_count:
            self.error(section, f"{missing_count} project mappings do not resolve to period Jobs.")
        else:
            self.pass_check(section, f"All {len(self.imported_job_ids):,} mapped period Jobs resolve correctly.")

        duplicate_targets = (
            all_period_project_maps.values("object_id")
            .annotate(total=Count("id"))
            .filter(total__gt=1)
        )
        if duplicate_targets.exists():
            details = [
                f"Job #{row['object_id']} has {row['total']} project mappings"
                for row in duplicate_targets[: self.sample_size]
            ]
            self.error(section, f"{duplicate_targets.count()} Jobs have multiple project mappings.", details)
        else:
            self.pass_check(section, "No imported Job is mapped from multiple legacy projects.")

    def check_jobs(self):
        section = "Jobs"
        jobs = list(self.imported_jobs)
        self.info(section, f"Auditing {len(jobs):,} imported Jobs.")

        required_checks = (
            ("customer", lambda job: job.customer_id is None, "customer", "ERROR"),
            ("style", lambda job: job.style_id is None, "style", "ERROR"),
            ("due date", lambda job: job.due is None, "due date", "ERROR"),
            ("barcode", lambda job: job.barcode is None, "barcode", "ERROR"),
            ("stock number", lambda job: not (job.stock_num or "").strip(), "stock number", "WARN"),
            ("status", lambda job: job.status_id is None, "status", "WARN"),
        )
        for _, predicate, label, severity in required_checks:
            affected = [job for job in jobs if predicate(job)]
            details = [self.job_label(job) for job in affected]
            if affected:
                getattr(self, "error" if severity == "ERROR" else "warn")(
                    section,
                    f"{len(affected)} imported Jobs are missing {label}.",
                    details,
                )
            else:
                self.pass_check(section, f"No imported Jobs are missing {label}.")

        normalized = defaultdict(list)
        for job in jobs:
            value = (job.stock_num or "").strip().casefold()
            if value:
                normalized[value].append(job)
        duplicate_groups = [group for group in normalized.values() if len(group) > 1]
        if duplicate_groups:
            details = [", ".join(self.job_label(job) for job in group) for group in duplicate_groups]
            self.error(section, f"{len(duplicate_groups)} normalized stock-number duplicate groups found.", details)
        else:
            self.pass_check(section, "No normalized duplicate stock numbers found.")

        assignment_mismatches = [
            job for job in jobs if job.assigned_to_id != job.holder_id
        ]
        if assignment_mismatches:
            details = [
                f"{self.job_label(job)}: assigned_to={job.assigned_to_id or '-'}, "
                f"holder={job.holder_id or '-'}"
                for job in assignment_mismatches
            ]
            self.warn(section, f"{len(assignment_mismatches)} Jobs have different assigned_to and holder values.", details)
        else:
            self.pass_check(section, "All imported Jobs have matching assigned_to and holder values.")

        customer_mismatches = [
            job for job in jobs
            if job.customer_id and job.style_id and job.style.customer_id
            and job.customer_id != job.style.customer_id
        ]
        if customer_mismatches:
            details = [
                f"{self.job_label(job)}: job customer={job.customer_id}, "
                f"style customer={job.style.customer_id}"
                for job in customer_mismatches
            ]
            self.warn(section, f"{len(customer_mismatches)} Jobs differ from their Style customer.", details)
        else:
            self.pass_check(section, "All imported Jobs match their Style customer where one is defined.")

    def check_job_metals(self):
        section = "Job metals"
        qs = JobMetal.objects.filter(job_id__in=self.imported_job_ids).select_related("job", "part", "metal_type")
        records = list(qs)
        self.info(section, f"Auditing {len(records):,} JobMetal records.")

        invalid = [record for record in records if (record.qty_req is not None and record.qty_req < 0) or (record.weight_req is not None and record.weight_req < 0)]
        if invalid:
            self.error(section, f"{len(invalid)} JobMetal records have negative requirements.", [f"JobMetal #{r.pk} on {self.job_label(r.job)}" for r in invalid])
        else:
            self.pass_check(section, "No JobMetal records have negative requirements.")

        duplicates = (
            qs.values("job_id", "part_id", "metal_type_id")
            .annotate(total=Count("id"))
            .filter(total__gt=1)
        )
        if duplicates.exists():
            details = [f"Job #{r['job_id']}, part #{r['part_id']}, metal type #{r['metal_type_id'] or '-'}: {r['total']} rows" for r in duplicates[: self.sample_size]]
            self.warn(section, f"{duplicates.count()} duplicate JobMetal component groups found.", details)
        else:
            self.pass_check(section, "No duplicate JobMetal component groups found.")

        self.check_component_mappings(section, self.job_metal_ct, records, ("project_part_number", "metal"))

    def check_job_stones(self):
        section = "Job stones"
        qs = JobStone.objects.filter(job_id__in=self.imported_job_ids).select_related("job", "stone_type", "stone_shape")
        records = list(qs)
        self.info(section, f"Auditing {len(records):,} JobStone records.")

        invalid = [record for record in records if record.qty_req < 0 or (record.stone_type_id is None and record.stone_shape_id is None) or len(record.stone_size or "") > 10]
        if invalid:
            self.error(section, f"{len(invalid)} JobStone records have invalid type, shape, size, or quantity.", [f"JobStone #{r.pk} on {self.job_label(r.job)}" for r in invalid])
        else:
            self.pass_check(section, "All JobStone records have valid type/shape, size, and quantity values.")

        duplicates = (
            qs.values("job_id", "stone_type_id", "stone_shape_id", "stone_size")
            .annotate(total=Count("id"))
            .filter(total__gt=1)
        )
        if duplicates.exists():
            details = [f"Job #{r['job_id']}, stone type #{r['stone_type_id'] or '-'}, shape #{r['stone_shape_id'] or '-'}, size {r['stone_size']!r}: {r['total']} rows" for r in duplicates[: self.sample_size]]
            self.warn(section, f"{duplicates.count()} duplicate JobStone component groups found.", details)
        else:
            self.pass_check(section, "No exact duplicate JobStone component groups found.")

        self.check_component_mappings(section, self.job_stone_ct, records, ("stone", "project_stone"))

    def check_job_findings(self):
        section = "Job findings"
        qs = JobFinding.objects.filter(job_id__in=self.imported_job_ids).select_related("job", "finding")
        records = list(qs)
        self.info(section, f"Auditing {len(records):,} JobFinding records.")

        invalid = [record for record in records if record.qty_req < 0 or record.qty_used < 0]
        if invalid:
            self.error(section, f"{len(invalid)} JobFinding records have negative quantities.", [f"JobFinding #{r.pk} on {self.job_label(r.job)}" for r in invalid])
        else:
            self.pass_check(section, "No JobFinding records have negative quantities.")

        nonzero_used = [record for record in records if record.qty_used != Decimal("0")]
        if nonzero_used:
            self.info(section, f"{len(nonzero_used)} imported-job findings currently have nonzero qty_used.", [f"JobFinding #{r.pk} on {self.job_label(r.job)}: qty_used={r.qty_used}" for r in nonzero_used])
        else:
            self.pass_check(section, "All imported-job findings currently have qty_used=0.")

        duplicates = (
            qs.values("job_id", "finding_id")
            .annotate(total=Count("id"))
            .filter(total__gt=1)
        )
        if duplicates.exists():
            details = [f"Job #{r['job_id']}, finding #{r['finding_id']}: {r['total']} rows" for r in duplicates[: self.sample_size]]
            self.warn(section, f"{duplicates.count()} duplicate JobFinding component groups found.", details)
        else:
            self.pass_check(section, "No duplicate JobFinding component groups found.")

        self.check_component_mappings(section, self.job_finding_ct, records, ("project_finding", "finding"))

    def check_component_mappings(self, section, content_type, records, legacy_tables):
        target_ids = {record.pk for record in records}
        maps = LegacyRecordMap.objects.filter(
            content_type=content_type,
            object_id__in=target_ids,
            legacy_table__in=legacy_tables,
        )
        mapped_target_ids = set(maps.values_list("object_id", flat=True))
        unmapped = sorted(target_ids - mapped_target_ids)
        if unmapped:
            self.info(section, f"{len(unmapped)} component records on imported Jobs have no recognized legacy mapping.", [f"{content_type.model} #{pk}" for pk in unmapped])
        else:
            self.pass_check(section, "Every component record on imported Jobs has a recognized legacy mapping.")

        missing_component_targets = LegacyRecordMap.objects.filter(
            content_type=content_type,
            legacy_table__in=legacy_tables,
            object_id__isnull=False,
        ).exclude(object_id__in=content_type.model_class().objects.values("pk"))
        if missing_component_targets.exists():
            self.error(section, f"{missing_component_targets.count()} component mappings point to deleted targets.", [str(m) for m in missing_component_targets[: self.sample_size]])

    def check_style_component_completeness(self):
        section = "Style component comparison"
        jobs = list(self.imported_jobs)

        style_metal_style_ids = set(StyleMetal.objects.values_list("style_id", flat=True).distinct())
        style_stone_style_ids = set(StyleStone.objects.values_list("style_id", flat=True).distinct())
        style_finding_style_ids = set(StyleFinding.objects.values_list("style_id", flat=True).distinct())

        jobs_with_metals = set(JobMetal.objects.filter(job_id__in=self.imported_job_ids).values_list("job_id", flat=True).distinct())
        jobs_with_stones = set(JobStone.objects.filter(job_id__in=self.imported_job_ids).values_list("job_id", flat=True).distinct())
        jobs_with_findings = set(JobFinding.objects.filter(job_id__in=self.imported_job_ids).values_list("job_id", flat=True).distinct())

        comparisons = (
            ("metals", style_metal_style_ids, jobs_with_metals),
            ("stones", style_stone_style_ids, jobs_with_stones),
            ("findings", style_finding_style_ids, jobs_with_findings),
        )
        for label, recipe_style_ids, component_job_ids in comparisons:
            missing = [job for job in jobs if job.style_id in recipe_style_ids and job.pk not in component_job_ids]
            if missing:
                self.warn(section, f"{len(missing)} Jobs use Styles with {label} but have no Job {label}.", [self.job_label(job) for job in missing])
            else:
                self.pass_check(section, f"No Jobs are missing all {label} from a Style recipe that defines them.")

    @staticmethod
    def job_label(job: Job) -> str:
        return f"Job #{job.pk} ({job.stock_num or 'no stock number'}, barcode={job.barcode or '-'})"

    def print_report(self):
        self.stdout.write("")
        current_section = None
        for finding in self.findings:
            if finding.section != current_section:
                current_section = finding.section
                self.stdout.write("")
                self.stdout.write(self.style.MIGRATE_HEADING(current_section))

            label = f"{finding.severity:<5}"
            if finding.severity == "ERROR":
                rendered = self.style.ERROR(label)
            elif finding.severity == "WARN":
                rendered = self.style.WARNING(label)
            elif finding.severity == "PASS":
                rendered = self.style.SUCCESS(label)
            else:
                rendered = self.style.NOTICE(label)
            self.stdout.write(f"{rendered} {finding.message}")

            if self.verbose and finding.details:
                for detail in finding.details:
                    self.stdout.write(f"      - {detail}")

        errors = sum(1 for finding in self.findings if finding.severity == "ERROR")
        warnings = sum(1 for finding in self.findings if finding.severity == "WARN")
        infos = sum(1 for finding in self.findings if finding.severity == "INFO")
        passes = sum(1 for finding in self.findings if finding.severity == "PASS")

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("-" * 68))
        if errors:
            result = "FAILED"
            rendered_result = self.style.ERROR(result)
        elif warnings:
            result = "PASSED WITH WARNINGS"
            rendered_result = self.style.WARNING(result)
        else:
            result = "PASSED"
            rendered_result = self.style.SUCCESS(result)
        self.stdout.write(f"RESULT: {rendered_result}")
        self.stdout.write(
            f"Checks: {passes} passed, {warnings} warnings, "
            f"{errors} errors, {infos} informational"
        )
        self.stdout.write(self.style.MIGRATE_HEADING("-" * 68))