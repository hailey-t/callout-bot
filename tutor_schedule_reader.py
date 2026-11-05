
import re
from datetime import datetime
from pathlib import Path
from datetime import timedelta
from schedule_reader import week_dates, DAYS

from openpyxl import load_workbook


WORKBOOK = Path(__file__).resolve().parent / "morning_drop_in.xlsx"

DAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
)


def parse_time(value):
    return datetime.strptime(
        value.strip().upper(),
        "%I:%M %p" if ":" in value else "%I %p",
    ).strftime("%H:%M")


def parse_hours(value):
    parts = re.split(r"\s*[-–]\s*", str(value).strip())

    if len(parts) != 2:
        raise ValueError(f"Invalid tutor shift: {value}")

    start, end = map(parse_time, parts)

    if not ("09:00" <= start < end <= "13:00"):
        raise ValueError(
            f"Tutor shift outside Morning Drop-In hours: {value}"
        )

    return start, end


def get_tutor_shifts(employee=None, workbook_path=WORKBOOK):
    wb = load_workbook(
        workbook_path,
        read_only=True,
        data_only=True,
    )

    try:
        ws = wb["Schedule by Tutor"]
        shifts = []
        day = None

        for row in ws.iter_rows(
            min_col=1,
            max_col=3,
            values_only=True,
        ):
            name, courses_text, hours = row

            if isinstance(name, str):
                name = name.strip()
            else:
                continue

            if name in DAYS:
                day = name
                continue

            if name == "Tutor" or not day:
                continue

            if not courses_text or not hours:
                continue

            if employee and name.casefold() != employee.strip().casefold():
                continue

            start, end = parse_hours(hours)

            courses = list(dict.fromkeys(
                course.strip().upper()
                for course in str(courses_text).split(",")
                if course.strip()
            ))

            shifts.append({
                "employee": name,
                "role": "TUTOR",
                "day": day,
                "location": "Morning Drop In",
                "start": start,
                "end": end,
                "courses": courses,
            })

        return shifts

    finally:
        wb.close()


def get_tutor_courses(employee, workbook_path=WORKBOOK):
    courses = set()

    for shift in get_tutor_shifts(employee, workbook_path):
        courses.update(shift["courses"])

    return sorted(courses)

def get_tutor_week_shifts(employee, worksheet):
    monday, _ = week_dates(worksheet)
    shifts = get_tutor_shifts(employee)

    return [
        {
            **shift,
            "date": (
                monday + timedelta(days=DAYS[shift["day"]])
            ).isoformat(),
            "desk": "Morning Drop In",
        }
        for shift in shifts
    ]


if __name__ == "__main__":
    shifts = get_tutor_shifts("John Duffy")

    for shift in shifts:
        print(
            shift["day"],
            shift["start"],
            shift["end"],
            ", ".join(shift["courses"]),
        )
