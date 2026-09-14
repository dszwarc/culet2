# Piecework Activity investigation — September 14, 2026

## Finding

The suspected line-return regression is **not reproduced in this checkout or the configured local database**. Commit `0d8fe04c5bd5af0d4988c628ee91b28a96f1d4d3` (August 6, 2026) moved Activity creation from `PieceworkReturnView.post` into `services.return_piecework_lines`; it did not remove it. Both implementations create completed Piecework Activities. No missing lookup, signal dependency, bulk-update bypass, or broken Activity condition was found in the return workflow. This does not establish what code/data is deployed on a different production server.

The real structural gap was the absence of an explicit relationship identifying the Activity for a particular line. Timestamp matching was required for audits. The change adds that relationship and a conservative repair tool; it is hardening, not evidence of a confirmed creation regression.

## Lifecycle and semantics

- `PieceworkCreateView.post`, `PieceworkMemoCreateForm`, `PieceworkScanForm`, and `piecework/create.html`: validate scans and job eligibility, lock jobs, create the memo and all lines in the creation transaction. There is no application workflow to append a later line to an existing memo. Line admin disallows additions and deletion.
- Creation calls `move_job` with `assigned` and `received`, sets location to Piecework, sets `is_piecework`, clears `in_work`, and records `Job.piecework_assigned_at`.
- `PieceworkMemoLine` has **no created_at field**, including before migration 0090. `memo.created_at` is the supported, historical period start. Do not invent a line timestamp. `Job.piecework_assigned_at` is mutable and cleared on return, so it cannot safely reconstruct historical starts.
- `PieceworkOpenListView`, `MyPieceworkListView`, `OpenPieceworkFilter`, `piecework/open.html`, `piecework/my_piecework.html`, and `PieceworkPrintView` use line return status/counts. `piecework/return.html` posts selected `line_ids` to `PieceworkReturnView.post`. One line and multiple selected lines use the same service; returning all open lines is the whole-memo equivalent.
- `return_piecework_lines` locks memo, selected lines, and jobs inside `transaction.atomic`. It rejects duplicate IDs, stale/foreign selections, completed memos, shipped jobs, active work, missing reference data, and now negative periods or an already-linked open line.
- Each selected line receives one Activity: job=line.job, employee=memo.assigned_to, step code=`piecework`, start=memo.created_at, end=operation timestamp subsequently saved as line.returned_at, is_piecework=True, active=False. `Activity.save` computes duration=end-start and fills name from the step.
- The same transaction calls `move_job` for `returned-to-manager` (assignment) and `returned` (holder), clears piecework/work flags and assignment timestamp, saves line.returned_at/returned_by/activity, and closes memo.returned_at/returned_by only when no open lines remain. Neither signals nor memo.save creates Activities.
- Retry requests return existing validation feedback and create no Activity or movement. A second legitimate memo for the same Job produces another distinct Activity.
- Migration 0090 copied memo return timestamps/returner onto historical completed lines. It did not generate Activities. Integrity/consistency commands audit or repair state flags, not Activity history. Direct ORM/bulk updates remain outside the workflow contract.

## Implementation

- `culet/models.py` and migration `0092_piecework_line_activity.py`: nullable, non-editable `PieceworkMemoLine.activity` OneToOneField with PROTECT. Unique linkage prevents sharing an Activity between periods; PROTECT prevents casually deleting a linked Activity. Nullable permits migration without guessing historical matches and allows open lines.
- `culet/services.py`: saves the created Activity link with line completion in the existing transaction; validates chronology and corrupt open links. Existing row locks serialize retries.
- `culet/piecework_activities.py`: strict historical matching and ambiguity classification.
- `culet/management/commands/backfill_piecework_activities.py`: dry run, CSV, conservative creation/linking, per-line transaction and memo/line/job locks. Existing exact Activities are linked on real runs. No job state, movements, or historical Activity timestamps are rewritten.
- `culet/management/commands/audit_piecework_integrity.py`: directs Activity audits to the new command.
- `culet/test_piecework_activities.py`: regression, rollback, report/history, repeated period, matching, CSV and command tests.

The relationship guarantees at most one linked Activity per line. Application returns guarantee its creation on completion. Arbitrary direct database writes can still mark a line returned without an Activity; the nullable migration deliberately does not forbid historical unresolved rows. No schema migration or repair was applied to the configured application database.

## Read-only historical audit

Configured **local** database: 9 returned lines; 9 Piecework Activities; 6 safely linkable exact matching lines; 0 confidently missing/eligible lines; 3 ambiguous lines; 0 skipped lines; 0 duplicate-suspected period cases. Seven lines have exact timestamp matches in isolation, but one is held for cross-period review. The two slightly different Activities account for the other two lines; no Activity is confidently missing.

| Memo / line | Activity | Difference |
| --- | --- | --- |
| PW-000002 / 2 | 8 | Activity start is 22.641 ms after memo creation |
| PW-000004 / 4 | 9 | Activity start is 16.825 ms after memo creation |
| PW-000008 / 8 | 11 (exact), 9 (older period) | Same Job has unresolved older Activity 9, so conservative audit holds this line too |

All other checked fields match for the first two, and both Activities are completed. The command deliberately does not link or rewrite fuzzy matches. A reviewer can verify the original assignment timestamps before deciding whether to link them. Earlier whole-memo code used `job.piecework_assigned_at or memo.created_at`; commit `10d7ffc` (July 30, 2026) changed it to memo.created_at before line returns were introduced. This plausibly explains the millisecond differences but the original mutable job timestamp is no longer recoverable. Job/employee foreign keys are protected on memo/line records; historical manual changes to those values cannot be disproved from this schema alone.

CSV: `/private/tmp/piecework-activity-audit-reviewed-20260914.csv`. The line_created_at column is empty because that field does not exist; start contains memo.created_at. The audit works before migration 0092 without attempting to read its absent column.

Movement inspection found return movements for some historical jobs, not a complete pair for every returned line. This alone is not a regression: `move_job` explicitly skips no-op changes, and historical job state at return is not fully reconstructable. Current workflow tests verify two movements when assignment and holder actually change and no duplicates on retry. The repair command does not invent historical movements.

## Repair safety and operation

Only complete, nonnegative periods with the Piecework step and no possible conflicting Activity/overlapping line are eligible. Exact matching checks job, employee, step, piecework flag, inactive state, start/end and stored duration. Unexplained disjoint Activities on the same Job are also held for review; only an exact match to another period permits ignoring them. Overlapping or same-boundary candidate Activities, corrupt links, overlapping lines and duplicates are reported for review. Distinct nonoverlapping periods for the same Job are supported.

Run from the repository containing manage.py:

```sh
env/bin/python manage.py backfill_piecework_activities --dry-run --output /private/tmp/piecework-before.csv
# Deployment step: applies schema only; no historical Activity creation.
env/bin/python manage.py migrate culet
# Explicit repair: creates eligible Activities and links exact existing matches.
env/bin/python manage.py backfill_piecework_activities --output /private/tmp/piecework-repair.csv
env/bin/python manage.py backfill_piecework_activities --dry-run --output /private/tmp/piecework-after.csv
```

Output paths must be new. Dry run performs no database mutations, including links. A real run commits per line; if interrupted or an output write fails, rerun with a new output path. Previously committed links prevent duplicate creation. Avoid concurrent manual editing/deletion of historical data during repair. In this local dataset a real run would create **zero** Activities and link six exact matches; three records remain for manual review.

## Report impact

- Employee Activity report (`EmployeeActivityReportView`): includes completed Activities by employee/end date/style. Missing periods omit rows and duration. Separately, its existing `sum()` starts at integer zero and can fail for timedelta durations; this pre-existing report issue is not an Activity creation failure and was left outside this change.
- Step Duration / style operation report (`StyleStepTimeReportView`): counts and total elapsed durations would be understated; averages may move either direction. Piecework duration is elapsed time away, including nights/weekends, not actual hands-on labor.
- Job detail combined Events (`get_job_history`, Job detail/history templates): missing Activity event and elapsed duration, independently of movements.
- TimeClockReportView: includes Activities without excluding piecework for eligible clock-in employees. Missing periods reduce summed job labor and potentially merged active-work coverage/utilization, increasing computed downtime. Clock entries themselves are unchanged.
- Payroll HTML/XLSX (`payroll.build_payroll_report`): uses TimeClock, not Activity; raw/rounded/overtime payroll hours are unaffected.
- Activity index, clocked-in idle report's last-activity fields: missing history. Current active-work counters use active=True/end=NULL and should not change when completed piecework is restored. No separate persisted style-duration/productivity recalculation was found.

No reports were modified. No demonstrated missing-period understatement was found in this local dataset.

## Validation results

- Initial existing piecework/integrity suite: **57/57 passed**.
- Final focused run: `env/bin/python manage.py test culet.test_piecework_activities culet.test_piecework_integrity culet.test_job_history culet.test_payroll --noinput --keepdb` — **102 tests, 101 passed, one failure**. All 13 new Activity/backfill tests, all 57 piecework/integrity tests, and all 25 payroll tests passed. The remaining failure is `JobHistoryTests.test_nullable_values_render_without_errors_and_action_is_preserved`, which expects a “Stop Work” action. The new completed Piecework duration-report/combined-events integration test passed.
- Broader run: `env/bin/python manage.py test culet --noinput --keepdb` — **224 tests, 219 passed, five failures**, before four additional focused tests were added. Failures were the above history UI expectation; `JobWeightCreateTests.test_missing_previous_component_displays_placeholder` and `test_uses_most_recent_weight_across_all_steps` (expected g, rendered dwt); `MyJobsRunningTimerTests.test_poll_reflects_inprocess_repair_closing_other_employee_work` (password-change redirect); and `test_poll_requires_authentication` (login URL slash mismatch). These assertions concern UI/auth behavior outside the changed return/backfill path; no claim is made that the whole suite is green.
- `manage.py check`: passed. `makemigrations --check --dry-run`: no changes detected (sandbox prevented migration-history DB check, but actual PostgreSQL test migrations succeeded). `git diff --check`: passed.
- Test logs: `/private/tmp/culet-piecework-focused-tests.log`, `/private/tmp/culet-piecework-full-tests.log`.
- Existing and concurrent edits in forms, views, filters, and the integrity test file were preserved. No production repair, application-database migration, commit, or deployment was performed.

## Live database evidence supplied by the user

The user ran a read-only first-pass audit on DigitalOcean and supplied its output after the local investigation. Live counts: **934 returned lines, 933 Piecework Activities, 919 exact matching lines, 15 lines without an exact match**. These are first-pass counts, not the more conservative command's classifications; exact matches were not checked for competing overlapping periods by that script.

Of the 15 non-exact lines, **14 have a completed Activity matching job, employee, step and end, with a small positive start offset** consistent with the older `job.piecework_assigned_at` behavior. They are lines 7, 8, 48, 53–62, and 64. All returned on July 29, 2026, before the August 6 line-return refactor. These should not receive newly created duplicate Activities. The offsets are 39.930 ms for memo 2, 42.254 ms for memo 7, 123.260 ms for memo 12, and 49.663 ms for memo 14.

**Line 49, memo 8, Job 1765 is the substantive unresolved case.** Its recorded period is July 23 14:54:59.433791 UTC through July 29 20:32:05 UTC, employee 31. The only supplied Piecework Activity for that Job is Activity 808, employee 45, July 29 20:13:16.740610 through 20:13:42.704374 UTC. That Activity matches line 64/memo 14 apart from the legacy start offset, not line 49. Thus line 49 appears to lack its own Piecework Activity. Its recorded interval overlaps line 64 and ends later, so automatic reconstruction remains unsafe without reviewing the two memo records and Job history. The user-supplied output does not establish why it was marked returned or whether its timestamps/assignment are accurate.

This live evidence does not support a widespread Activity-creation regression after the line-return refactor. It identifies an older potentially missing or inconsistent period plus legacy timestamp differences. No live data has been modified; a focused read-only history query is the next step. Do not use aggregate count difference alone to authorize a repair.

## Job detail display fix and focused live history

The subsequent DigitalOcean output confirms Activity 808 is the later employee-45 period, with no Activity representing line 49's employee-31 period. Line 49 and its memo have no returned_by. Movements show the Job reassigned and received by other employees on July 27, before the recorded July 29 return. The origin of that inconsistent completion remains unknown; its full recorded interval cannot safely be treated as verified piecework time. No repair is authorized by this evidence alone.

The Job detail query already included Piecework Activities. The display problem is that the combined history sorted every Activity by start, so a long Piecework period appeared at its assignment date behind intervening movements and could disappear from the first ten rows. This explains poor discoverability, not missing database records. For the supplied Job, many later movements also place the July Activity well down the history.

Updated `get_job_history` to position completed Piecework Activities at their end/return time, while preserving start ordering for other Activities and open Piecework Activities. Added All events, Activities, Piecework (with count), and Movements filters on Job detail. Filtering happens before pagination; Load 10 More preserves the chosen filter. The shared row template explicitly labels Piecework returns and shows both Start and End alongside duration and employee. Historical Activities require no new line link to appear, and missing Activities are not fabricated from memo rows.

Files for this display change: `culet/services.py`, `culet/views.py`, `culet/templates/jobs/detail.html`, `culet/templates/jobs/partials/history_rows.html`, `culet/test_job_history.py`.

Validation: Job history plus Piecework Activity tests ran **24 tests: 23 passed, one previously observed failure** expecting the absent “Stop Work” action. All four newly added display/filter/pagination tests passed, as did all 13 Piecework Activity tests. Ordinary activity ordering, stable pagination, and repeated Piecework periods remain covered. `git diff --check` passed. Test log: `/private/tmp/piecework-history-tests.log`. Changes remain local and have not been deployed; no live data was modified.
