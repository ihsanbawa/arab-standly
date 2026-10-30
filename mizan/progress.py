"""Derived attendance progress: corrections update streaks and monthly risk automatically."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import calendar
import json
from .core import month_of, month_bounds, stamp


def complete(store, sid):
    return bool(store.one("SELECT 1 FROM sessions s WHERE s.id=? AND s.status='held' AND (SELECT COUNT(*) FROM session_members m WHERE m.session_id=s.id)=4 AND (SELECT COUNT(*) FROM attendance a JOIN session_members m ON m.session_id=a.session_id AND m.user_id=a.user_id WHERE a.session_id=s.id AND a.status='present')=4", (sid,)))


def monthly_count(store, month):
    a,b=month_bounds(month,store.tz)
    return sum(complete(store,s['id']) for s in store.rows("SELECT id FROM sessions WHERE status='held' AND start_at>=? AND start_at<?",(a,b)))


def locked(store,sid):
    s=store.session(sid)
    if s['status']!='scheduled' or len(store.participants(sid))!=4: return False
    return len([r for r in store.responses(sid) if r['response'] in {'coming','late'}])==4


def personal(store,uid):
    events=[]
    absence_sessions=set()
    for cause in store.rows("SELECT session_id FROM penalty_causes WHERE user_id=? AND cause='absence'",(uid,)):
        sid=cause['session_id']
        records=store.rows("SELECT created_at,details FROM audit WHERE action='attendance_corrected' ORDER BY id")
        recorded=store.session(sid)['confirmation_due']
        for record in records:
            details=json.loads(record['details'])
            if details.get('session')==sid and details.get('user')==uid and details.get('status')=='absent':
                recorded=record['created_at'];break
        if recorded<=stamp(store.clock()):
            events.append((recorded,0));absence_sessions.add(sid)
    for row in store.rows("SELECT s.id,s.start_at,s.status class_status,a.status FROM attendance a JOIN sessions s ON s.id=a.session_id WHERE a.user_id=? AND s.confirmation_due<=? ORDER BY s.start_at,s.id",(uid,stamp(store.clock()))):
        if row['status']=='present' and row['class_status']=='held': events.append((row['start_at'],1))
        elif row['status']=='absent' and row['id'] not in absence_sessions: events.append((row['start_at'],0))
    current=best=0
    for _,attended in sorted(events):
        current=current+1 if attended else 0
        best=max(best,current)
    return {'current':current,'best':best}


def previous_month(month):
    return (datetime.strptime(month,'%Y-%m').replace(day=1)-timedelta(days=1)).strftime('%Y-%m')


def group_streak(store):
    now=month_of(store.clock(),store.tz)
    end=now if monthly_count(store,now)>=2 else previous_month(now)
    first=store.one('SELECT MIN(start_at) start FROM sessions')
    if not first or not first['start']: return {'current':0,'best':0}
    start=month_of(datetime.fromisoformat(first['start']),store.tz)
    months=[]; m=end
    while m>=start:
        months.append(m); m=previous_month(m)
    current=best=0
    for m in reversed(months):
        current=current+1 if monthly_count(store,m)>=2 else 0
        best=max(best,current)
    return {'current':current,'best':best}


def danger(store,month=None):
    local=store.clock().astimezone(ZoneInfo(store.tz)); month=month or month_of(local,store.tz)
    held=monthly_count(store,month); need=max(0,2-held)
    a,b=month_bounds(month,store.tz)
    sessions=store.rows("SELECT * FROM sessions WHERE status='scheduled' AND start_at>=? AND start_at<? ORDER BY confirmation_due",(a,b))
    confirmed=0; previous_end=None; actions=[]
    for s in sessions:
        if datetime.fromisoformat(s['start_at'])<=store.clock():
            actions.append(f"Confirm attendance and recap for class #{s['id']}")
            continue
        if locked(store,s['id']):
            # Four people cannot attend two overlapping classes.
            if previous_end is None or s['start_at']>=previous_end:
                confirmed+=1;previous_end=s['confirmation_due']
        else:
            replies={r['user_id']:r['response'] for r in store.responses(s['id'])}
            missing=[m['name'] for m in store.participants(s['id']) if m['user_id'] not in replies]
            declines=[m['name'] for m in store.participants(s['id']) if replies.get(m['user_id']) in {'unavailable','change'}]
            if missing: actions.append('RSVP needed: '+', '.join(missing))
            if declines: actions.append('Agree a replacement with '+', '.join(declines))
    level=0 if not need else 3 if month==month_of(local,store.tz) and local.day>=calendar.monthrange(local.year,local.month)[1]-6 else 2 if confirmed<need else 1
    names={0:('🟢','Target met'),1:('🟡','On track · attendance still needed'),2:('🔴','At risk'),3:('🚨','Urgent · final week')}
    icon,title=names[level]
    if need and confirmed<need: actions.append(f'Find {need-confirmed} more all-four class time(s) before month-end')
    return dict(month=month,held=held,needed=need,confirmed=confirmed,level=level,icon=icon,title=title,actions=list(dict.fromkeys(actions)))


def summary(store,month=None):
    d=danger(store,month)
    return f"{d['icon']} **{d['title']} · {d['held']}/2 completed**\n{d['confirmed']} future class(es) agreed by all four. "+('Target met.' if not d['needed'] else f"{d['needed']} more needed.")


def member_line(store,sid,member):
    s=store.session(sid);uid=member['user_id']
    a=store.one('SELECT status FROM attendance WHERE session_id=? AND user_id=?',(sid,uid))
    if a:
        return {'present':'✅ Present','absent':'❌ Absent','excused':'🤍 Excused','partial':'🟠 Partial'}[a['status']]
    if s['status']=='held': return '⏳ Attendance unresolved'
    r=next((r['response'] for r in store.responses(sid) if r['user_id']==uid),None)
    if r in {'coming','late'}: return '✅ Coming'
    if r in {'unavailable','change'}:
        p=store.one("SELECT state FROM replacement_requests WHERE session_id=? AND revision=? AND user_id=?",(sid,s['revision'],uid))
        return '📅 Replacement suggested' if p and p['state']=='suggested' else '❌ Can’t attend · time needed'
    return '⏳ Awaiting RSVP'
