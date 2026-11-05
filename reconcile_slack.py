
import logging
from datetime import datetime, timedelta

from app import app, refresh_callout_message
from coverage_db import connect, initialize_database

logging.basicConfig(level=logging.INFO)


def reconcile_slack(days_back=30):
    initialize_database()

    cutoff = (
        datetime.now().date() - timedelta(days=days_back)
    ).isoformat()

    with connect() as db:
        rows = db.execute(
            """
            SELECT id
            FROM callouts
            WHERE shift_date >= ?
              AND channel_id IS NOT NULL
              AND message_ts IS NOT NULL
            ORDER BY id
            """,
            (cutoff,),
        ).fetchall()

    successes = 0
    failures = 0

    for (callout_id,) in rows:
        try:
            refresh_callout_message(app.client, callout_id)
            successes += 1
            print(f"Synced callout #{callout_id}")

        except Exception:
            failures += 1
            logging.exception(
                "Failed to sync callout #%s", callout_id
            )

    print(
        f"Reconciliation finished: "
        f"{successes} succeeded, {failures} failed"
    )

    return failures


if __name__ == "__main__":
    raise SystemExit(1 if reconcile_slack() else 0)
