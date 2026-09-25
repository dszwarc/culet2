# Piecework memo operations

Each newly created memo requires one ActivityStep. The creation form and Django
admin validate against ActivitySteps excluding codes `piecework` and `repair`.
The database FK remains nullable and PROTECTed for legacy records. Admin makes
an existing memo's operation read-only to avoid contradicting its returned lines.

Migration `0094_piecework_memo_activity_step` adds only the nullable FK. It does
not backfill memo operations or alter historical Activities. Apply it before
serving the updated application. It has not been applied to the working database
as part of implementation.

All interactive returns flow through `return_piecework_lines`, including partial
and multiple-line returns. It uses the locked memo's operation, falling back to
code `piecework` only for a null operation. Employee, timestamps, elapsed duration,
return attribution, movement, assignment, and holder behavior remain unchanged.
The separate audit/backfill command also recognizes the recorded operation;
its historical reconciliation remains conservative and never rewrites Activities.
No backfill command was run against working data.

Creation displays employee selection, memo operation/details, then job scanning.
The existing select widget and responsive layout are reused. Open, return, and
print pages display the operation or “Operation not recorded”.

## Progress compatibility

`get_job_progress` and `with_job_progress_data` include completed, inactive
Activities with an end and step, regardless of `is_piecework`. Cleaning therefore
counts when it belongs to a supported progress department (Jewelry, Polishing,
Polishing 37, or Setting). Generic `piecework` and `repair` remain excluded from
the progress step catalog. No progress redesign or style ordering was added.

## Reports requiring a separate decision

These existing reports do not exclude `is_piecework=True`:

- `ReportingListView`: total and average Activity duration include piecework by
  default; its generic ActivityFilter exposes `is_piecework` for manual filtering.
- `EmployeeActivityReportView`: employee Activity duration totals.
- `TimeClockReportView`: elapsed Activity intervals feed job labor hours,
  active-work coverage, downtime, and utilization. Piecework can inflate labor
  and utilization and reduce reported downtime.
- `StyleStepTimeReportView`: average and total duration by style and ActivityStep.
  Actual-operation piecework now shares the operation group with internal work,
  so these averages must not be interpreted as internal processing time.

These reports were inspected but not changed, as requested. Manufacturing history
continues to show piecework as evidence that an operation happened.

## Current ActivityStep choices

Read-only inspection of the configured database found Adding Findings (`addfind`),
After Setter (`afterset`), Assembly (`assm`), Cleaning (`clean`), Final Polish
(`finalpol`), Inspection (`qc`), Piecework (`piecework`), Polish before stamp
(`polstamp`), Pre-polish (`prepol`), Pre-polish for set (`prepolset`), Repair
(`repair`), Set center(s) (`setcenter`), and Set melee (`setmel`).

Only the two requested codes are excluded. Inspection (`qc`) is a candidate for
future exclusion because Culet has a dedicated quality-inspection workflow; this
change does not decide whether external inspection is valid piecework. No other
obviously inappropriate current steps were identified, and no allowlist was added.

## Changed files and validation

- `culet/models.py` and `culet/migrations/0094_piecework_memo_activity_step.py`:
  nullable protected memo operation.
- `culet/forms.py` and `culet/admin.py`: required creation validation and admin
  protection for existing operations.
- `culet/services.py`: shared line-return operation selection.
- `culet/piecework_activities.py` and
  `culet/management/commands/backfill_piecework_activities.py`: operation-aware
  reconciliation, including distinct operations on successive periods for a job,
  while retaining compatibility with auditing older database schemas.
- `culet/views.py`: fetch operation relations for display without per-row queries.
- `culet/templates/piecework/create.html`, `open.html`, `return.html`, `print.html`,
  and `culet/templates/memos/memo_print.html`: selector and operation display.
- `culet/test_piecework_operations.py`: eight new tests covering rejected
  operations, valid creation, scan deduplication, repeat assignment rejection,
  partial/bulk returns, completed flags, elapsed timestamps, attribution and
  movements, historical preservation, legacy fallback, progress, admin validation,
  and reconciliation across operations.
- `culet/test_piecework_integrity.py`: existing creation helper submits Cleaning.
- `docs/piecework-operations.md`: implementation and investigation notes.

Validation: 96 tests passed in the initial run of operation, integrity, Activity,
and progress suites. After the final reconciliation changes and an additional
regression test, all 21 operation/Activity tests passed. There are 97 distinct tests
across these runs. `manage.py check`, `makemigrations --check --dry-run`, and
`git diff --check` passed. Tests used a separate PostgreSQL test database.
