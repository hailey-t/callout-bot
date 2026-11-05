# Shift Coverage Bot — local development version

Slack Bolt (Socket Mode), local read-only Excel export, and SQLite. **This is not connected to the live SharePoint schedule**; do not treat Slack claims as confirmed Excel updates until an approved integration exists.

## Setup

1. Copy source files into your project. **Back up and preserve your existing `.env`, `employees.json`, and `coverage.db` first.** Do not overwrite your actual employee mapping with the example in this archive.
2. `py -3.14 -m venv .venv`
3. `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`
4. Add Slack tokens and test channel ID to `.env` (see `.env.example`).
5. Ensure `schedule.xlsx` is a current approved export of the original Excel workbook.
6. Map employee Slack user IDs to exact schedule names in `employees.json`. Add authorized **mentor Slack user IDs** in `mentors.json` under `mentor_ids`.
7. `.\.venv\Scripts\python.exe coverage_db.py` (adds the new audit table without deleting existing records).
8. In your Slack app settings under **Slash Commands**, create `/callout-digest` just as you created `/callout`; Socket Mode does not need a public request URL. Reinstall the app if Slack prompts.
9. `.\.venv\Scripts\python.exe app.py`

## Functionality

- `/callout`: select a week, assigned shift, and full or partial coverage window.
- `Claim Coverage`: select uncovered 30-minute intervals, with conflict checks against the weekly schedule, template, and accepted claims.
- `Cancel Callout`: requester-only; only before the requested coverage starts and **only if there are no active claims**. Claimants must release coverage first.
- `Release ...`: claimant-only; only before that particular claim begins. Released hours reopen. Claim changes and cancellations are logged in `audit_events`.
- `/callout-digest`: mentor-only (server-side Slack ID allowlist), private modal. Choose employee and semester/month. **After requested coverage ends**, any uncovered portion counts as **one** penalty-eligible callout; fully covered and cancelled requests do not count. Pending requests don't count yet. Two occurrences flag mentor review, not automatic discipline.

## Test

`\.venv\Scripts\python.exe test_accountability.py`

Tests use a temporary database and do not touch your existing `coverage.db`. You can test with fictional employees and dates. Run `test_post.py` only if you intend to send a test message.

## Constraints and safeguards

- Authorization is enforced in SQLite service functions, not just by hiding buttons. Slack buttons may be visible to others, but unauthorized actions are rejected.
- Cancelling a callout with claims is blocked to avoid silently revoking another person's commitment. For exceptions after a shift begins, a future mentor override/audit workflow is needed.
- The digest covers **records entered into this bot**, not historical callouts that exist only in SharePoint/Slack.
- SQLite is authoritative for status; Slack API updates can fail after a database transaction, leaving a stale visible message until reconciled. Monitor bot logs; reconciliation is not yet automated.
- The `Template` conflict check is intentionally conservative and may incorrectly reject coverage on exceptional/holiday weeks. An immutable original weekly snapshot is a future improvement.
- The local Excel export can become stale. Refresh it using an approved process before using this in production.
- A synced OneDrive folder is not ideal for an actively written SQLite database. Set `COVERAGE_DB_PATH` to a stable **local, non-sync** path before deployment and back it up regularly.
- Keep student-specific accountability reports private and restrict mentor membership deliberately.
