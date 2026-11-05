
from callout_service import time_label


def format_tutor_remaining(remaining):
    if not remaining:
        return "None"

    return "\n".join(
        f"• {item['course']}: "
        f"{time_label(item['start'])}–{time_label(item['end'])}"
        for item in remaining
    )


def format_tutor_claims(claims):
    if not claims:
        return "None"

    return "\n".join(
        f"• <@{claim['slack_user_id']}>: "
        f"{', '.join(claim['courses'])} "
        f"({time_label(claim['coverage_start'])}–"
        f"{time_label(claim['coverage_end'])})"
        for claim in claims
    )


def format_tutor_message(state):
    callout = state["callout"]

    required = ", ".join(state["courses"])
    claimed = format_tutor_claims(state["claims"])

    remaining = (
        "Not applicable (cancelled)"
        if callout["status"] == "CANCELLED"
        else format_tutor_remaining(state["remaining"])
    )

    return (
        f"*Morning Drop-In Coverage — {callout['status']}*\n"
        f"*Date:* {callout['shift_date']}\n"
        f"*Original tutor:* <@{callout['slack_user_id']}>\n"
        f"*Requested hours:* "
        f"{time_label(callout['coverage_start'])}–"
        f"{time_label(callout['coverage_end'])}\n"
        f"*Required courses:* {required}\n\n"
        f"*Confirmed coverage:*\n{claimed}\n\n"
        f"*Remaining uncovered:*\n{remaining}\n\n"
        f"*Status:* {callout['status']}"
    )
