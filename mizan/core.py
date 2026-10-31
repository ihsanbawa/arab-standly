"""Persistent domain rules; no Discord or financial side effects here."""

from __future__ import annotations

import calendar
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

UTC = timezone.utc
RSVPS = {"coming", "late", "unavailable", "change"}


class RuleError(ValueError):
    pass


def now_utc():
    return datetime.now(UTC)


def stamp(dt):
    if dt.tzinfo is None:
        raise RuleError("A timezone is required.")
    return dt.astimezone(UTC).isoformat()


def parse_local(text, tz):
    """Reject ambiguous/nonexistent local times instead of guessing across DST."""
    try:
        dt = datetime.fromisoformat(text)
        zone = ZoneInfo(tz)
    except (ValueError, KeyError) as exc:
        raise RuleError("Use YYYY-MM-DD HH:MM and a valid IANA timezone.") from exc
    if dt.tzinfo:
        return dt.astimezone(UTC)
    a, b = dt.replace(tzinfo=zone, fold=0), dt.replace(tzinfo=zone, fold=1)
    if a.utcoffset() != b.utcoffset():
        raise RuleError("This local time is ambiguous or does not exist. Supply an explicit UTC offset.")
    if a.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != dt:
        raise RuleError("This local time does not exist.")
    return a.astimezone(UTC)


def month_of(dt, tz):
    return dt.astimezone(ZoneInfo(tz)).strftime("%Y-%m")


def month_bounds(month, tz):
    try:
        d = datetime.strptime(month, "%Y-%m")
    except ValueError as exc:
        raise RuleError("Month must be YYYY-MM.") from exc
    zone = ZoneInfo(tz)
    start = d.replace(tzinfo=zone)
    end = (d.replace(day=28) + timedelta(days=4)).replace(day=1, tzinfo=zone)
    return stamp(start), stamp(end)


SCHEMA = """
PRAGMA foreign_keys=ON;
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS members(user_id INTEGER PRIMARY KEY,name TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS sessions(
 id INTEGER PRIMARY KEY AUTOINCREMENT,start_at TEXT NOT NULL,duration INTEGER NOT NULL,
 location TEXT NOT NULL,topic TEXT NOT NULL,prep TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'scheduled',revision INTEGER NOT NULL DEFAULT 0,
 rsvp_due TEXT NOT NULL,message_id INTEGER,created_by INTEGER NOT NULL,
 confirmation_due TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS session_members(
 session_id INTEGER NOT NULL REFERENCES sessions(id),user_id INTEGER NOT NULL,
 PRIMARY KEY(session_id,user_id));
CREATE TABLE IF NOT EXISTS rsvps(
 session_id INTEGER NOT NULL REFERENCES sessions(id),revision INTEGER NOT NULL,user_id INTEGER NOT NULL,
 response TEXT NOT NULL,note TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL,
 PRIMARY KEY(session_id,revision,user_id));
CREATE TABLE IF NOT EXISTS commitments(
 session_id INTEGER NOT NULL,revision INTEGER NOT NULL,user_id INTEGER NOT NULL,
 accepted_at TEXT NOT NULL,withdrawn_at TEXT,
 PRIMARY KEY(session_id,revision,user_id));
CREATE TABLE IF NOT EXISTS attendance(
 session_id INTEGER NOT NULL REFERENCES sessions(id),user_id INTEGER NOT NULL,
 status TEXT NOT NULL,reason TEXT NOT NULL DEFAULT '',PRIMARY KEY(session_id,user_id));
CREATE TABLE IF NOT EXISTS ledger(
 event_key TEXT PRIMARY KEY,session_id INTEGER,user_id INTEGER NOT NULL,month TEXT NOT NULL,
 points INTEGER NOT NULL,reason TEXT NOT NULL,actor INTEGER NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS penalties(
 session_id INTEGER NOT NULL,user_id INTEGER NOT NULL,points INTEGER NOT NULL,
 PRIMARY KEY(session_id,user_id));
CREATE TABLE IF NOT EXISTS penalty_causes(
 session_id INTEGER NOT NULL,user_id INTEGER NOT NULL,cause TEXT NOT NULL,
 points INTEGER NOT NULL,reason TEXT NOT NULL,month TEXT NOT NULL,
 PRIMARY KEY(session_id,user_id,cause));
CREATE TABLE IF NOT EXISTS polls(
 id INTEGER PRIMARY KEY AUTOINCREMENT,session_id INTEGER NOT NULL REFERENCES sessions(id),
 revision INTEGER NOT NULL,options TEXT NOT NULL,deadline TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'open',message_id INTEGER);
CREATE TABLE IF NOT EXISTS votes(
 poll_id INTEGER NOT NULL REFERENCES polls(id),user_id INTEGER NOT NULL,slot INTEGER NOT NULL,
 PRIMARY KEY(poll_id,user_id,slot));
CREATE TABLE IF NOT EXISTS poll_no(
 poll_id INTEGER NOT NULL REFERENCES polls(id),user_id INTEGER NOT NULL,slot INTEGER NOT NULL,
 PRIMARY KEY(poll_id,user_id,slot));
CREATE TABLE IF NOT EXISTS poll_cards(
 poll_id INTEGER NOT NULL REFERENCES polls(id),slot INTEGER NOT NULL,message_id INTEGER NOT NULL,
 PRIMARY KEY(poll_id,slot));
CREATE TABLE IF NOT EXISTS notices(key TEXT PRIMARY KEY,sent_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(
 id INTEGER PRIMARY KEY AUTOINCREMENT,actor INTEGER NOT NULL,action TEXT NOT NULL,
 details TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS replacement_requests(
 session_id INTEGER NOT NULL,revision INTEGER NOT NULL,user_id INTEGER NOT NULL,
 proposed_at TEXT,state TEXT NOT NULL DEFAULT 'pending',updated_at TEXT NOT NULL,
 PRIMARY KEY(session_id,revision,user_id));
CREATE TABLE IF NOT EXISTS auto_moves(
 poll_id INTEGER PRIMARY KEY,session_id INTEGER NOT NULL,revision INTEGER NOT NULL,
 old_start TEXT NOT NULL,new_start TEXT NOT NULL,announced INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS lesson_notes(
 session_id INTEGER PRIMARY KEY,revision INTEGER NOT NULL,recap TEXT NOT NULL,
 next_lesson TEXT NOT NULL,prep TEXT NOT NULL,actor INTEGER NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS deliveries(
 month TEXT NOT NULL,binding TEXT NOT NULL,value INTEGER NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending',request_id TEXT NOT NULL,datapoint_id TEXT,
 attempts INTEGER NOT NULL DEFAULT 0,error TEXT,updated_at TEXT NOT NULL,
 PRIMARY KEY(month,binding));
"""


class Store:
    def __init__(self, path, clock=now_utc):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.clock = clock

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        nested=self.db.in_transaction
        self._savepoint=getattr(self,'_savepoint',0)+1
        name=f'mizan_{self._savepoint}'
        self.db.execute(f'SAVEPOINT {name}' if nested else 'BEGIN')
        try:
            yield
        except BaseException:
            if nested:
                self.db.execute(f'ROLLBACK TO {name}')
                self.db.execute(f'RELEASE {name}')
            else: self.db.rollback()
            raise
        else:
            if nested: self.db.execute(f'RELEASE {name}')
            else: self.db.commit()

    def rows(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        r = self.db.execute(sql, args).fetchone()
        return dict(r) if r else None

    def get(self, key, default=None):
        r = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(r["value"]) if r else default

    def set(self, key, value):
        with self.db:
            self.db.execute(
                "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    @property
    def tz(self):
        return self.get("timezone", "America/Chicago")

    def log(self, actor, action, details):
        self.db.execute(
            "INSERT INTO audit(actor,action,details,created_at) VALUES(?,?,?,?)",
            (actor, action, json.dumps(details), stamp(self.clock())),
        )

    def member(self, uid, name, active=True):
        with self.db:
            self.db.execute(
                "INSERT INTO members VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET name=excluded.name,active=excluded.active",
                (uid, name, int(active)),
            )

    def roster(self):
        return self.rows("SELECT * FROM members WHERE active=1 ORDER BY name")

    def session(self, sid):
        r = self.one("SELECT * FROM sessions WHERE id=?", (sid,))
        if not r:
            raise RuleError("Class not found.")
        return r

    def participants(self, sid):
        return self.rows(
            "SELECT sm.user_id,COALESCE(m.name,'Member') name FROM session_members sm LEFT JOIN members m USING(user_id) WHERE session_id=?",
            (sid,),
        )

    def ensure_participant(self, sid, uid):
        if not self.one("SELECT 1 FROM session_members WHERE session_id=? AND user_id=?", (sid, uid)):
            raise RuleError("You are not on this class's roster. Ask a TA to add you.")

    def ensure_unsealed(self, s):
        m = month_of(datetime.fromisoformat(s["start_at"]), self.tz)
        if self.one("SELECT 1 FROM deliveries WHERE month=?", (m,)):
            raise RuleError(
                "This month has a Beeminder audit. Reconcile it with the TA before changing class records."
            )

    def create_class(self, start, duration, location, topic, actor, prep="", due=None, weekly_key=None):
        if not self.roster():
            raise RuleError("Add the class members first.")
        if start <= self.clock():
            raise RuleError("Schedule a future class.")
        if self.one(
            "SELECT 1 FROM sessions WHERE start_at=? AND status IN ('scheduled','held')", (stamp(start),)
        ):
            raise RuleError("A class is already scheduled at that time.")
        if not 10 <= duration <= 240:
            raise RuleError("Duration must be 10–240 minutes.")
        if not location.strip() or not topic.strip():
            raise RuleError("Location and topic are required.")
        due = due or max(self.clock() + timedelta(minutes=1), start - timedelta(hours=48))
        if not self.clock() < due < start:
            raise RuleError("RSVP deadline must be in the future and before class.")
        # Reserve a small month-end audit window; otherwise an in-progress class could be charged as missed.
        end = start + timedelta(minutes=duration)
        local_end = end.astimezone(ZoneInfo(self.tz))
        if local_end.day == calendar.monthrange(local_end.year, local_end.month)[1] and local_end.hour == 23:
            raise RuleError(
                "Month-end classes must finish before 23:00 to leave time for financial confirmation."
            )
        with self.transaction():
            cursor = self.db.execute(
                "INSERT INTO sessions(start_at,duration,location,topic,prep,rsvp_due,created_by,confirmation_due,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    stamp(start),
                    duration,
                    location,
                    topic,
                    prep,
                    stamp(due),
                    actor,
                    stamp(end),
                    stamp(self.clock()),
                ),
            )
            sid = cursor.lastrowid
            self.db.executemany(
                "INSERT INTO session_members VALUES(?,?)", [(sid, m["user_id"]) for m in self.roster()]
            )
            self.log(actor, "class_created", {"session": sid})
            if weekly_key:
                self.db.execute('INSERT INTO settings VALUES(?,?)', (weekly_key, json.dumps(sid)))
        return sid

    def rsvp(self, sid, uid, response, note="", revision=None):
        s = self.session(sid)
        self.ensure_participant(sid, uid)
        if s["status"] != "scheduled" or datetime.fromisoformat(s["start_at"]) <= self.clock():
            raise RuleError("RSVPs are closed for this class. Report a disruption instead.")
        if revision is not None and revision != s["revision"]:
            raise RuleError("That card is outdated. Use /nextclass for the current class.")
        if response not in RSVPS:
            raise RuleError("Unknown RSVP response.")
        with self.db:
            self.db.execute(
                "INSERT INTO rsvps VALUES(?,?,?,?,?,?) ON CONFLICT(session_id,revision,user_id) DO UPDATE SET response=excluded.response,note=excluded.note,updated_at=excluded.updated_at",
                (sid, s["revision"], uid, response, note, stamp(self.clock())),
            )
            if response in {"coming", "late"}:
                self.db.execute(
                    "INSERT OR IGNORE INTO commitments VALUES(?,?,?,?,NULL)",
                    (sid, s["revision"], uid, stamp(self.clock())),
                )
            else:
                self.db.execute(
                    "UPDATE commitments SET withdrawn_at=COALESCE(withdrawn_at,?) WHERE session_id=? AND revision=? AND user_id=?",
                    (stamp(self.clock()), sid, s["revision"], uid),
                )
            self.log(uid, "rsvp", {"session": sid, "response": response})

    def answer(self, sid, uid, coming, revision=None):
        s = self.session(sid)
        self.ensure_unsealed(s)
        self.rsvp(sid, uid, 'coming' if coming else 'unavailable', revision=revision)
        with self.transaction():
            if coming:
                self.db.execute("UPDATE replacement_requests SET state='withdrawn' WHERE session_id=? AND revision=? AND user_id=?", (sid,s['revision'],uid))
            else:
                points = -2 if datetime.fromisoformat(s['start_at'])-self.clock()<timedelta(hours=24) else -1
                self.penalty(sid,uid,points,"Can't attend scheduled class",uid)
                self.db.execute("INSERT INTO replacement_requests VALUES(?,?,?,NULL,'pending',?) ON CONFLICT(session_id,revision,user_id) DO UPDATE SET state=CASE WHEN proposed_at IS NULL THEN 'pending' ELSE 'suggested' END,updated_at=excluded.updated_at", (sid,s['revision'],uid,stamp(self.clock())))
        return self.one('SELECT points FROM penalties WHERE session_id=? AND user_id=?',(sid,uid))

    def suggest(self, sid, uid, proposed, revision):
        s=self.session(sid)
        self.ensure_participant(sid,uid)
        self.ensure_unsealed(s)
        if s['revision']!=revision or s['status'] not in {'scheduled','needs_replacement'}:
            raise RuleError('This class changed. Open its current invitation.')
        response=self.one('SELECT response FROM rsvps WHERE session_id=? AND revision=? AND user_id=?',(sid,revision,uid))
        rescheduling=self.one("SELECT 1 FROM polls WHERE session_id=? AND revision=? AND status IN ('open','no_agreement')",(sid,revision))
        if (not response or response['response'] not in {'unavailable','change'}) and not rescheduling and s['status']!='needs_replacement':
            raise RuleError("Choose I can't before suggesting a replacement.")
        if proposed<=self.clock()+timedelta(minutes=5) or stamp(proposed)==s['start_at']:
            raise RuleError('Choose a different time at least five minutes in the future.')
        finish=(proposed+timedelta(minutes=s['duration'])).astimezone(ZoneInfo(self.tz))
        if finish.day==calendar.monthrange(finish.year,finish.month)[1] and finish.hour==23:
            raise RuleError('Month-end replacements must finish before 23:00.')
        p=self.one("SELECT id FROM polls WHERE session_id=? AND revision=? AND status='open' AND deadline>? ORDER BY id DESC LIMIT 1",(sid,revision,stamp(self.clock())))
        options=self.poll(p['id'])['options'] if p else []
        if stamp(proposed) not in options:
            if len(options)>=3: raise RuleError('This round already has three alternatives. Save your availability for these; if none works for everyone, a fresh round will open.')
            options.append(stamp(proposed))
        due=min(self.clock()+timedelta(hours=24),min(datetime.fromisoformat(x) for x in options)-timedelta(minutes=1))
        with self.transaction():
            if p:
                pid=p['id']
                self.db.execute('UPDATE polls SET options=?,deadline=? WHERE id=?',(json.dumps(options),stamp(due),pid))
            else:
                pid=self.open_poll(sid,options,due,uid)
            self.db.execute("INSERT INTO replacement_requests VALUES(?,?,?,?,'suggested',?) ON CONFLICT(session_id,revision,user_id) DO UPDATE SET proposed_at=excluded.proposed_at,state='suggested',updated_at=excluded.updated_at",(sid,revision,uid,stamp(proposed),stamp(self.clock())))
            self.db.execute('DELETE FROM votes WHERE poll_id=? AND user_id=? AND slot=-1',(pid,uid))
            self.db.execute('DELETE FROM poll_no WHERE poll_id=? AND user_id=? AND slot=?',(pid,uid,options.index(stamp(proposed))))
            self.db.execute('INSERT OR IGNORE INTO votes VALUES(?,?,?)',(pid,uid,options.index(stamp(proposed))))
            self.log(uid,'replacement_suggested',{'session':sid,'time':stamp(proposed),'poll':pid})
        return pid

    def suggest_many(self,sid,uid,proposed,revision):
        times=list(dict.fromkeys(proposed))
        if not times: raise RuleError('Select at least one time you can attend, then save.')
        # Publish one poll only after every selected time has passed validation.
        with self.transaction():
            pid=None
            for dt in times: pid=self.suggest(sid,uid,dt,revision)
        return pid

    def save_lesson(self,sid,actor,recap,next_lesson,prep=''):
        s=self.session(sid)
        if not recap.strip() or not next_lesson.strip():
            raise RuleError('Tell us what you covered and what comes next.')
        self.held(sid,actor)
        with self.transaction():
            self.db.execute('INSERT INTO lesson_notes VALUES(?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET revision=excluded.revision,recap=excluded.recap,next_lesson=excluded.next_lesson,prep=excluded.prep,actor=excluded.actor,updated_at=excluded.updated_at', (sid,s['revision'],recap.strip(),next_lesson.strip(),prep.strip(),actor,stamp(self.clock())))
            self.log(actor,'lesson_recap_saved',{'session':sid})

    def latest_lesson(self,before):
        return self.one("SELECT n.*,s.start_at FROM lesson_notes n JOIN sessions s ON s.id=n.session_id WHERE s.status='held' AND n.revision=s.revision AND s.start_at<? ORDER BY s.start_at DESC LIMIT 1",(stamp(before),))

    def responses(self, sid):
        s = self.session(sid)
        return self.rows("SELECT * FROM rsvps WHERE session_id=? AND revision=?", (sid, s["revision"]))

    def open_poll(self, sid, options, deadline, actor):
        s = self.session(sid)
        if s["status"] not in ("scheduled", "needs_replacement"):
            raise RuleError("Only scheduled or unresolved classes can be moved.")
        self.ensure_unsealed(s)
        options=[stamp(datetime.fromisoformat(x)) for x in options]
        for option in options:
            finish=(datetime.fromisoformat(option)+timedelta(minutes=s['duration'])).astimezone(ZoneInfo(self.tz))
            if finish.day==calendar.monthrange(finish.year,finish.month)[1] and finish.hour==23:
                raise RuleError('Month-end replacements must finish before 23:00.')
        if not 1 <= len(options) <= 5 or len(set(options)) != len(options):
            raise RuleError("Supply 1–5 different proposed times.")
        if not self.clock() < deadline < min(datetime.fromisoformat(x) for x in options):
            raise RuleError("Poll must close in the future and before every proposed time.")
        with self.transaction():
            self.db.execute(
                "UPDATE polls SET status='superseded' WHERE session_id=? AND status='open'", (sid,)
            )
            pid = self.db.execute(
                "INSERT INTO polls(session_id,revision,options,deadline) VALUES(?,?,?,?)",
                (sid, s["revision"], json.dumps(options), stamp(deadline)),
            ).lastrowid
            self.log(actor, "poll_opened", {"session": sid, "poll": pid})
        return pid

    def poll(self, pid):
        p = self.one("SELECT * FROM polls WHERE id=?", (pid,))
        if not p:
            raise RuleError("Poll not found.")
        p["options"] = json.loads(p["options"])
        return p

    def vote(self, pid, uid, slot):
        p = self.poll(pid)
        s = self.session(p["session_id"])
        self.ensure_participant(s["id"], uid)
        if (
            p["status"] != "open"
            or s["revision"] != p["revision"]
            or self.clock() >= datetime.fromisoformat(p["deadline"])
        ):
            raise RuleError("This poll is closed or outdated.")
        if not 0 <= slot < len(p["options"]):
            raise RuleError("Unknown option.")
        with self.db:
            old = self.one("SELECT 1 FROM votes WHERE poll_id=? AND user_id=? AND slot=?", (pid, uid, slot))
            if old:
                self.db.execute(
                    "DELETE FROM votes WHERE poll_id=? AND user_id=? AND slot=?", (pid, uid, slot)
                )
            else:
                self.db.execute("DELETE FROM votes WHERE poll_id=? AND user_id=? AND slot=-1", (pid, uid))
                self.db.execute("INSERT INTO votes VALUES(?,?,?)", (pid, uid, slot))
        return not bool(old)

    def poll_answer(self, pid, uid, slot):
        if self.one("SELECT 1 FROM votes WHERE poll_id=? AND user_id=? AND slot=?",(pid,uid,slot)):
            return 'yes'
        if self.one("SELECT 1 FROM poll_no WHERE poll_id=? AND user_id=? AND slot=?",(pid,uid,slot)):
            return 'no'
        # Compatibility with the old all-options rejection.
        if self.one("SELECT 1 FROM votes WHERE poll_id=? AND user_id=? AND slot=-1",(pid,uid)):
            return 'no'
        return 'waiting'

    def answer_poll(self, pid, uid, slot, available):
        p=self.poll(pid)
        self.ensure_participant(p['session_id'],uid)
        if p['status']!='open' or self.session(p['session_id'])['revision']!=p['revision'] or self.clock()>=datetime.fromisoformat(p['deadline']):
            raise RuleError('This reschedule round has closed or changed.')
        if not 0<=slot<len(p['options']): raise RuleError('Unknown proposed time.')
        with self.transaction():
            # Preserve old None-work answers as explicit per-option responses.
            if self.one('SELECT 1 FROM votes WHERE poll_id=? AND user_id=? AND slot=-1',(pid,uid)):
                for n in range(len(p['options'])):
                    self.db.execute('INSERT OR IGNORE INTO poll_no VALUES(?,?,?)',(pid,uid,n))
                self.db.execute('DELETE FROM votes WHERE poll_id=? AND user_id=? AND slot=-1',(pid,uid))
            self.db.execute('DELETE FROM votes WHERE poll_id=? AND user_id=? AND slot=?',(pid,uid,slot))
            self.db.execute('DELETE FROM poll_no WHERE poll_id=? AND user_id=? AND slot=?',(pid,uid,slot))
            table='votes' if available else 'poll_no'
            self.db.execute(f'INSERT INTO {table} VALUES(?,?,?)',(pid,uid,slot))
        return 'yes' if available else 'no'

    def penalty(self, sid, uid, points, reason, actor):
        """One disruption penalty per person/class. The strongest replaces the weaker."""
        self.ensure_participant(sid, uid)
        cause = "absence" if reason == "Unexcused absence" else "decline" if reason == "Can't attend scheduled class" else "disruption"
        self.db.execute(
            "INSERT INTO penalty_causes VALUES(?,?,?,?,?,?) ON CONFLICT(session_id,user_id,cause) DO UPDATE SET points=MIN(points,excluded.points),reason=excluded.reason",
            (sid, uid, cause, points, reason, month_of(self.clock(), self.tz)),
        )
        self.refresh_penalty(sid, uid, actor)

    def refresh_penalty(self, sid, uid, actor):
        worst = self.one(
            "SELECT * FROM penalty_causes WHERE session_id=? AND user_id=? ORDER BY points,month LIMIT 1",
            (sid, uid),
        )
        if not worst:
            self.db.execute("DELETE FROM penalties WHERE session_id=? AND user_id=?", (sid, uid))
            self.db.execute("DELETE FROM ledger WHERE event_key=?", (f"penalty:{sid}:{uid}",))
            return
        self.db.execute(
            "INSERT INTO penalties VALUES(?,?,?) ON CONFLICT(session_id,user_id) DO UPDATE SET points=excluded.points",
            (sid, uid, worst["points"]),
        )
        self.ledger(f"penalty:{sid}:{uid}", sid, uid, worst["points"], worst["reason"], actor, worst["month"])

    def ledger(self, key, sid, uid, points, reason, actor, month):
        self.db.execute(
            "INSERT INTO ledger VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(event_key) DO UPDATE SET month=excluded.month,points=excluded.points,reason=excluded.reason,actor=excluded.actor,updated_at=excluded.updated_at",
            (key, sid, uid, month, points, reason, actor, stamp(self.clock())),
        )

    def confirm_slot(self, pid, slot, actor, responsible=None, excused=False):
        p = self.poll(pid)
        s = self.session(p["session_id"])
        if (
            p["status"] != "open"
            or p["revision"] != s["revision"]
            or s["status"] not in ("scheduled", "needs_replacement")
        ):
            raise RuleError("This poll is no longer current.")
        if not 0 <= slot < len(p["options"]):
            raise RuleError("Unknown option.")
        self.ensure_unsealed(s)
        from .consensus import winner
        if winner(self,pid)!=slot:
            raise RuleError('A move requires all four votes. The earliest unanimously available option is selected automatically; no override is allowed.')
        start = datetime.fromisoformat(p["options"][slot])
        if start <= self.clock():
            raise RuleError("Choose a future time.")
        end = start + timedelta(minutes=s["duration"])
        local_end = end.astimezone(ZoneInfo(self.tz))
        if local_end.day == calendar.monthrange(local_end.year, local_end.month)[1] and local_end.hour == 23:
            raise RuleError("Month-end replacement must finish before 23:00.")
        due = max(self.clock() + timedelta(minutes=1), start - timedelta(hours=24))
        if due >= start:
            raise RuleError("Choose a time at least a few minutes away.")
        with self.transaction():
            if responsible and not excused:
                accepted = self.one(
                    "SELECT * FROM commitments WHERE session_id=? AND revision=? AND user_id=?",
                    (s["id"], s["revision"], responsible),
                )
                if accepted:
                    changed_at = (
                        datetime.fromisoformat(accepted["withdrawn_at"])
                        if accepted["withdrawn_at"]
                        else self.clock()
                    )
                    pts = (
                        -2 if datetime.fromisoformat(s["start_at"]) - changed_at < timedelta(hours=24) else -1
                    )
                    self.penalty(s["id"], responsible, pts, "Moved class after accepting", actor)
            self.db.execute(
                "UPDATE sessions SET start_at=?,confirmation_due=?,rsvp_due=?,status='scheduled',revision=revision+1,updated_at=? WHERE id=?",
                (stamp(start), stamp(end), stamp(due), stamp(self.clock()), s["id"]),
            )
            self.db.execute("UPDATE polls SET status='confirmed' WHERE id=?", (pid,))
            self.db.execute("DELETE FROM attendance WHERE session_id=?", (s["id"],))
            self.log(
                actor,
                "class_moved",
                {
                    "session": s["id"],
                    "old": s["start_at"],
                    "new": stamp(start),
                    "responsible": responsible,
                    "excused": excused,
                },
            )
        return s["id"]

    def disruption(self, sid, actor, reason):
        s = self.session(sid)
        if s["status"] not in ("scheduled", "needs_replacement"):
            raise RuleError("This class is already settled.")
        with self.db:
            self.log(actor, "disruption_reported", {"session": sid, "reason": reason})
        # A report does not cancel class or assign blame.

    def cancel(self, sid, actor, reason, responsible=None, excused=False):
        s = self.session(sid)
        if s["status"] == "held":
            raise RuleError("Use /reopen before changing a held class.")
        self.ensure_unsealed(s)
        with self.transaction():
            if responsible and not excused:
                self.penalty(sid, responsible, -2, "Class did not happen: " + reason, actor)
            self.db.execute(
                "UPDATE sessions SET status='needs_replacement',updated_at=? WHERE id=?",
                (stamp(self.clock()), sid),
            )
            self.db.execute(
                "UPDATE polls SET status='superseded' WHERE session_id=? AND status='open'", (sid,)
            )
            self.log(actor, "class_cancelled", {"session": sid, "reason": reason})

    def set_attendance(self, sid, uid, status, actor, reason=""):
        s = self.session(sid)
        self.ensure_participant(sid, uid)
        self.ensure_unsealed(s)
        if status not in {"present", "absent", "excused", "partial"}:
            raise RuleError("Choose present, absent, excused or partial.")
        with self.transaction():
            self.db.execute(
                "INSERT INTO attendance VALUES(?,?,?,?) ON CONFLICT(session_id,user_id) DO UPDATE SET status=excluded.status,reason=excluded.reason",
                (sid, uid, status, reason),
            )
            if status == "absent":
                self.penalty(sid, uid, -2, "Unexcused absence", actor)
            elif status in {"excused", "present", "partial"}:
                # Remove an absence penalty, but retain a separate confirmed reschedule penalty.
                self.db.execute(
                    "DELETE FROM penalty_causes WHERE session_id=? AND user_id=? AND cause='absence'",
                    (sid, uid),
                )
                if status == 'excused':
                    self.db.execute("DELETE FROM penalty_causes WHERE session_id=? AND user_id=? AND cause='decline'",(sid,uid))
                self.refresh_penalty(sid, uid, actor)
            if s["status"] == "held":
                pts = 1 if status == "present" else 0
                self.ledger(
                    f"attend:{sid}:{uid}",
                    sid,
                    uid,
                    pts,
                    status,
                    actor,
                    month_of(datetime.fromisoformat(s["start_at"]), self.tz),
                )
            self.log(
                actor,
                "attendance_corrected",
                {"session": sid, "user": uid, "status": status, "reason": reason},
            )

    def held(self, sid, actor):
        s = self.session(sid)
        if s["status"] == "held":
            return False
        if s["status"] != "scheduled" or self.clock() < datetime.fromisoformat(s["confirmation_due"]):
            raise RuleError(
                "Confirm only a scheduled class after its scheduled end. Move cancelled sessions first."
            )
        self.ensure_unsealed(s)
        missing = [
            p["user_id"]
            for p in self.participants(sid)
            if not self.one("SELECT 1 FROM attendance WHERE session_id=? AND user_id=?", (sid, p["user_id"]))
            and not self.one(
                "SELECT 1 FROM rsvps WHERE session_id=? AND revision=? AND user_id=? AND response IN ('coming','late')",
                (sid, s["revision"], p["user_id"]),
            )
        ]
        if missing:
            raise RuleError(
                "Not everyone accepted. Set present/absent/excused/partial for unresolved members with /attendance first."
            )
        with self.transaction():
            for p in self.participants(sid):
                a = self.one("SELECT * FROM attendance WHERE session_id=? AND user_id=?", (sid, p["user_id"]))
                status = a["status"] if a else "present"
                self.db.execute(
                    "INSERT OR IGNORE INTO attendance VALUES(?,?,?,'Assumed from accepted RSVP; TA confirmed class held')",
                    (sid, p["user_id"], status),
                )
                self.ledger(
                    f"attend:{sid}:{p['user_id']}",
                    sid,
                    p["user_id"],
                    1 if status == "present" else 0,
                    status,
                    actor,
                    month_of(datetime.fromisoformat(s["start_at"]), self.tz),
                )
            self.db.execute(
                "UPDATE sessions SET status='held',updated_at=? WHERE id=?", (stamp(self.clock()), sid)
            )
            self.db.execute(
                "UPDATE polls SET status='superseded' WHERE session_id=? AND status='open'", (sid,)
            )
            self.log(actor, "class_held", {"session": sid})
        return True

    def reopen(self, sid, actor):
        s = self.session(sid)
        self.ensure_unsealed(s)
        with self.transaction():
            self.db.execute("UPDATE sessions SET status='scheduled' WHERE id=?", (sid,))
            self.db.execute("DELETE FROM ledger WHERE session_id=? AND event_key LIKE 'attend:%'", (sid,))
            self.log(actor, "class_reopened", {"session": sid})

    def waive(self, sid, uid, actor, reason):
        self.ensure_unsealed(self.session(sid))
        with self.transaction():
            self.db.execute("DELETE FROM penalty_causes WHERE session_id=? AND user_id=?", (sid, uid))
            self.db.execute("DELETE FROM penalties WHERE session_id=? AND user_id=?", (sid, uid))
            self.db.execute("DELETE FROM ledger WHERE event_key=?", (f"penalty:{sid}:{uid}",))
            self.log(actor, "penalty_waived", {"session": sid, "user": uid, "reason": reason})

    def monthly_count(self, month):
        from .progress import monthly_count
        return monthly_count(self,month)

    def standings(self, month):
        month_bounds(month, self.tz)
        return self.rows(
            "SELECT m.user_id,m.name,COALESCE(SUM(l.points),0) points FROM members m LEFT JOIN ledger l ON m.user_id=l.user_id AND l.month=? GROUP BY m.user_id ORDER BY points DESC,m.name",
            (month,),
        )

    def unsettled(self, month):
        a, b = month_bounds(month, self.tz)
        return self.rows(
            "SELECT * FROM sessions WHERE status='scheduled' AND start_at>=? AND start_at<?", (a, b)
        )

    def next_classes(self):
        return self.rows(
            "SELECT * FROM sessions WHERE status IN ('scheduled','needs_replacement') ORDER BY CASE status WHEN 'needs_replacement' THEN 0 ELSE 1 END,start_at"
        )

    def notice_sent(self, key):
        return bool(self.one("SELECT 1 FROM notices WHERE key=?", (key,)))

    def mark_notice(self, key):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO notices VALUES(?,?)", (key, stamp(self.clock())))
