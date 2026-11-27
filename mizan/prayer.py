"""Friday afternoon schedule, plus the legacy verified IACC prayer lookup."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from .core import RuleError, parse_local

TIMETABLE = json.loads(Path(__file__).with_name('iacc_2026.json').read_text())
SOURCE = TIMETABLE['source']
LOCATION = 'IACC Plano'


def fajr_slot(day, offset=15):
    iqamah = TIMETABLE['iqamah'].get(day.isoformat())
    if not iqamah:
        raise RuleError('IACC has no verified timetable loaded for this date. Choose a custom time; the TA must verify it. No previous-year times will be reused.')
    return parse_local(f'{day.isoformat()} {iqamah}', 'America/Chicago') + timedelta(minutes=offset)


def regular_slot(day, hour=16):
    return parse_local(f'{day.isoformat()} {hour:02d}:00', 'America/Chicago')


def upcoming(store, count=4):
    local = store.clock().astimezone(ZoneInfo('America/Chicago'))
    day = local.date() + timedelta(days=(4-local.weekday()) % 7)
    result = []
    for _ in range(count + 1):
        dt = regular_slot(day, store.get('class_hour', 16))
        if dt > store.clock() + timedelta(minutes=5):
            result.append(dt)
        if len(result) == count:
            break
        day += timedelta(days=7)
    return result


def defaults(store, start=None):
    dt = start or upcoming(store, 1)[0]
    previous=store.latest_lesson(dt)
    return dict(start=dt, duration=60, location=LOCATION,
                topic=previous['next_lesson'] if previous else store.get('class_topic', 'Arabic class · continue our current lesson'),
                prep=previous['prep'] if previous and previous['prep'] else store.get('class_prep', 'Bring the textbook, exercise book, and vocabulary book.'))


def ensure_weekly(store):
    """One regular invitation per Friday, even after a move/cancel/restart."""
    if store.get('automation_paused',False) or not store.get('weekly_invites', True) or len(store.roster()) != 4:
        return None
    active=store.active_class()
    if active:
        return active['id']  # A future manual date or unfinished class blocks all new weekly invites.
    dt = upcoming(store, 1)[0]
    day = dt.astimezone(ZoneInfo('America/Chicago')).date()
    if store.get(f'weekly_skip:{day.isoformat()}',False):
        return None
    check_in = datetime.combine(day-timedelta(days=2), datetime.min.time(), ZoneInfo('America/Chicago'))+timedelta(hours=18)
    if store.clock()<check_in:
        return None
    key = f'weekly:{day.isoformat()}'
    existing = store.get(key) or store.get(f'weekly:{(day+timedelta(days=1)).isoformat()}')
    if existing:
        store.set(key, existing)
        return existing  # Caller can repair a missing announcement after a restart.
    week_start = datetime.combine(day - timedelta(days=4), datetime.min.time(), ZoneInfo('America/Chicago'))
    week_end = week_start + timedelta(days=7)
    from .core import stamp
    existing = store.one('SELECT id FROM sessions WHERE start_at>=? AND start_at<? ORDER BY id LIMIT 1', (stamp(week_start),stamp(week_end)))
    if existing:
        store.set(key, existing['id'])
        return existing['id']
    spec = defaults(store, dt)
    return store.create_class(actor=0, weekly_key=key, **spec)
