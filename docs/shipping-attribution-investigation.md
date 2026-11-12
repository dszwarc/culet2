# Shipping attribution investigation: 1161318 and 1161317

Repository reviewed: HEAD `2087a3d`, November 12, 2026. No production database was accessed. Only tests and this report were added; application behavior is unchanged. Findings below describe this checkout, not the unknown deployed revision.

## Findings and code paths

| Path (file:function, line) | Trigger / job identification | Employee source | Shipment / batch | Atomicity and attribution implications |
| --- | --- | --- | --- | --- |
| `culet/views.py:BulkJobShipView.post`, 5127 → `culet/shipping.py:ship_jobs`, 19 | POST `/jobs/ship/`; separate barcode and stock textareas; split whitespace/commas; case-insensitive `.first()` lookups at 5200 and 5233 | `Employee(user=request.user)` at 5136; client-supplied employee IDs ignored | Creates one JobShip per job; all submitted jobs share one actor and notes | Service atomic; locks Jobs in PK order, rechecks shipped/record/work/piecework state; never overwrites shipment. Batch rollback on failure. Authenticated session identifies account, not physical operator. |
| `culet/services.py:move_job`, 704 | Called twice per shipped job to clear assigned_to and holder | Explicit `performed_by=employee` from service | Creates movement only when that employee field changes; no JobShip itself | Locks Job; participates in service transaction. No movement is expected when both fields were already null. Does not choose shipment actor from holder/assignee. |
| `culet/management/commands/backfill_2025_shipments.py:Command.handle`, 65 | Manual command; **all jobs due before cutoff**, default exclusive `2025-12-31`; optionally excludes active open work | Arbitrary `--shipped-by` username, resolved at 74 | Bulk `update_or_create` at 243; creates OR overwrites JobShip | One atomic write block, but job selection occurs before it without Job locks. Sets shipped/status; clears ownership. Replaces actor, timestamp and notes of existing shipments. Actor need not be command operator or physical shipper. No movement or dedicated execution audit. |
| `culet/management/commands/import_jobs.py:Command.import_job`, 134 | Legacy project mapping by old ID; new barcode uses project.id, stock uses customer_reference_number | No shipment actor; assignee/holder intentionally null | New Job receives legacy is_shipped flag and Shipped/Imported status; **no JobShip** | Atomic per job. Existing mapped Jobs locked and lifecycle fields excluded at 278; current reruns preserve shipping state. New imported shipped Job remains active=True. |
| `culet/management/commands/import_jobs_2025.py:Command` | Inherits import_jobs, dates default to calendar 2025 | Same as parent | Same as parent | No separate shipment writer; imported flag without JobShip can be intentional legacy state. |
| `culet/views.py:ChangeJobStatusView.post`, 7247, 7338 | POST `/change-status/`; list of barcodes; any active JobStatus by ID | No employee stored | Bulk updates status only; **can select Shipped without JobShip or shipped=True** | Atomic bulk_update, no row locks, no lifecycle checks or shipping audit; can also change a shipped Job to another status. |
| `culet/admin.py:JobShipAdmin`, 570; `JobAdmin`, 428 | Current admin | No write allowed to JobShip | Add/change/delete explicitly denied | Current admin cannot reattribute JobShip. Job shipped/status are readonly; restricted saves protect stale edits. Job.active is editable as of HEAD. |
| `culet/admin.py:EmployeeAdmin`, 148; `JobStatusAdmin`, 92; Django User admin | Admin edits to related identity/reference records | Employee.user can be changed; User names can be edited | No JobShip write, but existing shipments display the current linked identity | Standard admin permissions; historical identity is not snapshotted. Renaming a status changes its label for every linked Job. Seed/import reference-status helpers normalize/create statuses, without shipping Jobs or writing JobShip. |
| `culet/views.py:JobUpdateView`; `culet/shipping.py:save_job_form_fields`, 9; `culet/forms.py:JobForm`, 381 | Ordinary Job edit | No shipment actor | No shipping field exposed, no JobShip | Explicit form-field saves preserve lifecycle state even if form instance predates shipping. |
| `culet/models.py:Job.save`, 333; `JobShip`, 371; migrations 0056/0057 | ORM save/schema | Caller must provide Employee | No automatic shipment callback; JobShip.job is OneToOne after 0057 | Unique JobShip.job prevents duplicates with migrated schema, not overwrites. Job.save only allocates missing barcode. No shipping signals found; culet/signals.py concerns Style file cleanup. |
| Historical shipping view: initial `4c99ad2`, then textarea `93557d9`, pre-hardening `e0d1440^` | Earlier deployed shipping screen / POST | Authenticated user's Employee | Creates records in a batch | Initial version had no Job locks; later pre-hardening code locked Jobs but checked shipped/work before locking (piecework rechecked). Could race into unique violation/rollback; no evidence these versions swapped employees. Initial version assigned holder to shipper and did not set status. |
| Historical admin / import before `e0d1440` (October 7, 2026) | Admin forms and import reruns | Admin could choose any Employee for JobShip | Admin add/edit could create or overwrite attribution; imports changed flag/status but no JobShip | Standard admin permissions/transaction handling, no shipment actor binding. Stale Job edit/import could overwrite lifecycle state. Verify deployed version at incident time. |

Search covered tracked application code, templates, JavaScript, migrations, management commands and relevant Git history. No other direct JobShip writer, shipped=True writer, raw shipping SQL, import callback, or shipping signal was found in current source. Bundled ZIPs/backups are not established deployed code; their presence is not evidence of execution. Arbitrary shell/SQL operators remain outside what repository inspection can prove.

## Confirmed defects and capabilities

1. **Historical backfill can destroy original attribution.** `backfill_2025_shipments.py:243` supplies shipped_by, shipped_at and notes as update defaults, with no exclusion of existing or already shipped jobs. It can also create a record long after the physical shipment with a fabricated historical timestamp (default noon in configured timezone). This is the strongest code mechanism for an employee being credited without initiating shipment. Whether it affected these two jobs is unknown. A backfill note is supporting evidence, not proof; subsequent edits can remove it.
2. **Missing server permission enforcement.** `permissions.py:71` restricts `can_ship_jobs` to Super, and `context_processors.py:71` uses it for UI visibility. `BulkJobShipView` at `views.py:5088` and the route at `urls.py:61` enforce only login. An Employee without shipping permission can directly POST and ship. This credits that authenticated Employee, not an arbitrary victim, unless the victim's session/account is being used.
3. **Status can contradict actual shipment state.** `views.py:7275` accepts any active status and `views.py:7338` bulk-writes it without flag/record reconciliation or audit. This can show Shipped without a shipment, or another status despite a shipment; it does not itself explain an existing JobShip's actor.

## UI and unconfirmed hypotheses

`job_ship_bulk.html:13` is a conventional CSRF-protected POST form with two textareas and notes. No shipping-specific HTMX/AJAX, camera button, delayed job lookup, or per-scan auto-submit is present. Base globally loads `barcode_scanner.js`, but its click handler requires `.js-culet-scanner-btn`, absent from this page. Camera repeat suppression/auto-submit therefore does not participate in this checkout's shipping form. Global custom JS has no shipping submit handler.

USB scanning types into the focused textarea. Consecutive scans accumulate and **all entries in both fields ship on the next valid POST**, with the one authenticated actor. Scanner separators must match whitespace/comma parsing. An invalid submission deliberately retains all values; back navigation/browser restoration may also restore old text (not browser-tested). No resolved-job preview exists, and success reports only a count at `views.py:5336`, so the user cannot confirm identities from that message. Click confirmation at `job_ship_bulk.html:92` is attached to the button, not the form submit event; programmatic submission need not invoke it. These are opportunities for unintended batches, not proof of this incident.

Barcode and stock numbers are different namespaces. A value in the barcode field never falls back to stock. The same numeric value can belong to different jobs across namespaces; the wrong field can ship a different valid job and report success. Tests reproduce this. Barcode and stock are individually unique in current schema, so `.first()` is not inherently ambiguous for numeric stock values if those constraints are applied. Stock uniqueness is case-sensitive whereas lookup is case-insensitive, so alphanumeric case variants remain a possible ambiguity. Leading-zero/format differences should be checked against actual labels and submitted payloads. Import uses project.id, not legacy RFID, for physical labels.

Current ship_jobs binds one employee once per request, with no cross-request global employee or loop-variable reassignment. Distinct jobs/actors and concurrent same-job submissions are tested. Duplicate strings, barcode-plus-stock aliases to the same Job, already shipped flags, and existing shipment conflicts are rejected. Retrying after commit cannot overwrite actor; retry after rollback can win legitimately. Current row locks serialize cooperating shipping requests; unique constraint protects against independent inserts. Backfill does not honor the Job lock protocol and could race with normal shipping; not reproduced concurrently. Stock resolution occurs before service locks: concurrent edits to identifiers could leave the submitted identifier referring to a now-changed job. No evidence of such an edit yet.

Authentication uses Django SessionMiddleware and AuthenticationMiddleware (`settings/base.py:44`), with password-change middleware but no impersonation/rebinding found. Shared unlocked workstation, shared login, a previously open form submitted after switching login, or changed Employee→User mapping/name could explain apparent actor identity. No successful-shipping request audit, immutable shipment created_at/updated_at, original actor field, or full Job field history is defined. Employee display names are current User names. Exception middleware logs failures with account context; it does not prove successful shipment authorship. Django admin LogEntry may cover earlier admin changes only; absence of logs/movements is not proof of backfill.

## Exact read-only production evidence command

Run from the production application directory with its usual virtualenv and production environment. `manage.py` otherwise defaults to dev settings, so this command explicitly selects the repository's production settings. It uses a PostgreSQL read-only repeatable-read transaction, SELECTs only, and prints both UTC and America/New_York timestamps including offsets. Do not run any backfill/import command during this investigation.

```bash
python manage.py shell --settings=celjewelry.settings.production <<'PY'
import json
from datetime import datetime, timedelta, timezone as datetime_timezone
from zoneinfo import ZoneInfo
from django.apps import apps
from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q, Count
from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from culet.models import Job, JobShip, JobMovement, Activity, Employee, LegacyRecordMap, LegacyImportRun

local_zone = ZoneInfo('America/New_York')
def encode(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return {'naive_unexpected': value.isoformat()}
        return {'utc': value.astimezone(datetime_timezone.utc).isoformat(),
                'America/New_York': value.astimezone(local_zone).isoformat()}
    return str(value)
def emit(label, value):
    print(label + '\n' + json.dumps(value, default=encode, indent=2))
def rows(queryset):
    return list(queryset.values())

with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        cursor.execute('SHOW transaction_read_only')
        emit('transaction_read_only', cursor.fetchone())
        cursor.execute('SELECT CURRENT_TIMESTAMP')
        emit('snapshot_time', cursor.fetchone())
        cursor.execute("SELECT app, name, applied FROM django_migrations WHERE app='culet' ORDER BY applied, name")
        emit('culet_migrations', cursor.fetchall())
    emit('configuration', {'settings': settings.SETTINGS_MODULE, 'TIME_ZONE': settings.TIME_ZONE, 'USE_TZ': settings.USE_TZ})
    emit('model_fields', {model.__name__: [field.attname for field in model._meta.concrete_fields]
                          for model in (Job, JobShip, JobMovement, Activity, Employee)})
    targets = ['1161318', '1161317']
    jobs = Job.objects.filter(Q(stock_num__in=targets) | Q(barcode__in=[int(value) for value in targets])).order_by('pk')
    job_rows = rows(jobs)
    job_ids = [row['id'] for row in job_rows]
    emit('jobs_matching_stock_OR_barcode', job_rows)
    emit('stock_matches', {value: rows(Job.objects.filter(stock_num__iexact=value)) for value in targets})
    job_barcodes = [row['barcode'] for row in job_rows if row['barcode'] is not None]
    job_stocks = [row['stock_num'] for row in job_rows if row['stock_num']]
    numeric_stocks = [int(value) for value in job_stocks if str(value).isdigit()]
    emit('cross_namespace_collisions', rows(Job.objects.filter(
        Q(stock_num__in=[str(value) for value in job_barcodes]) | Q(barcode__in=numeric_stocks))))
    shipments = rows(JobShip.objects.filter(job_id__in=job_ids).order_by('shipped_at', 'pk'))
    movements = rows(JobMovement.objects.filter(job_id__in=job_ids).order_by('created_at', 'pk'))
    activities = rows(Activity.objects.filter(job_id__in=job_ids).order_by('start', 'pk'))
    emit('shipments', shipments)
    emit('shipment_pair_comparisons', [
        {'first_job_id': first['job_id'], 'second_job_id': second['job_id'],
         'same_employee': first['shipped_by_id'] == second['shipped_by_id'],
         'elapsed_seconds': (second['shipped_at'] - first['shipped_at']).total_seconds()}
        for index, first in enumerate(shipments) for second in shipments[index + 1:]])
    emit('movements', movements)
    emit('activities', activities)
    employee_ids = set()
    for row in job_rows + shipments + movements + activities:
        for key in ('assigned_to_id', 'holder_id', 'shipped_by_id', 'employee_id', 'performed_by_id', 'from_employee_id', 'to_employee_id'):
            if row.get(key) is not None:
                employee_ids.add(row[key])
    nearby = []
    for shipment in shipments:
        nearby.extend(rows(JobShip.objects.filter(
            shipped_at__gte=shipment['shipped_at'] - timedelta(minutes=15),
            shipped_at__lte=shipment['shipped_at'] + timedelta(minutes=15),
        ).order_by('shipped_at', 'pk')[:100]))
    emit('nearby_shipments_first_100_per_window', nearby)
    employee_ids.update(row['shipped_by_id'] for row in nearby)
    emit('employees_current_identity', list(Employee.objects.filter(pk__in=employee_ids).values(
        'id', 'user_id', 'active', 'user__username', 'user__first_name', 'user__last_name', 'user__is_active', 'role_id')))
    status_model = Job._meta.get_field('status').remote_field.model
    movement_model = JobMovement._meta.get_field('movement_type').remote_field.model
    emit('statuses', rows(status_model.objects.all()))
    emit('movement_types', rows(movement_model.objects.all()))
    activity_step_model = Activity._meta.get_field('step').remote_field.model
    emit('activity_steps', rows(activity_step_model.objects.filter(pk__in=[row['step_id'] for row in activities if row['step_id']])))
    emit('duplicate_shipments_global', list(JobShip.objects.values('job_id').annotate(total=Count('pk')).filter(total__gt=1)))
    emit('duplicate_nonnull_barcodes_global', list(Job.objects.exclude(barcode=None).values('barcode').annotate(total=Count('pk')).filter(total__gt=1)))
    emit('duplicate_nonnull_stocks_global', list(Job.objects.exclude(stock_num=None).exclude(stock_num='').values('stock_num').annotate(total=Count('pk')).filter(total__gt=1)))
    shipment_job_ids = JobShip.objects.values('job_id')
    checks = {
        'flag_true_without_shipment_legacy_possible': Job.objects.filter(shipped=True).exclude(pk__in=shipment_job_ids),
        'shipment_without_flag': Job.objects.filter(shipped=False, pk__in=shipment_job_ids),
        'status_shipped_flag_false': Job.objects.filter(status__name__iexact='Shipped', shipped=False),
        'flag_true_status_not_shipped': Job.objects.filter(shipped=True).exclude(status__name__iexact='Shipped'),
        'flag_true_active_true': Job.objects.filter(shipped=True, active=True),
    }
    emit('state_checks', {label: {'global_count': queryset.count(), 'target_jobs': rows(queryset.filter(pk__in=job_ids))}
                          for label, queryset in checks.items()})
    job_type = ContentType.objects.filter(app_label='culet', model='job').first()
    shipment_type = ContentType.objects.filter(app_label='culet', model='jobship').first()
    log_query = Q(pk__in=[])
    if job_type:
        log_query |= Q(content_type=job_type, object_id__in=[str(value) for value in job_ids])
    if shipment_type:
        log_query |= Q(content_type=shipment_type, object_id__in=[str(row['id']) for row in shipments])
    employee_type = ContentType.objects.filter(app_label='culet', model='employee').first()
    user_type = ContentType.objects.filter(app_label='auth', model='user').first()
    if employee_type:
        log_query |= Q(content_type=employee_type, object_id__in=[str(value) for value in employee_ids])
    if user_type:
        user_ids = Employee.objects.filter(pk__in=employee_ids).values_list('user_id', flat=True)
        log_query |= Q(content_type=user_type, object_id__in=[str(value) for value in user_ids])
    emit('admin_logs_if_any', list(LogEntry.objects.filter(log_query).order_by('action_time', 'pk').values(
        'id', 'action_time', 'user_id', 'user__username', 'content_type_id', 'object_id', 'action_flag', 'change_message')))
    mappings = LegacyRecordMap.objects.filter(content_type=job_type, object_id__in=job_ids) if job_type else LegacyRecordMap.objects.none()
    mapping_rows = rows(mappings)
    emit('legacy_job_mappings_latest_state', mapping_rows)
    emit('mapped_import_runs', rows(LegacyImportRun.objects.filter(pk__in=[row['import_run_id'] for row in mapping_rows if row['import_run_id']])))
    emit('recent_import_runs_first_20', rows(LegacyImportRun.objects.order_by('-started_at')[:20]))
    emit('registered_history_models', [model._meta.label for model in apps.get_models()
                                     if any(term in model._meta.model_name for term in ('history', 'audit', 'historical'))])
PY
```

This returns every concrete Job/JobShip/movement/activity field, including due, created, last_updated, ownership IDs, actor IDs, notes and timestamps; related identity/status/step tables supply names. No passwords, session tokens or database credentials are printed. If production lacks a referenced model/field/table, stop and share the error and migration/field output; do not migrate production. Output can include internal notes and employee names; share through an appropriate private channel.

The nearby-shipment output is bounded: a large historical batch may fill 100 rows. Equal timestamps or shared employee do not prove common request/batch. Legacy mappings store latest mapping state, not a complete import or shipment audit. The backfill command does not create a LegacyImportRun; absence from that table cannot rule it out. JobShip.shipped_at is editable and is not the record insertion time. Database constraints/migrations must be verified against deployed state before assuming uniqueness.

## Evidence still needed and recommendations

### Partial production output received November 12, 2026

The supplied attachment starts partway through the statuses output. It omits jobs, shipments, movements, activities, employee identities, cross-namespace checks and shipment-time comparisons. Consequently no shipment actor, shipment date, or relationship between the two requested stock numbers can yet be established from this attachment.

The returned duplicate checks are empty globally; each listed state check has no target-job matches. Global counts (2,740 shipped flags without shipments, one shipment without flag, 38 shipped flags with another status, and 1,512 shipped flags on active Jobs) describe separate integrity/legacy conditions, not demonstrated causes of this incident. Returned admin entries concern Employee/User changes, with no Job or JobShip admin entry shown. No registered history models were reported.

The selected Jobs include ID 2942 (legacy project 1054916) and ID 2944 (legacy project 1054918), imported July 20, 2026 around 21:03 EDT by import run 14. Their exact requested-stock mapping is missing. Job 2944's mapping explicitly records a duplicate legacy customer reference being made unique using the project ID. Inspection of the actual import revision `dcce052d5da2d15c38384160d85747a706b266fc` confirms `prepare_stock_num` appends `-<legacy project ID>` when an existing stock number conflicts. This is a concrete identifier-history lead, not evidence that a wrong job was shipped. Current unique-stock checks cannot rule out duplicate references before import. The import mapping does not establish that a historical shipment backfill ran or created either shipment.

Please return command output, the reported employee's username/Employee ID, approximate incident date/time and timezone, production deployed commit at that time, and how she noticed the attribution (report/admin/job screen). Confirm actual printed barcodes versus these stock numbers, workstation/login sharing, and whether backfill/imports or admin changes ran near the event. Preserve available deployment/command logs, successful HTTP POST access logs and historical backups; exception logs alone are incomplete. Do not retrieve or share raw session tokens. A prior backup may be the only way to establish an overwritten original actor.

Ranked next steps, **recommendations only, no fixes applied**:

1. Highest explanatory value / high evidence-loss severity: inspect backfill notes, due dates, noon timestamp clustering and operator execution history. After joint review, prevent existing shipment overwrites by default; record import/operator/source separately from physical shipper; add immutable creation and change audit metadata.
2. Confirmed authorization gap / high severity: enforce can_ship_jobs on server, with explicit product decision about authorized roles. Add denial tests when fixing.
3. Plausible wrong-job batch / medium severity: show resolved job identities before commit and in success; clear or explicitly review retained entries; validate identifier ambiguity and bind confirmation to resolved identities. Investigate actual posted payload/device behavior before attributing incident to scanner.
4. Confirmed state-integrity gap / medium severity: reserve Shipped status for shipping service, or explicitly distinguish administrative status changes and audit them. Do not automatically repair legacy flag-only records.
5. Historical deployment/concurrency uncertainty: verify e0d1440 deployment and migration 0057; keep lock protocol for every lifecycle writer, especially backfill; preserve historical admin logs. Current normal shipping tests do not establish safety of all historical versions.

## Local reproduction

Added `culet/test_shipping_investigation.py` to reproduce backfill replacement/retrospective creation, missing endpoint permission, status-only shipping, separate identifier namespaces, retained invalid batches, consecutive actors/bulk attribution, and competing employees. Existing `culet/test_shipping.py` covers duplicate scans/aliases, repeated shipment attempts, state conflicts, batch rollback, stale edits/imports and concurrent same-actor shipping. These tests document current behavior; passing a defect reproduction means the defect is present. Production conclusions remain pending returned evidence.

Validation on local PostgreSQL (host verified as local before testing): final investigation suite **8/8 pass**, including two competing employees with real row locking. Initial combined run (before adding the last two investigation tests) **18/19 pass**. The existing `test_admin_guards_and_stale_admin_save` fails at `culet/test_shipping.py:229` because it expects `active` absent from the admin form, while commit `1ecd1ee9` intentionally made it editable. That unrelated expectation was left unchanged. Existing duplicate/retry and same-actor concurrent tests passed. Django system checks pass. Production evidence command syntax was compiled locally; it has not been executed against production.
