import json
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from coverage_db import initialize_database
from schedule_reader import get_employee_shifts, get_upcoming_worksheets
from callout_service import (
    create_callout, create_tutor_callout, cancel_unposted_callout,
    save_slack_message, claim_callout, claim_tutor_coverage,
    get_callout_state, get_tutor_callout_state, minutes, time_label,
    cancel_callout, unclaim_coverage, mentor_digest,
)
from coverage_display import format_tutor_message
from tutor_schedule_reader import get_tutor_week_shifts, get_tutor_courses
from slack_sync_service import synchronize_pending_messages
from reminder_service import send_due_reminders


BASE = Path(__file__).resolve().parent
load_dotenv(BASE / '.env')
logging.basicConfig(level=logging.INFO)
app = App(token=os.environ['SLACK_BOT_TOKEN'])
CALL_CHANNEL = os.environ['SLACK_CALLOUT_CHANNEL_ID']


def employees():
    with open(BASE / 'employees.json', encoding='utf-8') as f:
        return json.load(f)


def plain(text):
    return {'type': 'plain_text', 'text': text}


def option(label, value):
    return {'text': plain(label[:75]), 'value': str(value)}


def input_select(block_id, label, choices, initial=None):
    element = {'type': 'static_select', 'action_id': 'selected', 'options': choices}
    if initial is not None:
        element['initial_option'] = initial
    return {'type': 'input', 'block_id': block_id, 'label': plain(label), 'element': element}


def modal(callback, title, blocks, metadata, submit='Continue'):
    return {'type': 'modal', 'callback_id': callback, 'title': plain(title),
            'submit': plain(submit), 'close': plain('Cancel'),
            'private_metadata': json.dumps(metadata), 'blocks': blocks}


def clock_options(start, end, include_start=True, include_end=True):
    a, b = minutes(start), minutes(end)
    if not include_start:
        a += 30
    if not include_end:
        b -= 30
    return [option(time_label(f'{m//60:02d}:{m%60:02d}'), f'{m//60:02d}:{m%60:02d}') for m in range(a, b+1, 30)]


def user_notice(client, channel, user, text):
    client.chat_postEphemeral(channel=channel, user=user, text=text)


def combined_shifts(worksheet, employee):
    assistant = get_employee_shifts(worksheet, employee)["shifts"]
    tutor = get_tutor_week_shifts(employee, worksheet)
    return [
        {**shift, "role": "STUDENT_ASSISTANT"} for shift in assistant
    ] + tutor


@app.command('/callout')
def command_callout(ack, body, client):
    ack()
    uid = body['user_id']
    employee = employees().get(uid)
    if not employee:
        user_notice(client, body['channel_id'], uid, 'Your Slack ID is not registered in employees.json.')
        return
    weeks = get_upcoming_worksheets()
    if not weeks:
        user_notice(client, body['channel_id'], uid, 'No upcoming weeks are available in the local schedule.')
        return
    view = modal('select_callout_week', 'Request Coverage', [
        {'type': 'section', 'text': {'type': 'mrkdwn', 'text': f'*Employee:* {employee}'}},
        input_select('week', 'Schedule week', [option(w['name'].strip(), w['name']) for w in weeks])
    ], {'employee': employee})
    client.views_open(trigger_id=body['trigger_id'], view=view)


@app.view('select_callout_week')
def week_selected(ack, body, view):
    metadata = json.loads(view['private_metadata'])
    employee = employees().get(body['user']['id'])
    if employee != metadata['employee']:
        ack(response_action='errors', errors={'week': 'User identity mismatch.'})
        return
    sheet = view['state']['values']['week']['selected']['selected_option']['value']
    if sheet not in {w['name'] for w in get_upcoming_worksheets()}:
        ack(response_action='errors', errors={'week': 'Schedule week is unavailable.'})
        return
    shifts = combined_shifts(sheet, employee)
    if not shifts:
        ack(response_action='errors', errors={'week': 'No assigned shifts found for this week.'})
        return
    choices = [
        option(
            f"{shift['day']} | {'Tutor' if shift['role'] == 'TUTOR' else 'Student Assistant'} | "
            f"{shift['desk']} | {shift['start']}–{shift['end']}", index
        )
        for index, shift in enumerate(shifts)
    ]
    new_view = modal('select_callout_shift', 'Select Shift', [
        {'type': 'section', 'text': {'type': 'mrkdwn', 'text': f'*Employee:* {employee}\n*Week:* {sheet.strip()}'}},
        input_select('shift', 'Assigned shift', choices)
    ], {'employee': employee, 'worksheet': sheet})
    ack(response_action='push', view=new_view)


@app.view('select_callout_shift')
def shift_selected(ack, body, view):
    metadata = json.loads(view['private_metadata'])
    if employees().get(body['user']['id']) != metadata['employee']:
        ack(response_action='errors', errors={'shift': 'Employee identity mismatch.'})
        return
    shifts = combined_shifts(metadata['worksheet'], metadata['employee'])
    index = int(view['state']['values']['shift']['selected']['selected_option']['value'])
    if not 0 <= index < len(shifts):
        ack(response_action='errors', errors={'shift': 'Invalid shift.'})
        return
    shift = shifts[index]
    if shift["role"] == "TUTOR":
        assistant_count = len(get_employee_shifts(
            metadata["worksheet"], metadata["employee"]
        )["shifts"])
        metadata["shift_type"] = "TUTOR"
        metadata["shift_index"] = index - assistant_count
    else:
        metadata["shift_type"] = "STUDENT_ASSISTANT"
        metadata["shift_index"] = index
    starts = clock_options(shift['start'], shift['end'], include_end=False)
    ends = clock_options(shift['start'], shift['end'], include_start=False)
    new_view = modal('submit_callout_hours', 'Tutor Callout' if shift['role'] == 'TUTOR' else 'Coverage Hours', [
        {'type': 'section', 'text': {'type': 'mrkdwn', 'text': (
            f"*{shift['day']} — {shift['desk']}*\n"
            f"Assigned {time_label(shift['start'])}–{time_label(shift['end'])}"
            + (f"\n*Courses:* {', '.join(shift['courses'])}" if shift['role'] == 'TUTOR' else "")
        )}},
        input_select('coverage_start', 'Coverage start', starts, starts[0]),
        input_select('coverage_end', 'Coverage end', ends, ends[-1]),
    ], metadata, submit='Submit Callout')
    ack(response_action='push', view=new_view)


def refresh_callout_message(client, callout_id):
    state = get_callout_state(callout_id)
    callout = state["callout"]
    if callout["shift_type"] == "TUTOR":
        state = get_tutor_callout_state(callout_id)
        callout = state["callout"]
        message = format_tutor_message(state)
    else:
        claimed = "\n".join(
            f"• <@{claim['slack_user_id']}>: "
            f"{time_label(claim['coverage_start'])}–{time_label(claim['coverage_end'])}"
            for claim in state["claims"]
        ) or "None"
        remaining = (
            "Not applicable (cancelled)" if callout["status"] == "CANCELLED"
            else "\n".join(
                f"• {time_label(slot['start'])}–{time_label(slot['end'])}"
                for slot in state["remaining"]
            ) or "None"
        )
        message = (
            f"*Coverage {callout['status']} — {callout['desk']}*\n"
            f"*Date:* {callout['shift_date']}\n"
            f"*Original employee:* <@{callout['slack_user_id']}>\n"
            f"*Original shift:* {time_label(callout['shift_start'])}–{time_label(callout['shift_end'])}\n"
            f"*Requested coverage:* {time_label(callout['coverage_start'])}–{time_label(callout['coverage_end'])}\n\n"
            f"*Confirmed coverage:*\n{claimed}\n\n"
            f"*Remaining uncovered:*\n{remaining}\n\n*Status:* {callout['status']}"
        )
    if not callout["channel_id"] or not callout["message_ts"]:
        raise ValueError("Missing Slack message reference.")
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": message}}]
    actions = []
    if callout["status"] in ("OPEN", "PARTIAL"):
        actions.append({
            "type": "button",
            "action_id": "claim_tutor_coverage" if callout["shift_type"] == "TUTOR" else "claim_coverage",
            "text": plain("Claim Tutor Coverage" if callout["shift_type"] == "TUTOR" else "Claim Coverage"),
            "value": str(callout_id),
        })
    if callout["status"] != "CANCELLED":
        actions.append({
            "type": "button", "action_id": "cancel_callout",
            "text": plain("Cancel Callout"), "value": str(callout_id), "style": "danger",
        })
    for claim in state["claims"]:
        actions.append({
            "type": "button", "action_id": "unclaim_coverage",
            "text": plain(f"Release {claim['employee'][:28]} {claim['coverage_start']}"),
            "value": str(claim["id"]),
        })
    if actions:
        blocks.append({"type": "actions", "elements": actions[:25]})
    blocks.append({
        "type": "context", "elements": [{"type": "mrkdwn", "text": f"Callout #{callout_id}"}],
    })
    client.chat_update(
        channel=callout["channel_id"], ts=callout["message_ts"], text=message, blocks=blocks,
    )


@app.view('submit_callout_hours')
def submit_callout(ack, body, view, client):
    metadata = json.loads(view['private_metadata'])
    uid = body['user']['id']
    if employees().get(uid) != metadata['employee']:
        ack(response_action='errors', errors={'coverage_start': 'Employee verification failed.'})
        return
    values = view['state']['values']
    start = values['coverage_start']['selected']['selected_option']['value']
    end = values['coverage_end']['selected']['selected_option']['value']
    if start >= end:
        ack(response_action='errors', errors={'coverage_end': 'End time must follow start time.'})
        return
    # Acknowledge quickly; subsequent errors are reported privately and logged.
    ack(response_action='update', view={'type': 'modal', 'title': plain('Submitting'), 'close': plain('Done'),
        'blocks': [{'type': 'section', 'text': {'type': 'mrkdwn', 'text': 'Processing request. Check the test channel or a private error message.'}}]})
    callout = None
    posted = None
    try:
        if metadata.get("shift_type") == "TUTOR":
            callout = create_tutor_callout(
                uid, metadata["employee"], metadata["worksheet"],
                int(metadata["shift_index"]), start, end,
            )
            message = format_tutor_message(get_tutor_callout_state(callout["id"]))
            claim_action = "claim_tutor_coverage"
        else:
            callout = create_callout(
                uid, metadata["employee"], metadata["worksheet"],
                int(metadata["shift_index"]), start, end,
            )
            message = (
                f"*Coverage Needed — {callout['desk']}*\n"
                f"*Date:* {callout['shift_date']}\n"
                f"*Employee:* <@{uid}>\n"
                f"*Original shift:* {time_label(callout['shift_start'])}–{time_label(callout['shift_end'])}\n"
                f"*Coverage needed:* {time_label(start)}–{time_label(end)}\n*Status:* OPEN"
            )
            claim_action = "claim_coverage"
        posted = client.chat_postMessage(
            channel=CALL_CHANNEL, text=message, blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": message}},
                {"type": "actions", "elements": [
                    {"type": "button", "action_id": claim_action,
                     "text": plain("Claim Coverage"), "value": str(callout["id"])},
                    {"type": "button", "action_id": "cancel_callout",
                     "text": plain("Cancel Callout"), "value": str(callout["id"]), "style": "danger"},
                ]},
                {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Callout #{callout['id']}"}]},
            ],
        )
        save_slack_message(callout['id'], posted['channel'], posted['ts'])
        print(f"Callout #{callout['id']} posted: {metadata['employee']}, {callout['shift_date']}, {start}–{end}")
    except Exception as error:
        logging.exception('Callout submission failed')
        if callout is not None and posted is None:
            cancel_unposted_callout(callout['id'])
        try:
            client.chat_postMessage(channel=uid, text=f'Callout request failed: {str(error)[:250]}. Check the test channel before retrying.')
        except Exception:
            logging.exception('Unable to notify requester')


@app.action('claim_coverage')
def open_claim(ack, body, client):
    ack()
    uid = body['user']['id']
    employee = employees().get(uid)
    channel = body['channel']['id']
    if not employee:
        user_notice(client, channel, uid, 'Your Slack ID is not registered.')
        return
    callout_id = int(body['actions'][0]['value'])
    state = get_callout_state(callout_id)
    callout = state['callout']
    if callout['shift_type'] != 'STUDENT_ASSISTANT':
        user_notice(client, channel, uid, 'Use Claim Tutor Coverage for this request.')
        return
    if uid == callout['slack_user_id'] or callout['status'] not in ('OPEN', 'PARTIAL') or not state['remaining']:
        user_notice(client, channel, uid, 'You cannot claim this callout.')
        return
    starts = []
    ends = []
    for slot in state['remaining']:
        starts.extend(clock_options(slot['start'], slot['end'], include_end=False))
        ends.extend(clock_options(slot['start'], slot['end'], include_start=False))
    default_end = next(x for x in ends if x['value'] == state['remaining'][0]['end'])
    view = modal('submit_coverage_claim', 'Claim Coverage', [
        {'type': 'section', 'text': {'type': 'mrkdwn', 'text': f"*{callout['desk']}* — {callout['shift_date']}\nOriginal employee: <@{callout['slack_user_id']}>\nRequested: {time_label(callout['coverage_start'])}–{time_label(callout['coverage_end'])}"}},
        input_select('claim_start', 'Coverage start', starts, starts[0]),
        input_select('claim_end', 'Coverage end', ends, default_end)
    ], {'callout_id': callout_id}, submit='Claim')
    client.views_open(trigger_id=body['trigger_id'], view=view)


@app.view('submit_coverage_claim')
def submit_claim(ack, body, view, client):
    callout_id = int(json.loads(view['private_metadata'])['callout_id'])
    uid = body['user']['id']
    employee = employees().get(uid)
    if not employee:
        ack(response_action='errors', errors={'claim_start': 'You are not registered.'})
        return
    values = view['state']['values']
    start = values['claim_start']['selected']['selected_option']['value']
    end = values['claim_end']['selected']['selected_option']['value']
    if start >= end:
        ack(response_action='errors', errors={'claim_end': 'End must follow start.'})
        return
    try:
        result = claim_callout(callout_id, uid, employee, start, end)
    except ValueError as error:
        ack(response_action='errors', errors={'claim_start': str(error)[:150]})
        return
    except Exception:
        logging.exception('Claim failed')
        ack(response_action='errors', errors={'claim_start': 'Claim could not be saved. Try again.'})
        return
    ack(response_action='update', view={'type': 'modal', 'title': plain('Claim Confirmed'), 'close': plain('Done'),
        'blocks': [{'type': 'section', 'text': {'type': 'mrkdwn', 'text': f"*Claim saved!*\n{time_label(start)}–{time_label(end)}\nCallout #{callout_id}: {result['status']}"}}]})
    try:
        state = get_callout_state(callout_id)
        callout = state['callout']
        client.chat_postMessage(channel=callout['channel_id'], thread_ts=callout['message_ts'],
                                text=f'✅ <@{uid}> claimed {time_label(start)}–{time_label(end)}.')
    except Exception:
        logging.exception('Claim saved, but Slack message update failed; manual reconciliation needed')
    print(f'Callout #{callout_id} claimed by {employee}: {start}–{end} ({result["status"]})')



@app.action("claim_tutor_coverage")
def open_tutor_claim(ack, body, client):
    ack()
    uid = body["user"]["id"]
    employee = employees().get(uid)
    channel = body["channel"]["id"]
    if not employee:
        user_notice(client, channel, uid, "Your Slack account is not registered.")
        return
    callout_id = int(body["actions"][0]["value"])
    state = get_tutor_callout_state(callout_id)
    callout = state["callout"]
    if (
        uid == callout["slack_user_id"]
        or callout["status"] not in ("OPEN", "PARTIAL")
        or not state["remaining"]
    ):
        user_notice(client, channel, uid, "You cannot claim this tutor callout.")
        return
    qualified = set(get_tutor_courses(employee))
    available = sorted(qualified & {r["course"] for r in state["remaining"]})
    if not available:
        user_notice(client, channel, uid, "No uncovered courses match your qualifications.")
        return
    starts = clock_options(callout["coverage_start"], callout["coverage_end"], include_end=False)
    ends = clock_options(callout["coverage_start"], callout["coverage_end"], include_start=False)
    course_select = {
        "type": "input", "block_id": "tutor_courses", "label": plain("Courses you can cover"),
        "element": {
            "type": "multi_static_select", "action_id": "selected",
            "options": [option(c, c) for c in available],
        },
    }
    view = modal(
        "submit_tutor_claim", "Claim Tutor Coverage", [
            {"type": "section", "text": {"type": "mrkdwn", "text": (
                f"*Morning Drop In — {callout['shift_date']}*\n"
                f"*Original tutor:* <@{callout['slack_user_id']}>\n"
                "Select the hours and courses you can cover."
            )}},
            input_select("claim_start", "Coverage start", starts, starts[0]),
            input_select("claim_end", "Coverage end", ends, ends[-1]),
            course_select,
        ], {"callout_id": callout_id}, submit="Claim",
    )
    client.views_open(trigger_id=body["trigger_id"], view=view)


@app.view("submit_tutor_claim")
def submit_tutor_claim(ack, body, view, client):
    uid = body["user"]["id"]
    employee = employees().get(uid)
    callout_id = int(json.loads(view["private_metadata"])["callout_id"])
    if not employee:
        ack(response_action="errors", errors={"tutor_courses": "Employee not registered."})
        return
    values = view["state"]["values"]
    start = values["claim_start"]["selected"]["selected_option"]["value"]
    end = values["claim_end"]["selected"]["selected_option"]["value"]
    courses = [
        item["value"] for item in values["tutor_courses"]["selected"].get("selected_options", [])
    ]
    if start >= end:
        ack(response_action="errors", errors={"claim_end": "End must follow start."})
        return
    if not courses:
        ack(response_action="errors", errors={"tutor_courses": "Select at least one course."})
        return
    try:
        result = claim_tutor_coverage(callout_id, uid, employee, start, end, courses)
    except (ValueError, PermissionError) as error:
        ack(response_action="errors", errors={"tutor_courses": str(error)[:150]})
        return
    except Exception:
        logging.exception("Tutor claim failed")
        ack(response_action="errors", errors={"tutor_courses": "Unable to save tutor claim."})
        return
    ack(response_action="update", view={
        "type": "modal", "title": plain("Claim Confirmed"), "close": plain("Done"),
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": (
            "*Tutor coverage saved!*\n"
            f"*Hours:* {time_label(start)}–{time_label(end)}\n"
            f"*Courses:* {', '.join(courses)}\n*Status:* {result['status']}"
        )}}],
    })
    try:
        callout = get_tutor_callout_state(callout_id)["callout"]
        client.chat_postMessage(
            channel=callout["channel_id"], thread_ts=callout["message_ts"],
            text=(
                f"✅ <@{uid}> claimed Morning Drop-In coverage "
                f"from {time_label(start)} to {time_label(end)} for {', '.join(courses)}."
            ),
        )
    except Exception:
        logging.exception("Tutor claim saved, but thread notification failed")


@app.action('cancel_callout')
def on_cancel(ack, body, client):
    ack()
    callout_id = int(body['actions'][0]['value'])
    uid = body['user']['id']
    try:
        state = get_callout_state(callout_id)
        if state['callout']['slack_user_id'] != uid:
            raise ValueError('Only the person who requested the callout may cancel it.')
        client.views_open(trigger_id=body['trigger_id'], view=modal(
            'confirm_cancel_callout', 'Cancel Callout',
            [{'type': 'section', 'text': {'type': 'mrkdwn', 'text':
                f"Cancel callout #{callout_id}? This will remove the request from active coverage and record the cancellation."}}],
            {'callout_id': callout_id}, submit='Cancel Callout'))
    except ValueError as error:
        user_notice(client, body['channel']['id'], uid, str(error))


@app.view('confirm_cancel_callout')
def confirm_cancel(ack, body, view, client):
    uid = body['user']['id']
    callout_id = int(json.loads(view['private_metadata'])['callout_id'])
    try:
        callout = cancel_callout(callout_id, uid)
    except ValueError as error:
        ack()
        client.chat_postMessage(channel=uid, text=f'Could not cancel callout: {error}')
        return
    ack()
    try:
        client.chat_postMessage(channel=callout['channel_id'], thread_ts=callout['message_ts'],
                                text=f'🚫 <@{uid}> cancelled callout #{callout_id}.')
    except Exception:
        logging.exception('Cancellation saved; Slack sync needs retry')


@app.action('unclaim_coverage')
def on_unclaim(ack, body, client):
    ack()
    claim_id = int(body['actions'][0]['value'])
    uid = body['user']['id']
    client.views_open(trigger_id=body['trigger_id'], view=modal(
        'confirm_unclaim_coverage', 'Release Coverage',
        [{'type': 'section', 'text': {'type': 'mrkdwn',
            'text': f'Release coverage claim #{claim_id}? The hours will become available again.'}}],
        {'claim_id': claim_id}, submit='Release'))


@app.view('confirm_unclaim_coverage')
def confirm_unclaim(ack, body, view, client):
    uid = body['user']['id']
    claim_id = int(json.loads(view['private_metadata'])['claim_id'])
    try:
        claim, callout = unclaim_coverage(claim_id, uid)
    except ValueError as error:
        ack()
        client.chat_postMessage(channel=uid, text=f'Could not release coverage: {error}')
        return
    ack()
    try:
        client.chat_postMessage(channel=callout['channel_id'], thread_ts=callout['message_ts'],
                                text=f"↩️ <@{uid}> released {time_label(claim['coverage_start'])}–{time_label(claim['coverage_end'])}. The hours need coverage again.")
    except Exception:
        logging.exception('Release saved; Slack sync needs retry')


def mentor_ids():
    path = BASE / 'mentors.json'
    if not path.exists():
        return set()
    with open(path, encoding='utf-8') as f:
        value = json.load(f)
    return set(value if isinstance(value, list) else value.get('mentor_ids', []))


@app.command('/callout-digest')
def callout_digest_command(ack, body, client):
    ack()
    uid = body['user_id']
    if uid not in mentor_ids():
        user_notice(client, body['channel_id'], uid, 'You are not authorized to view employee callout digests.')
        return
    names = sorted(set(employees().values()))
    if not names:
        user_notice(client, body['channel_id'], uid, 'No employees are registered.')
        return
    view = modal('show_callout_digest', 'Callout Digest', [
        input_select('employee', 'Employee', [option(n, n) for n in names]),
        input_select('period', 'Reporting period', [
            option('Fall 2026 semester', 'semester'),
            option('October 2026', 'october'),
            option('November 2026', 'november'),
            option('December 2026', 'december')])
    ], {}, submit='View Digest')
    client.views_open(trigger_id=body['trigger_id'], view=view)


@app.view('show_callout_digest')
def show_callout_digest(ack, body, view):
    if body['user']['id'] not in mentor_ids():
        ack(response_action='errors', errors={'employee': 'Not authorized.'})
        return
    state = view['state']['values']
    name = state['employee']['selected']['selected_option']['value']
    period = state['period']['selected']['selected_option']['value']
    periods = {'semester': ('2026-08-01', '2026-12-31'),
               'october': ('2026-10-01', '2026-10-31'),
               'november': ('2026-11-01', '2026-11-30'),
               'december': ('2026-12-01', '2026-12-31')}
    if name not in employees().values():
        ack(response_action='errors', errors={'employee': 'Unknown employee.'})
        return
    start, end = periods[period]
    try:
        digest = mentor_digest(body['user']['id'], name, start, end)
    except PermissionError:
        ack(response_action='errors', errors={'employee': 'Not authorized.'})
        return
    lines = []
    for row in digest['records']:
        lines.append(f"• #{row['id']} | {row['shift_date']} | {row['desk']} | "
                     f"{time_label(row['coverage_start'])}–{time_label(row['coverage_end'])} | "
                     f"*{row['classification']}* | Uncovered: {row['uncovered_minutes']/60:g}h")
    body_text = (f"*{name} — {start} to {end}*\n"
                 f"*Total:* {len(digest['records'])} | *Fully covered:* {digest['covered_count']} | "
                 f"*Pending:* {digest['pending_count']}\n"
                 f"*Penalty-eligible:* {digest['penalty_count']} / 2\n"
                 f"*Threshold:* {'REACHED — mentor review' if digest['penalty_count'] >= 2 else 'Not reached'}\n\n"
                 + ('\n'.join(lines) if lines else 'No callouts in this period.'))
    ack(response_action='update', view={'type': 'modal', 'title': plain('Private Digest'),
        'close': plain('Done'), 'blocks': [{'type': 'section', 'text': {'type': 'mrkdwn',
        'text': body_text[:2900]}}]})


def slack_sync_worker(client):
    while True:
        try:
            synchronize_pending_messages(client, refresh_callout_message)
        except Exception:
            logging.exception("Slack sync worker failed")
        time.sleep(15)


def reminder_worker(client):
    while True:
        try:
            send_due_reminders(client)
        except Exception:
            logging.exception("Reminder worker failed")
        time.sleep(60)


if __name__ == '__main__':
    initialize_database()
    threading.Thread(target=slack_sync_worker, args=(app.client,), daemon=True).start()
    threading.Thread(target=reminder_worker, args=(app.client,), daemon=True).start()
    print('Starting Shift Coverage Bot...')
    SocketModeHandler(app, os.environ['SLACK_APP_TOKEN']).start()
