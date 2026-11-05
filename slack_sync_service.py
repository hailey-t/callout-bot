
import logging
import time

from coverage_db import connection


def synchronize_pending_messages(client, refresh):
    now = int(time.time())

    with connection() as db:
        pending = db.execute(
            """
            SELECT s.callout_id, s.revision, s.attempts
            FROM slack_sync s
            JOIN callouts c ON c.id = s.callout_id
            WHERE s.revision > s.synced_revision
              AND s.next_retry <= ?
              AND c.channel_id IS NOT NULL
              AND c.message_ts IS NOT NULL
            ORDER BY s.callout_id
            LIMIT 50
            """,
            (now,),
        ).fetchall()

    for callout_id, revision, attempts in pending:
        try:
            refresh(client, callout_id)

            with connection() as db:
                db.execute(
                    """
                    UPDATE slack_sync
                    SET synced_revision = ?,
                        attempts = 0,
                        next_retry = 0,
                        last_error = NULL
                    WHERE callout_id = ?
                      AND revision = ?
                    """,
                    (revision, callout_id, revision),
                )

        except Exception as error:
            delay = min(300, 2 ** min(attempts + 1, 8))

            with connection() as db:
                db.execute(
                    """
                    UPDATE slack_sync
                    SET attempts = attempts + 1,
                        next_retry = ?,
                        last_error = ?
                    WHERE callout_id = ?
                      AND revision = ?
                    """,
                    (
                        int(time.time()) + delay,
                        str(error)[:500],
                        callout_id,
                        revision,
                    ),
                )

            logging.exception(
                "Slack sync failed for callout #%s",
                callout_id,
            )
