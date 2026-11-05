
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from slack_sdk import WebClient


BASE = Path(__file__).resolve().parent
EMPLOYEES_FILE = BASE / "employees.json"

load_dotenv(BASE / ".env")

client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])


def import_employees(channel_id):
    employees = {}

    if EMPLOYEES_FILE.exists():
        employees = json.loads(
            EMPLOYEES_FILE.read_text(encoding="utf-8")
        )

    cursor = None
    imported = 0
    skipped = 0

    while True:
        response = client.conversations_members(
            channel=channel_id,
            limit=200,
            cursor=cursor,
        )

        for user_id in response["members"]:
            user = client.users_info(user=user_id)["user"]

            if user.get("deleted") or user.get("is_bot"):
                skipped += 1
                continue

            profile = user.get("profile", {})

            name = (
                profile.get("real_name")
                or user.get("real_name")
                or user.get("name")
            )

            if not name:
                skipped += 1
                continue

            if user_id not in employees:
                employees[user_id] = name.strip()
                imported += 1

        cursor = response.get(
            "response_metadata", {}
        ).get("next_cursor")

        if not cursor:
            break

    backup = EMPLOYEES_FILE.with_suffix(".json.bak")

    if EMPLOYEES_FILE.exists():
        backup.write_bytes(EMPLOYEES_FILE.read_bytes())

    EMPLOYEES_FILE.write_text(
        json.dumps(
            dict(sorted(employees.items())),
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )

    print(f"New employees imported: {imported}")
    print(f"Users skipped: {skipped}")
    print(f"Total registered employees: {len(employees)}")
    print(f"Saved to: {EMPLOYEES_FILE}")


if __name__ == "__main__":
    channel_id = input("Enter Slack channel ID: ").strip()
    import_employees(channel_id)
