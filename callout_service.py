import json
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from coverage_db import connect, connection
from identity import require_employee, require_mentor
from schedule_reader import DAYS, get_employee_shifts, week_dates
from tutor_schedule_reader import get_tutor_courses, get_tutor_week_shifts

TZ = ZoneInfo("America/New_York")


def minutes(value):
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def clock(value):
    return f"{value // 60:02d}:{value % 60:02d}"


def time_label(value):
    return datetime.strptime(value, "%H:%M").strftime("%I:%M %p").lstrip("0")


def shift_date(worksheet, day):
    monday, _ = week_dates(worksheet)
    return (monday + timedelta(days=DAYS[day])).isoformat()


def _start_datetime(day, start):
    return datetime.combine(
        date.fromisoformat(day),
        datetime.strptime(start, "%H:%M").time(),
        tzinfo=TZ,
    )


def record_audit(db, callout_id, actor_id, event_type, details=None):
    db.execute(
        """INSERT INTO audit_events (callout_id,actor_slack_id,event_type,details)
           VALUES (?,?,?,?)""",
        (callout_id, actor_id, event_type, json.dumps(details or {}, sort_keys=True)),
    )


def get_uncovered_intervals(callout, claims):
    start = minutes(callout["coverage_start"])
    end = minutes(callout["coverage_end"])
    covered = set()
    for claim in claims:
        covered.update(range(minutes(claim["coverage_start"]), minutes(claim["coverage_end"]), 30))
    intervals = []
    beginning = None
    for t in range(start, end, 30):
        if t not in covered and beginning is None:
            beginning = t
        elif t in covered and beginning is not None:
            intervals.append({"start": clock(beginning), "end": clock(t)})
            beginning = None
    if beginning is not None:
        intervals.append({"start": clock(beginning), "end": clock(end)})
    return intervals


def get_callout_state(callout_id):
    with connection() as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM callouts WHERE id=?", (callout_id,)).fetchone()
        if row is None:
            raise ValueError("Callout not found.")
        callout = dict(row)
        claims = [dict(r) for r in db.execute(
            "SELECT * FROM claims WHERE callout_id=? ORDER BY coverage_start,id", (callout_id,)
        )]
    return {
        "callout": callout,
        "claims": claims,
        "remaining": get_uncovered_intervals(callout, claims),
    }


def tutor_coverage_from_db(db, callout):
    from tutor_coverage import tutor_coverage_status
    callout_id = callout["id"]
    courses = [r[0] for r in db.execute(
        "SELECT course FROM tutor_callout_courses WHERE callout_id=? ORDER BY course", (callout_id,)
    )]
    rows = db.execute(
        """SELECT c.id,c.coverage_start,c.coverage_end,tc.course
           FROM claims c JOIN tutor_claim_courses tc ON tc.claim_id=c.id
           WHERE c.callout_id=? ORDER BY c.id""", (callout_id,)
    ).fetchall()
    grouped = {}
    for claim_id, start, end, course in rows:
        item = grouped.setdefault(claim_id, {"start": start, "end": end, "courses": []})
        item["courses"].append(course)
    return tutor_coverage_status(
        courses, callout["coverage_start"], callout["coverage_end"], list(grouped.values())
    )


def get_tutor_callout_state(callout_id):
    from tutor_coverage import tutor_coverage_status
    with connection() as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM callouts WHERE id=?", (callout_id,)).fetchone()
        if row is None or row["shift_type"] != "TUTOR":
            raise ValueError("Tutor callout not found.")
        callout = dict(row)
        courses = [r[0] for r in db.execute(
            "SELECT course FROM tutor_callout_courses WHERE callout_id=? ORDER BY course", (callout_id,)
        )]
        claims = [dict(r) for r in db.execute(
            "SELECT * FROM claims WHERE callout_id=? ORDER BY coverage_start,id", (callout_id,)
        )]
        for claim in claims:
            claim["courses"] = [r[0] for r in db.execute(
                "SELECT course FROM tutor_claim_courses WHERE claim_id=? ORDER BY course", (claim["id"],)
            )]
    coverage = tutor_coverage_status(
        courses, callout["coverage_start"], callout["coverage_end"], [
            {"start": c["coverage_start"], "end": c["coverage_end"], "courses": c["courses"]}
            for c in claims
        ],
    )
    return {
        "callout": callout, "claims": claims, "courses": courses,
        "remaining": coverage["remaining"], "coverage": coverage,
    }


def check_employee_availability(employee, worksheet, day, start, end):
    for source in (worksheet, "Template"):
        schedule = get_employee_shifts(source, employee)
        for shift in schedule["shifts"]:
            if (
                shift["day"] == day
                and minutes(shift["start"]) < minutes(end)
                and minutes(shift["end"]) > minutes(start)
            ):
                raise ValueError(
                    f"You are scheduled at {shift['desk']} "
                    f"{shift['start']}–{shift['end']} ({source})."
                )


def check_tutor_claim_availability(employee, worksheet, shift_date_value, coverage_start, coverage_end):
    day = date.fromisoformat(shift_date_value).strftime("%A")
    check_employee_availability(employee, worksheet, day, coverage_start, coverage_end)
    for shift in get_tutor_week_shifts(employee, worksheet):
        if shift["date"] != shift_date_value:
            continue
        if minutes(shift["start"]) < minutes(coverage_end) and minutes(shift["end"]) > minutes(coverage_start):
            raise ValueError(
                f"You already have a Morning Drop-In shift from {shift['start']} to {shift['end']}."
            )


def _existing_commitment(db, slack_user_id, shift_date_value, start, end):
    return db.execute(
        """SELECT c.id FROM claims c JOIN callouts o ON o.id=c.callout_id
           WHERE c.slack_user_id=? AND o.shift_date=? AND o.status!='CANCELLED'
           AND c.coverage_start < ? AND c.coverage_end > ? LIMIT 1""",
        (slack_user_id, shift_date_value, end, start),
    ).fetchone()


def create_callout(slack_user_id, employee, worksheet, shift_index, coverage_start, coverage_end):
    require_employee(slack_user_id, employee)
    schedule = get_employee_shifts(worksheet, employee)
    shifts = schedule["shifts"]
    if not 0 <= shift_index < len(shifts):
        raise ValueError("Invalid shift selection.")
    shift = shifts[shift_index]
    start, end = minutes(coverage_start), minutes(coverage_end)
    if start % 30 or end % 30 or not (minutes(shift["start"]) <= start < end <= minutes(shift["end"])):
        raise ValueError("Coverage times must fall within your shift on 30-minute boundaries.")
    actual_date = shift_date(worksheet, shift["day"])
    if _start_datetime(actual_date, coverage_start) <= datetime.now(TZ):
        raise ValueError("Cannot create a callout for a period that has already started.")
    affected = [
        c for c in shift["cells"]
        if minutes(c["start"]) >= start and minutes(c["end"]) <= end
    ]
    if len(affected) * 30 != end - start:
        raise ValueError("The requested hours do not match contiguous assigned cells.")
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        duplicate = db.execute(
            """SELECT id FROM callouts WHERE slack_user_id=? AND shift_date=?
               AND status!='CANCELLED' AND coverage_start < ? AND coverage_end > ? LIMIT 1""",
            (slack_user_id, actual_date, coverage_end, coverage_start),
        ).fetchone()
        if duplicate:
            raise ValueError(f"An overlapping callout already exists (request #{duplicate[0]}).")
        cursor = db.execute(
            """INSERT INTO callouts
               (slack_user_id,employee,worksheet,shift_date,desk,shift_start,shift_end,coverage_start,coverage_end)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (slack_user_id, employee, worksheet, actual_date, shift["desk"], shift["start"],
             shift["end"], coverage_start, coverage_end),
        )
        callout_id = cursor.lastrowid
        db.executemany(
            "INSERT INTO callout_cells (callout_id,worksheet,row,column) VALUES (?,?,?,?)",
            [(callout_id, worksheet, c["row"], c["column"]) for c in affected],
        )
        record_audit(db, callout_id, slack_user_id, "CALLOUT_CREATED", {
            "employee": employee, "worksheet": worksheet, "date": actual_date,
            "desk": shift["desk"], "start": coverage_start, "end": coverage_end,
        })
        db.commit()
        return get_callout_state(callout_id)["callout"]
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def create_tutor_callout(slack_user_id, employee, worksheet, shift_index, coverage_start, coverage_end):
    from tutor_coverage import normalize_courses
    require_employee(slack_user_id, employee)
    shifts = get_tutor_week_shifts(employee, worksheet)
    if not 0 <= shift_index < len(shifts):
        raise ValueError("Invalid tutor shift selection.")
    shift = shifts[shift_index]
    start, end = minutes(coverage_start), minutes(coverage_end)
    if start % 30 or end % 30 or not (minutes(shift["start"]) <= start < end <= minutes(shift["end"])):
        raise ValueError("Invalid tutor coverage hours.")
    actual_date = shift["date"]
    if _start_datetime(actual_date, coverage_start) <= datetime.now(TZ):
        raise ValueError("Cannot call out after the requested coverage begins.")
    courses = normalize_courses(shift["courses"])
    if not courses:
        raise ValueError("Tutor has no assigned courses.")
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        duplicate = db.execute(
            """SELECT id FROM callouts WHERE slack_user_id=? AND shift_date=?
               AND status!='CANCELLED' AND coverage_start < ? AND coverage_end > ? LIMIT 1""",
            (slack_user_id, actual_date, coverage_end, coverage_start),
        ).fetchone()
        if duplicate:
            raise ValueError(f"An overlapping callout already exists (request #{duplicate[0]}).")
        cursor = db.execute(
            """INSERT INTO callouts
               (slack_user_id,employee,worksheet,shift_date,desk,shift_start,shift_end,
                coverage_start,coverage_end,shift_type)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (slack_user_id, employee, worksheet, actual_date, "Morning Drop In",
             shift["start"], shift["end"], coverage_start, coverage_end, "TUTOR"),
        )
        callout_id = cursor.lastrowid
        db.executemany(
            "INSERT INTO tutor_callout_courses (callout_id,course) VALUES (?,?)",
            [(callout_id, course) for course in courses],
        )
        record_audit(db, callout_id, slack_user_id, "TUTOR_CALLOUT_CREATED", {
            "employee": employee, "worksheet": worksheet, "date": actual_date,
            "location": "Morning Drop In", "start": coverage_start,
            "end": coverage_end, "courses": courses,
        })
        db.commit()
        return {
            "id": callout_id, "employee": employee, "shift_date": actual_date,
            "shift_type": "TUTOR", "coverage_start": coverage_start,
            "coverage_end": coverage_end, "courses": courses, "status": "OPEN",
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def save_slack_message(callout_id, channel_id, ts):
    with connection() as db:
        db.execute(
            "UPDATE callouts SET channel_id=?, message_ts=? WHERE id=?",
            (channel_id, ts, callout_id),
        )


def cancel_unposted_callout(callout_id):
    with connection() as db:
        row = db.execute(
            "SELECT slack_user_id,status FROM callouts WHERE id=? AND message_ts IS NULL",
            (callout_id,),
        ).fetchone()
        if row is None or row[1] == "CANCELLED":
            return
        db.execute(
            "UPDATE callouts SET status='CANCELLED' WHERE id=? AND message_ts IS NULL",
            (callout_id,),
        )
        record_audit(db, callout_id, row[0], "CALLOUT_POST_FAILED", {
            "reason": "Slack message was not recorded as posted."
        })


def claim_callout(callout_id, slack_user_id, employee, coverage_start, coverage_end):
    require_employee(slack_user_id, employee)
    db = connect()
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM callouts WHERE id=?", (callout_id,)).fetchone()
        if row is None:
            raise ValueError("Callout not found.")
        callout = dict(row)
        if callout["shift_type"] != "STUDENT_ASSISTANT":
            raise ValueError("Tutor coverage must use course-specific claiming.")
        if callout["status"] not in ("OPEN", "PARTIAL") or not callout["message_ts"]:
            raise ValueError("This callout is no longer available.")
        if callout["slack_user_id"] == slack_user_id:
            raise ValueError("You cannot claim your own callout.")
        start, end = minutes(coverage_start), minutes(coverage_end)
        if start % 30 or end % 30 or not (
            minutes(callout["coverage_start"]) <= start < end <= minutes(callout["coverage_end"])
        ):
            raise ValueError("Claim is outside requested hours or not on 30-minute boundaries.")
        if _start_datetime(callout["shift_date"], coverage_start) <= datetime.now(TZ):
            raise ValueError("Claims must be made before the coverage begins.")
        claims = [dict(r) for r in db.execute("SELECT * FROM claims WHERE callout_id=?", (callout_id,))]
        remaining = get_uncovered_intervals(callout, claims)
        if not any(minutes(x["start"]) <= start and end <= minutes(x["end"]) for x in remaining):
            raise ValueError("These hours are already covered. Reopen the claim form.")
        day = date.fromisoformat(callout["shift_date"]).strftime("%A")
        check_employee_availability(employee, callout["worksheet"], day, coverage_start, coverage_end)
        if _existing_commitment(db, slack_user_id, callout["shift_date"], coverage_start, coverage_end):
            raise ValueError("You already claimed overlapping coverage.")
        cursor = db.execute(
            """INSERT INTO claims (callout_id,slack_user_id,employee,coverage_start,coverage_end)
               VALUES (?,?,?,?,?)""",
            (callout_id, slack_user_id, employee, coverage_start, coverage_end),
        )
        claims.append({"coverage_start": coverage_start, "coverage_end": coverage_end})
        remaining = get_uncovered_intervals(callout, claims)
        status = "PARTIAL" if remaining else "FILLED"
        db.execute("UPDATE callouts SET status=? WHERE id=?", (status, callout_id))
        record_audit(db, callout_id, slack_user_id, "COVERAGE_CLAIMED", {
            "claim_id": cursor.lastrowid, "employee": employee,
            "start": coverage_start, "end": coverage_end, "status_after": status,
        })
        db.commit()
        return {"status": status, "remaining": remaining}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def claim_tutor_coverage(callout_id, slack_user_id, employee, coverage_start, coverage_end, courses):
    from tutor_coverage import normalize_courses
    require_employee(slack_user_id, employee)
    courses = normalize_courses(courses)
    if not courses:
        raise ValueError("Select at least one course.")
    start, end = minutes(coverage_start), minutes(coverage_end)
    if start >= end or start % 30 or end % 30:
        raise ValueError("Invalid 30-minute coverage interval.")
    db = connect()
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM callouts WHERE id=?", (callout_id,)).fetchone()
        if row is None or row["shift_type"] != "TUTOR":
            raise ValueError("Tutor callout not found.")
        callout = dict(row)
        if callout["status"] not in ("OPEN", "PARTIAL") or not callout["message_ts"]:
            raise ValueError("This tutor callout is no longer available.")
        if callout["slack_user_id"] == slack_user_id:
            raise ValueError("You cannot cover your own callout.")
        if not (minutes(callout["coverage_start"]) <= start < end <= minutes(callout["coverage_end"])):
            raise ValueError("Hours are outside the callout.")
        if _start_datetime(callout["shift_date"], coverage_start) <= datetime.now(TZ):
            raise ValueError("Cannot claim coverage after its start time.")
        required = {r[0] for r in db.execute(
            "SELECT course FROM tutor_callout_courses WHERE callout_id=?", (callout_id,)
        )}
        if not required:
            raise ValueError("Callout has no required courses.")
        if not set(courses).issubset(required):
            raise ValueError("Selected courses are not part of this callout.")
        if not set(courses).issubset(set(get_tutor_courses(employee))):
            raise ValueError("You are not qualified for all selected courses.")
        check_tutor_claim_availability(
            employee, callout["worksheet"], callout["shift_date"], coverage_start, coverage_end
        )
        if _existing_commitment(db, slack_user_id, callout["shift_date"], coverage_start, coverage_end):
            raise ValueError("You already have a coverage commitment during those hours.")
        placeholders = ",".join("?" for _ in courses)
        existing = db.execute(
            f"""SELECT tc.course FROM tutor_claim_courses tc JOIN claims c ON c.id=tc.claim_id
                WHERE c.callout_id=? AND c.coverage_start < ? AND c.coverage_end > ?
                AND tc.course IN ({placeholders}) LIMIT 1""",
            (callout_id, coverage_end, coverage_start, *courses),
        ).fetchone()
        if existing:
            raise ValueError(f"{existing['course']} is already covered during some or all of those hours.")
        cursor = db.execute(
            """INSERT INTO claims (callout_id,slack_user_id,employee,coverage_start,coverage_end)
               VALUES (?,?,?,?,?)""",
            (callout_id, slack_user_id, employee, coverage_start, coverage_end),
        )
        claim_id = cursor.lastrowid
        db.executemany(
            "INSERT INTO tutor_claim_courses (claim_id,course) VALUES (?,?)",
            [(claim_id, course) for course in courses],
        )
        coverage = tutor_coverage_from_db(db, callout)
        db.execute("UPDATE callouts SET status=? WHERE id=?", (coverage["status"], callout_id))
        record_audit(db, callout_id, slack_user_id, "TUTOR_COVERAGE_CLAIMED", {
            "claim_id": claim_id, "employee": employee, "start": coverage_start,
            "end": coverage_end, "courses": courses, "status_after": coverage["status"],
        })
        db.commit()
        return {"claim_id": claim_id, "status": coverage["status"], "remaining": coverage["remaining"]}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def cancel_callout(callout_id, user_id):
    db = connect()
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM callouts WHERE id=?", (callout_id,)).fetchone()
        if row is None:
            raise ValueError("Callout not found.")
        callout = dict(row)
        if callout["slack_user_id"] != user_id:
            raise ValueError("Only the requester may cancel this callout.")
        if callout["status"] == "CANCELLED":
            raise ValueError("Already cancelled.")
        if _start_datetime(callout["shift_date"], callout["coverage_start"]) <= datetime.now(TZ):
            raise ValueError("Cannot cancel after the coverage begins.")
        if db.execute("SELECT 1 FROM claims WHERE callout_id=? LIMIT 1", (callout_id,)).fetchone():
            raise ValueError("Coverage has been claimed. Claimants must unclaim before cancellation.")
        db.execute("UPDATE callouts SET status='CANCELLED' WHERE id=?", (callout_id,))
        record_audit(db, callout_id, user_id, "CANCELLED", {
            "employee": callout["employee"], "date": callout["shift_date"],
            "desk": callout["desk"], "start": callout["coverage_start"],
            "end": callout["coverage_end"], "status_before": callout["status"],
        })
        db.commit()
        return callout
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def unclaim_coverage(claim_id, user_id):
    db = connect()
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
        if row is None:
            raise ValueError("Claim not found or already released.")
        claim = dict(row)
        if claim["slack_user_id"] != user_id:
            raise ValueError("Only the claimant may release their coverage.")
        callout_row = db.execute("SELECT * FROM callouts WHERE id=?", (claim["callout_id"],)).fetchone()
        if callout_row is None:
            raise ValueError("Callout not found.")
        callout = dict(callout_row)
        if _start_datetime(callout["shift_date"], claim["coverage_start"]) <= datetime.now(TZ):
            raise ValueError("Cannot unclaim after the claimed coverage begins.")
        courses = []
        if callout["shift_type"] == "TUTOR":
            courses = [r[0] for r in db.execute(
                "SELECT course FROM tutor_claim_courses WHERE claim_id=?", (claim_id,)
            )]
            db.execute("DELETE FROM tutor_claim_courses WHERE claim_id=?", (claim_id,))
        db.execute("DELETE FROM claims WHERE id=?", (claim_id,))
        remaining_claims = [dict(r) for r in db.execute(
            "SELECT * FROM claims WHERE callout_id=?", (claim["callout_id"],)
        )]
        if callout["shift_type"] == "TUTOR":
            coverage = tutor_coverage_from_db(db, callout)
            status = coverage["status"]
        else:
            remaining = get_uncovered_intervals(callout, remaining_claims)
            status = "FILLED" if not remaining else ("PARTIAL" if remaining_claims else "OPEN")
        db.execute("UPDATE callouts SET status=? WHERE id=?", (status, claim["callout_id"]))
        record_audit(db, claim["callout_id"], user_id, "COVERAGE_UNCLAIMED", {
            "claim_id": claim_id, "employee": claim["employee"],
            "start": claim["coverage_start"], "end": claim["coverage_end"],
            "courses": courses, "status_after": status,
        })
        db.commit()
        return claim, callout
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def employee_digest(employee, start_date, end_date, now=None):
    now = now or datetime.now(TZ)
    with connection() as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """SELECT * FROM callouts WHERE employee=? AND shift_type='STUDENT_ASSISTANT'
               AND shift_date BETWEEN ? AND ? ORDER BY shift_date,coverage_start,id""",
            (employee, start_date, end_date),
        ).fetchall()
        records = []
        for row in rows:
            callout = dict(row)
            claims = [dict(r) for r in db.execute(
                "SELECT * FROM claims WHERE callout_id=?", (callout["id"],)
            )]
            remaining = get_uncovered_intervals(callout, claims)
            uncovered = sum(minutes(x["end"]) - minutes(x["start"]) for x in remaining)
            if callout["status"] == "CANCELLED":
                classification = "CANCELLED"
            elif not remaining:
                classification = "COVERED"
            elif _start_datetime(callout["shift_date"], callout["coverage_end"]) > now:
                classification = "PENDING"
            else:
                classification = "UNCOVERED"
            records.append({
                **callout, "classification": classification, "uncovered_minutes": uncovered,
                "claims": claims, "remaining": remaining,
            })
    return {
        "employee": employee, "records": records,
        "penalty_count": sum(r["classification"] == "UNCOVERED" for r in records),
        "covered_count": sum(r["classification"] == "COVERED" for r in records),
        "pending_count": sum(r["classification"] == "PENDING" for r in records),
    }


def mentor_digest(slack_user_id, employee, start_date, end_date):
    require_mentor(slack_user_id)
    return employee_digest(employee, start_date, end_date)
