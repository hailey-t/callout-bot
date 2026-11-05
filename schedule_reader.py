import re
from datetime import date, datetime, timedelta
from pathlib import Path
from openpyxl import load_workbook

BASE = Path(__file__).resolve().parent
SCHEDULE_FILE = BASE / 'schedule.xlsx'
DESKS = {2: 'Tutoring', 3: 'Tutoring', 4: '272: CS Help Desk', 5: '278: Chem/Orgo Drop-in', 6: '280: Math Lab'}
DAYS = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 'Friday': 4}
MONTHS = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6, 'Jul': 7, 'Aug': 8, 'Sep': 9, 'Sept': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}
WEEK_PATTERN = re.compile(r'^([A-Za-z]+)\s+(\d+)\s*-\s*([A-Za-z]+)\s+(\d+)$')
INTERVAL_PATTERN = re.compile(r'^(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*(AM|PM)$', re.I)
SCHEDULE_YEAR = 2026


def week_dates(name):
    match = WEEK_PATTERN.match(name.strip())
    if not match:
        raise ValueError(f'Not a weekly sheet: {name}')
    m1, d1, m2, d2 = match.groups()
    start = date(SCHEDULE_YEAR, MONTHS[m1], int(d1))
    end = date(SCHEDULE_YEAR, MONTHS[m2], int(d2))
    if end < start:
        end = date(SCHEDULE_YEAR + 1, MONTHS[m2], int(d2))
    return start, end


def get_upcoming_worksheets(today=None):
    today = today or date.today()
    wb = load_workbook(SCHEDULE_FILE, read_only=True, data_only=True)
    try:
        weeks = []
        for name in wb.sheetnames:
            if name == 'Template':
                continue
            try:
                start, end = week_dates(name)
            except (ValueError, KeyError):
                continue
            if end >= today:
                weeks.append({'name': name, 'start': start.isoformat(), 'end': end.isoformat()})
        return sorted(weeks, key=lambda w: w['start'])
    finally:
        wb.close()


def _clock(value, period):
    return datetime.strptime(f'{value} {period}', '%I:%M %p')


def parse_interval(label):
    if not isinstance(label, str):
        return None
    match = INTERVAL_PATTERN.match(label.strip())
    if not match:
        return None
    a, b, period = match.groups()
    end = _clock(b, period)
    start = _clock(a, period)
    if start > end:
        start -= timedelta(hours=12)
    if (end - start) != timedelta(minutes=30):
        return None
    return start.strftime('%H:%M'), end.strftime('%H:%M')


def get_employee_shifts(sheet_name, employee_name):
    wb = load_workbook(SCHEDULE_FILE, read_only=True, data_only=True)
    try:
        sheet = wb[sheet_name]
        shifts = []
        current_day = None
        monday = None if sheet_name == 'Template' else week_dates(sheet_name)[0]
        for row in sheet.iter_rows():
            first = row[0].value
            if isinstance(first, str):
                header = first.strip()
                if header in DAYS:
                    current_day = header
                    continue
                if header.endswith('s') and header[:-1] in DAYS:
                    current_day = header[:-1]
                    continue
            if not current_day:
                continue
            interval = parse_interval(first)
            if interval is None:
                continue
            start, end = interval
            for column, desk in DESKS.items():
                if len(row) < column:
                    continue
                assigned = row[column - 1].value
                if not isinstance(assigned, str) or assigned.strip().casefold() != employee_name.strip().casefold():
                    continue
                cell = {'row': row[0].row, 'column': column, 'start': start, 'end': end}
                previous = shifts[-1] if shifts else None
                if previous and previous['day'] == current_day and previous['desk'] == desk and previous['end'] == start:
                    previous['end'] = end
                    previous['cells'].append(cell)
                else:
                    shifts.append({'day': current_day, 'desk': desk, 'start': start, 'end': end, 'cells': [cell]})
        for shift in shifts:
            if monday is not None:
                shift['date'] = (monday + timedelta(days=DAYS[shift['day']])).isoformat()
        return {'employee': employee_name, 'worksheet': sheet_name, 'shifts': shifts}
    finally:
        wb.close()


if __name__ == '__main__':
    print('Upcoming weeks:')
    for week in get_upcoming_worksheets():
        print(week['name'])
    print('\nSample shifts:')
    for shift in get_employee_shifts('Oct 19 - Oct 23', 'Hailey Tayag')['shifts']:
        print(shift['day'], shift['desk'], shift['start'], shift['end'], len(shift['cells']))
