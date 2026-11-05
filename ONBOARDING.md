# Shift Coverage Bot — Staff and Schedule Onboarding

This is an administrator guide for the current Fall 2026 implementation. Only designated bot maintainers should operate the staff management utility.

## Before adding staff

1. Verify the bot uses a private repository and that `.env`, `employees.json`, `mentors.json`, staff schedules, `coverage.db`, and backups are excluded from Git.
2. Have an authorized maintainer configure the Slack bot and run `import_employees.py`. When prompted, enter the Slack channel ID containing the staff to import. The bot must have the required permissions to list channel members and read user profiles.
3. Ensure both the student-assistant and Morning Drop-In schedules are present in their expected locations. The latest upload did **not** contain `tutor_schedule_reader.py`, so its exact source path and time parsing rules need verification before declaring the tutor schedule ready.
4. Run the validation report: `python validate_employees.py`.

## Staff management without editing JSON

Run `python staff_admin.py` (or `\.venv\Scripts\python.exe staff_admin.py` on Windows). Use the numbered menu to:

- View existing mentors.
- Grant or revoke mentor privileges using an employee's displayed name.
- Match an imported Slack account with an **exact existing name** from the Excel schedules.
- Run the existing employee matching report.

The program backs up the previous JSON file as `.json.bak` and writes changes atomically. It does not edit the Excel files, database records, or Slack profiles. Mentors are managed independently of employee schedule roles.

**Permissions:** This is a local administrative command. Anyone with write access to the config files or permission to run it can change mentor access. Restrict access to the bot host and configuration directory. Do not expose this utility as a Slack command without additional authorization controls.

**Name matching:** Slack display names do not have to equal schedule names. Instead, imported Slack user IDs are mapped to the exact names in Excel. If two people share the same name on the schedule, the current name-based schedule system cannot distinguish them; resolve that ambiguity before proceeding. Name remapping does not rewrite old callout history.

## Student-assistant workbook format

The current `schedule_reader.py` expects:

- Local filename: `schedule.xlsx` next to the Python files.
- A `Template` worksheet and weekly worksheets named like `Oct 26 - Oct 30` (month abbreviations supported by the reader; spaces tolerated).
- The parser currently hardcodes `SCHEDULE_YEAR = 2026`; update/refactor this before Spring 2027 deployment.
- Monday–Friday sections where column A is `Monday`, `Mondays`, `Tuesday`, `Tuesdays`, etc.
- Thirty-minute row labels in column A such as `9:00-9:30 AM` or `11:30-12:00 PM`.
- Employee assignments as plain names in columns B–F, corresponding to B–C Tutoring, D CS Help Desk, E Chem/Orgo Drop-in, and F Math Lab. The parser uses **column positions**, not the visible column headings.
- Names of assigned people must match the staff administration mapping (case and leading/trailing whitespace are ignored, but shortened names and nicknames are not automatically resolved).
- `OPEN`, `TBD`, and `VACANT` are treated as placeholders in the existing validation report.

Changing columns, worksheet naming, time increments, or the layout requires updating the parser and tests—not just the README.

## Morning Drop-In tutor workbook format

The provided example workbook contains `Schedule by Course`, `Schedule by Tutor`, and `Front Facing Calendar` tabs. The existing name-validation script reads `Schedule by Tutor` with:

- Weekday headings in column A (e.g. `Monday`).
- Tutor name in column A, comma-separated courses in column B, and a time range in column C.
- Rows such as `John Duffy | CHEM 1211K, BIOS 1107 | 9:30 AM - 10:30 AM`.

This describes what `validate_employees.py` reads. Full tutor scheduling and claiming depend on `tutor_schedule_reader.py`, which was not included in the latest uploaded set. Its exact formats must be verified before treating the layout as a production contract.

## Validation and go-live

Run:

```powershell
.\.venv\Scripts\python.exe validate_employees.py
.\.venv\Scripts\python.exe -m unittest discover -p "test_*.py" -v
```

Check for unexpected unmatched scheduled employees, duplicate canonical names, missing workbooks, and errors. Test `/callout` for an SA and a tutor, one claim/release per type, and authorized versus unauthorized `/callout-digest` in the test channel before enabling production.

**Current limitations:** No graphical setup wizard, automated Slack import from within `staff_admin.py`, formal workbook-schema validation, or schedule freshness enforcement is installed by this document. Do not assume that passing a name-match report confirms the full worksheet structure.
