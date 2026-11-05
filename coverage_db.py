
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


BASE = Path(__file__).resolve().parent
DATABASE_FILE = Path(
    os.getenv("COVERAGE_DB_PATH", BASE / "coverage.db")
)


def open_connection():
    DATABASE_FILE.parent.mkdir(parents=True, exist_ok=True)

    db = sqlite3.connect(DATABASE_FILE, timeout=30)
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 30000")

    return db


def connect():
    return open_connection()


@contextmanager
def connection():
    db = open_connection()

    try:
        with db:
            yield db
    finally:
        db.close()


def initialize_database():
    with connection() as db:

        db.execute("""
            CREATE TABLE IF NOT EXISTS callouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slack_user_id TEXT NOT NULL,
                employee TEXT NOT NULL,
                worksheet TEXT NOT NULL,
                shift_date TEXT NOT NULL,
                desk TEXT NOT NULL,
                shift_start TEXT NOT NULL,
                shift_end TEXT NOT NULL,
                coverage_start TEXT NOT NULL,
                coverage_end TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN'
                    CHECK (
                        status IN (
                            'OPEN',
                            'PARTIAL',
                            'FILLED',
                            'CANCELLED'
                        )
                    ),
                channel_id TEXT,
                message_ts TEXT,
                created_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
        """)

        columns = {
            row[1]
            for row in db.execute("PRAGMA table_info(callouts)")
        }

        if "shift_type" not in columns:
            db.execute("""
                ALTER TABLE callouts
                ADD COLUMN shift_type TEXT NOT NULL
                    DEFAULT 'STUDENT_ASSISTANT'
                    CHECK (
                        shift_type IN (
                            'STUDENT_ASSISTANT',
                            'TUTOR'
                        )
                    )
            """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS callout_cells (
                callout_id INTEGER NOT NULL
                    REFERENCES callouts(id),
                worksheet TEXT NOT NULL,
                row INTEGER NOT NULL,
                column INTEGER NOT NULL,
                PRIMARY KEY (
                    callout_id,
                    row,
                    column
                )
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                callout_id INTEGER NOT NULL
                    REFERENCES callouts(id),
                slack_user_id TEXT NOT NULL,
                employee TEXT NOT NULL,
                coverage_start TEXT NOT NULL,
                coverage_end TEXT NOT NULL,
                created_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS tutor_callout_courses (
                callout_id INTEGER NOT NULL
                    REFERENCES callouts(id),
                course TEXT NOT NULL,
                PRIMARY KEY (
                    callout_id,
                    course
                )
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS tutor_claim_courses (
                claim_id INTEGER NOT NULL
                    REFERENCES claims(id),
                course TEXT NOT NULL,
                PRIMARY KEY (
                    claim_id,
                    course
                )
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                callout_id INTEGER NOT NULL
                    REFERENCES callouts(id),
                actor_slack_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS slack_sync (
                callout_id INTEGER PRIMARY KEY
                    REFERENCES callouts(id),
                revision INTEGER NOT NULL DEFAULT 0,
                synced_revision INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_retry INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS callout_reminders (
                callout_id INTEGER PRIMARY KEY
                    REFERENCES callouts(id),
                sent_at TEXT,
                locked_until INTEGER NOT NULL DEFAULT 0,
                next_retry INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_claims_callout
            ON claims(callout_id)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_callout_cells
            ON callout_cells(worksheet, row, column)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_callouts_employee_date
            ON callouts(employee, shift_date)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_callouts_type_date
            ON callouts(shift_type, shift_date)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_events_callout
            ON audit_events(callout_id)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_tutor_callout_courses
            ON tutor_callout_courses(course)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_tutor_claim_courses
            ON tutor_claim_courses(course)
        """)

        db.execute("""
            CREATE TRIGGER IF NOT EXISTS prevent_audit_update
            BEFORE UPDATE ON audit_events
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'Audit events cannot be modified'
                );
            END
        """)

        db.execute("""
            CREATE TRIGGER IF NOT EXISTS prevent_audit_delete
            BEFORE DELETE ON audit_events
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'Audit events cannot be deleted'
                );
            END
        """)

        db.execute("""
            CREATE TRIGGER IF NOT EXISTS sync_callout_created
            AFTER INSERT ON callouts
            BEGIN
                INSERT INTO slack_sync (
                    callout_id,
                    revision
                )
                VALUES (NEW.id, 1);
            END
        """)

        db.execute("""
            CREATE TRIGGER IF NOT EXISTS sync_callout_updated
            AFTER UPDATE OF status, channel_id, message_ts
            ON callouts
            BEGIN
                INSERT INTO slack_sync (
                    callout_id,
                    revision
                )
                VALUES (NEW.id, 1)
                ON CONFLICT(callout_id)
                DO UPDATE SET
                    revision = revision + 1,
                    next_retry = 0;
            END
        """)

        db.execute("""
            CREATE TRIGGER IF NOT EXISTS sync_claim_created
            AFTER INSERT ON claims
            BEGIN
                INSERT INTO slack_sync (
                    callout_id,
                    revision
                )
                VALUES (NEW.callout_id, 1)
                ON CONFLICT(callout_id)
                DO UPDATE SET
                    revision = revision + 1,
                    next_retry = 0;
            END
        """)

        db.execute("""
            CREATE TRIGGER IF NOT EXISTS sync_claim_released
            AFTER DELETE ON claims
            BEGIN
                INSERT INTO slack_sync (
                    callout_id,
                    revision
                )
                VALUES (OLD.callout_id, 1)
                ON CONFLICT(callout_id)
                DO UPDATE SET
                    revision = revision + 1,
                    next_retry = 0;
            END
        """)

        db.execute("""
            CREATE TRIGGER IF NOT EXISTS sync_tutor_callout_course_created
            AFTER INSERT ON tutor_callout_courses
            BEGIN
                INSERT INTO slack_sync (
                    callout_id,
                    revision
                )
                VALUES (NEW.callout_id, 1)
                ON CONFLICT(callout_id)
                DO UPDATE SET
                    revision = revision + 1,
                    next_retry = 0;
            END
        """)

        db.execute("""
            CREATE TRIGGER IF NOT EXISTS sync_tutor_claim_course_created
            AFTER INSERT ON tutor_claim_courses
            BEGIN
                INSERT INTO slack_sync (
                    callout_id,
                    revision
                )
                SELECT callout_id, 1
                FROM claims
                WHERE id = NEW.claim_id
                ON CONFLICT(callout_id)
                DO UPDATE SET
                    revision = revision + 1,
                    next_retry = 0;
            END
        """)

        db.execute("""
            CREATE TRIGGER IF NOT EXISTS sync_tutor_claim_course_deleted
            AFTER DELETE ON tutor_claim_courses
            BEGIN
                INSERT INTO slack_sync (
                    callout_id,
                    revision
                )
                SELECT callout_id, 1
                FROM claims
                WHERE id = OLD.claim_id
                ON CONFLICT(callout_id)
                DO UPDATE SET
                    revision = revision + 1,
                    next_retry = 0;
            END
        """)

        db.execute("""
            INSERT OR IGNORE INTO slack_sync (
                callout_id,
                revision
            )
            SELECT id, 1
            FROM callouts
            WHERE message_ts IS NOT NULL
        """)


if __name__ == "__main__":
    initialize_database()
    print("Coverage database initialized:", DATABASE_FILE)
