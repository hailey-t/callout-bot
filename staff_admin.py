"""Local administration of Slack-to-schedule names and mentor permissions.

Run: python staff_admin.py
Only trusted administrators with file access should use this tool.
"""

import json
import os
import re
import tempfile
from datetime import datetime
from difflib import get_close_matches
from pathlib import Path

BASE = Path(__file__).resolve().parent
EMPLOYEES = BASE / 'employees.json'
MENTORS = BASE / 'mentors.json'


def norm(value):
    return re.sub(r'\s+', ' ', value.strip()).casefold()


def read_json(path, default):
    if not path.exists():
        return default
    with path.open(encoding='utf-8') as stream:
        return json.load(stream)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(path.name + '.bak')
        backup.write_bytes(path.read_bytes())
    fd, temp = tempfile.mkstemp(prefix='.staff_', suffix='.json', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def select_member(members, prompt='Choose a number (blank cancels): '):
    members = sorted(members.items(), key=lambda pair: (norm(pair[1]), pair[0]))
    if not members:
        print('No staff members registered. Import Slack members first.')
        return None
    for i, (user_id, name) in enumerate(members, 1):
        print(f'{i:>3}. {name}  [{user_id}]')
    while True:
        choice = input(prompt).strip()
        if not choice:
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(members):
            return members[int(choice) - 1][0]
        print('Enter one of the displayed numbers.')


def select_schedule_name(existing):
    try:
        from validate_employees import student_assistant_names, tutor_names
        names = sorted(student_assistant_names() | tutor_names(), key=norm)
    except (ImportError, FileNotFoundError, KeyError, ValueError, OSError) as exc:
        print(f'Unable to load schedule names: {exc}')
        return None
    if not names:
        print('No schedule names found.')
        return None
    suggestions = get_close_matches(norm(existing), [norm(n) for n in names], n=4, cutoff=.5)
    if suggestions:
        print('Likely matches: ' + ', '.join(n for n in names if norm(n) in suggestions))
    for i, name in enumerate(names, 1):
        print(f'{i:>3}. {name}')
    while True:
        value = input('Choose the exact schedule name (blank cancels): ').strip()
        if not value:
            return None
        if value.isdigit() and 1 <= int(value) <= len(names):
            return names[int(value) - 1]
        print('Choose a displayed number.')


def main():
    employees = read_json(EMPLOYEES, {})
    mentor_data = read_json(MENTORS, {'mentor_ids': []})
    if not isinstance(employees, dict):
        raise ValueError('employees.json must contain a Slack ID to name mapping.')
    mentor_ids = set(mentor_data if isinstance(mentor_data, list) else mentor_data.get('mentor_ids', []))
    while True:
        print('\nSHIFT COVERAGE BOT — STAFF ADMINISTRATION')
        print('1. View mentors')
        print('2. Add mentor')
        print('3. Remove mentor')
        print('4. Match Slack user to an Excel schedule name')
        print('5. Run employee validation report')
        print('6. Exit')
        choice = input('Choose an option: ').strip()
        if choice == '1':
            for uid in sorted(mentor_ids, key=lambda uid: norm(employees.get(uid, uid))):
                print(f'- {employees.get(uid, "[Not registered]")} [{uid}]')
            if not mentor_ids:
                print('No mentors configured.')
        elif choice == '2':
            available = {uid: name for uid, name in employees.items() if uid not in mentor_ids}
            uid = select_member(available)
            if uid and input(f'Grant mentor access to {employees[uid]}? [y/N]: ').strip().lower() == 'y':
                mentor_ids.add(uid)
                save_json(MENTORS, {'mentor_ids': sorted(mentor_ids)})
                print('Mentor access granted.')
        elif choice == '3':
            available = {uid: employees.get(uid, '[Not registered]') for uid in mentor_ids}
            uid = select_member(available)
            if uid and input(f'Remove mentor access from {available[uid]}? [y/N]: ').strip().lower() == 'y':
                mentor_ids.remove(uid)
                save_json(MENTORS, {'mentor_ids': sorted(mentor_ids)})
                print('Mentor access removed.')
        elif choice == '4':
            uid = select_member(employees)
            if uid:
                name = select_schedule_name(employees[uid])
                if name:
                    collisions = [other for other, known in employees.items()
                                  if other != uid and norm(known) == norm(name)]
                    if collisions:
                        print('Blocked: another Slack account is already mapped to this schedule name.')
                    else:
                        old = employees[uid]
                        if input(f'Change {old!r} to {name!r}? [y/N]: ').strip().lower() == 'y':
                            employees[uid] = name
                            save_json(EMPLOYEES, dict(sorted(employees.items())))
                            print('Schedule name updated. Existing historical callouts are unchanged.')
        elif choice == '5':
            try:
                from validate_employees import validate
                validate()
            except (ImportError, FileNotFoundError, KeyError, ValueError, OSError) as exc:
                print(f'Validation unavailable: {exc}')
        elif choice == '6':
            return
        else:
            print('Choose 1–6.')


if __name__ == '__main__':
    main()
