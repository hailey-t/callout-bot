
import json
from pathlib import Path


EMPLOYEES_FILE = Path(__file__).resolve().parent / "employees.json"


def get_employee(slack_user_id):
    with open(EMPLOYEES_FILE, encoding="utf-8") as file:
        employees = json.load(file)

    employee = employees.get(slack_user_id)

    if not isinstance(employee, str) or not employee.strip():
        raise PermissionError("Slack user is not a registered employee.")

    return employee


def require_employee(slack_user_id, employee):
    registered_employee = get_employee(slack_user_id)

    if registered_employee != employee:
        raise PermissionError(
            "Slack identity does not match the supplied employee."
        )

    return registered_employee

MENTORS_FILE = Path(__file__).resolve().parent / "mentors.json"


def require_mentor(slack_user_id):
    with open(MENTORS_FILE, encoding="utf-8") as file:
        data = json.load(file)

    mentor_ids = (
        data if isinstance(data, list)
        else data.get("mentor_ids", [])
    )

    if slack_user_id not in mentor_ids:
        raise PermissionError(
            "You are not authorized to view employee callout digests."
        )

    return slack_user_id