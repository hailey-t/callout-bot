
from callout_service import minutes, clock


def normalize_courses(courses):
    result = []

    for course in courses:
        name = " ".join(str(course).upper().split())

        if not name:
            raise ValueError("Course names cannot be empty.")

        if name not in result:
            result.append(name)

    return result


def tutor_coverage_status(
    required_courses,
    start,
    end,
    claims,
):
    required = normalize_courses(required_courses)

    start_min = minutes(start)
    end_min = minutes(end)

    if not required:
        raise ValueError("A tutor callout must specify courses.")

    if (
        start_min >= end_min
        or start_min % 30
        or end_min % 30
    ):
        raise ValueError("Invalid 30-minute coverage interval.")

    covered = set()

    for claim in claims:
        claim_start = minutes(claim["start"])
        claim_end = minutes(claim["end"])

        if (
            claim_start < start_min
            or claim_end > end_min
            or claim_start >= claim_end
            or claim_start % 30
            or claim_end % 30
        ):
            raise ValueError("Claim falls outside the callout.")

        courses = normalize_courses(claim["courses"])

        if not courses or not set(courses).issubset(required):
            raise ValueError("Claim contains invalid courses.")

        for course in courses:
            for t in range(claim_start, claim_end, 30):
                covered.add((course, t))

    missing = []

    for course in required:
        beginning = None

        for t in range(start_min, end_min, 30):
            if (course, t) not in covered:
                if beginning is None:
                    beginning = t
            elif beginning is not None:
                missing.append({
                    "course": course,
                    "start": clock(beginning),
                    "end": clock(t),
                })
                beginning = None

        if beginning is not None:
            missing.append({
                "course": course,
                "start": clock(beginning),
                "end": clock(end_min),
            })

    total_slots = len(required) * (
        (end_min - start_min) // 30
    )

    covered_slots = total_slots - sum(
        (minutes(item["end"]) - minutes(item["start"])) // 30
        for item in missing
    )

    status = (
        "FILLED" if not missing
        else "PARTIAL" if covered_slots else "OPEN"
    )

    return {
        "status": status,
        "required_courses": required,
        "remaining": missing,
        "covered_slots": covered_slots,
        "total_slots": total_slots,
    }
