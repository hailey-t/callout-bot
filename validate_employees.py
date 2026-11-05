
import json
import re
from difflib import get_close_matches
from pathlib import Path

from openpyxl import load_workbook

from schedule_reader import (
    SCHEDULE_FILE,
    DESKS,
    DAYS,
    parse_interval,
)
from tutor_schedule_reader import WORKBOOK as TUTOR_FILE


BASE = Path(__file__).resolve().parent
EMPLOYEES_FILE = BASE / "employees.json"


def normalize(name):
    return re.sub(r"\s+", " ", name.strip()).casefold()


def student_assistant_names():
    names = set()
    wb = load_workbook(SCHEDULE_FILE, read_only=True, data_only=True)

    try:
        # Template includes recurring assignments.
        # Weekly sheets include any changes to those assignments.
        for ws in wb.worksheets:
            current_day = None

            for row in ws.iter_rows(values_only=True):
                first = row[0] if row else None

                if isinstance(first, str):
                    label = first.strip()

                    if label in DAYS:
                        current_day = label
                        continue

                    if label.endswith("s") and label[:-1] in DAYS:
                        current_day = label[:-1]
                        continue

                if current_day is None or parse_interval(first) is None:
                    continue

                for column in DESKS:
                    if len(row) < column:
                        continue

                    name = row[column - 1]

                    if (
                        isinstance(name, str)
                        and name.strip()
                        and name.strip().upper() not in {"OPEN", "TBD", "VACANT"}
                    ):
                        names.add(name.strip())

    finally:
        wb.close()

    return names


def tutor_names():
    names = set()
    wb = load_workbook(TUTOR_FILE, read_only=True, data_only=True)

    try:
        ws = wb["Schedule by Tutor"]
        current_day = None

        for name, courses, hours in ws.iter_rows(
            min_col=1,
            max_col=3,
            values_only=True,
        ):
            if isinstance(name, str) and name.strip() in DAYS:
                current_day = name.strip()
                continue

            if (
                current_day
                and isinstance(name, str)
                and name.strip()
                and courses
                and hours
            ):
                if name.strip().casefold() not in {"tutor", "open", "tbd"}:
                    names.add(name.strip())

    finally:
        wb.close()

    return names


def show_unmatched(title, names, registered):
    print(f"\n{title} ({len(names)})")
    print("-" * 60)

    if not names:
        print("None")
        return

    normalized = {
        normalize(name): name
        for name in registered
    }

    for name in sorted(names):
        suggestions = get_close_matches(
            normalize(name),
            normalized,
            n=3,
            cutoff=0.65,
        )

        print(f"  {name}")

        if suggestions:
            matches = [
                normalized[key]
                for key in suggestions
            ]
            print(f"    Possible matches: {', '.join(matches)}")


def validate():
    employees = json.loads(
        EMPLOYEES_FILE.read_text(encoding="utf-8")
    )

    sa_names = student_assistant_names()
    tutor_name_set = tutor_names()

    registered = {
        normalize(name): name
        for name in employees.values()
    }

    sa_lookup = {normalize(name): name for name in sa_names}
    tutor_lookup = {normalize(name): name for name in tutor_name_set}

    sa_keys = set(sa_lookup)
    tutor_keys = set(tutor_lookup)
    registered_keys = set(registered)

    sa_matches = sa_keys & registered_keys
    tutor_matches = tutor_keys & registered_keys

    both_roles = sa_keys & tutor_keys

    missing_sa = sa_keys - registered_keys
    missing_tutor = tutor_keys - registered_keys

    no_schedule = registered_keys - (sa_keys | tutor_keys)

    print("\nEMPLOYEE VALIDATION REPORT")
    print("=" * 60)

    print(f"Registered Slack users:       {len(employees)}")
    print(f"Student assistants in Excel:  {len(sa_keys)}")
    print(f"Tutors in Excel:              {len(tutor_keys)}")
    print(f"Matched student assistants:   {len(sa_matches)}")
    print(f"Matched tutors:               {len(tutor_matches)}")
    print(f"Present in both schedules:    {len(both_roles)}")

    print("\nEMPLOYEES IN BOTH SCHEDULES")
    print("-" * 60)

    if both_roles:
        for key in sorted(both_roles):
            status = (
                "REGISTERED" if key in registered_keys
                else "NOT REGISTERED"
            )
            print(f"  {sa_lookup[key]} — {status}")
    else:
        print("None found")

    show_unmatched(
        "STUDENT ASSISTANTS WITHOUT MATCHING SLACK NAME",
        [sa_lookup[key] for key in missing_sa],
        employees.values(),
    )

    show_unmatched(
        "TUTORS WITHOUT MATCHING SLACK NAME",
        [tutor_lookup[key] for key in missing_tutor],
        employees.values(),
    )

    print(f"\nREGISTERED USERS WITHOUT SCHEDULE MATCH ({len(no_schedule)})")
    print("-" * 60)

    if no_schedule:
        for key in sorted(no_schedule):
            print(f"  {registered[key]}")
    else:
        print("None")

    duplicate_names = {}

    for user_id, name in employees.items():
        duplicate_names.setdefault(normalize(name), []).append(user_id)

    duplicates = {
        name: ids
        for name, ids in duplicate_names.items()
        if len(ids) > 1
    }

    print(f"\nDUPLICATE REGISTERED NAMES ({len(duplicates)})")
    print("-" * 60)

    if duplicates:
        for name, ids in sorted(duplicates.items()):
            print(f"  {name}: {', '.join(ids)}")
    else:
        print("None")

    print("\nNo files were modified.")


if __name__ == "__main__":
    validate()
