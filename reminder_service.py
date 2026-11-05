
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from coverage_db import connection
from callout_service import (
    get_callout_state,
    get_tutor_callout_state,
    time_label,
)
from coverage_display import format_tutor_remaining


TZ = ZoneInfo("America/New_York")


def send_due_reminders(client):
    now = datetime.now(TZ)
    timestamp = int(time.time())

    with connection() as db:
        rows = db.execute(
            """
            SELECT id, shift_date, coverage_start
            FROM callouts
            WHERE status IN ('OPEN', 'PARTIAL')
              AND channel_id IS NOT NULL
              AND message_ts IS NOT NULL
            """
        ).fetchall()

    for callout_id, shift_date, coverage_start in rows:
        start = datetime.fromisoformat(
            f"{shift_date}T{coverage_start}"
        ).replace(tzinfo=TZ)

        if not (start - timedelta(hours=24) <= now < start):
            continue

        with connection() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO callout_reminders (callout_id)
                VALUES (?)
                """,
                (callout_id,),
            )

            reserved = db.execute(
                """
                UPDATE callout_reminders
                SET locked_until = ?
                WHERE callout_id = ?
                  AND sent_at IS NULL
                  AND locked_until <= ?
                  AND next_retry <= ?
                """,
                (
                    timestamp + 120,
                    callout_id,
                    timestamp,
                    timestamp,
                ),
            ).rowcount

        if not reserved:
            continue

        try:
            state = get_callout_state(callout_id)
            callout = state["callout"]

            if callout["shift_type"] == "TUTOR":
                state = get_tutor_callout_state(callout_id)
                callout = state["callout"]

            remaining = state["remaining"]

            if (
                callout["status"] not in ("OPEN", "PARTIAL")
                or not remaining
            ):
                with connection() as db:
                    db.execute(
                        """
                        UPDATE callout_reminders
                        SET locked_until = 0,
                            next_retry = ?
                        WHERE callout_id = ?
                        """,
                        (timestamp + 300, callout_id),
                    )
                continue

            link = client.chat_getPermalink(
                channel=callout["channel_id"],
                message_ts=callout["message_ts"],
            )["permalink"]

            if callout["shift_type"] == "TUTOR":
                uncovered = format_tutor_remaining(remaining)

                message = (
                    "<!channel> *Morning Drop-In coverage reminder*\n"
                    f"*Date:* {callout['shift_date']}\n"
                    f"*Original tutor:* <@{callout['slack_user_id']}>\n"
                    f"*Requested hours:* "
                    f"{time_label(callout['coverage_start'])}–"
                    f"{time_label(callout['coverage_end'])}\n"
                    f"*Remaining course coverage:*\n"
                    f"{uncovered}\n"
                    f"<{link}|View original callout>"
                )

            else:
                uncovered = "\n".join(
                    f"• {time_label(slot['start'])}–"
                    f"{time_label(slot['end'])}"
                    for slot in remaining
                )

                message = (
                    "<!channel> *Coverage reminder — shift approaching*\n"
                    f"*Date:* {callout['shift_date']}\n"
                    f"*Desk:* {callout['desk']}\n"
                    f"*Original employee:* "
                    f"<@{callout['slack_user_id']}>\n"
                    f"*Remaining uncovered hours:*\n"
                    f"{uncovered}\n"
                    f"<{link}|View and claim coverage>"
                )

            client.chat_postMessage(
                channel=callout["channel_id"],
                text=message,
            )

            with connection() as db:
                db.execute(
                    """
                    UPDATE callout_reminders
                    SET sent_at = ?,
                        locked_until = 0,
                        next_retry = 0
                    WHERE callout_id = ?
                    """,
                    (
                        datetime.now(TZ).isoformat(),
                        callout_id,
                    ),
                )

            print(
                f"24-hour reminder posted for callout #{callout_id}"
            )

        except Exception:
            logging.exception(
                "Reminder failed for callout #%s",
                callout_id,
            )

            with connection() as db:
                db.execute(
                    """
                    UPDATE callout_reminders
                    SET locked_until = 0,
                        attempts = attempts + 1,
                        next_retry = ?
                    WHERE callout_id = ?
                    """,
                    (
                        int(time.time()) + 300,
                        callout_id,
                    ),
                )
