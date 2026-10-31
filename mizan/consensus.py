"""Unanimous group decisions and retryable Discord announcements."""
from datetime import datetime,timedelta
import json
from .core import RuleError,stamp


def winner(store,pid):
    p=store.poll(pid);s=store.session(p['session_id'])
    if p['status']!='open' or s['revision']!=p['revision'] or store.clock()>=datetime.fromisoformat(p['deadline']): return None
    ids={m['user_id'] for m in store.participants(s['id'])}
    if len(ids)!=4: return None
    choices=[]
    for n,dt in enumerate(p['options']):
        votes={v['user_id'] for v in store.rows('SELECT user_id FROM votes WHERE poll_id=? AND slot=?',(pid,n))}
        if votes==ids and datetime.fromisoformat(dt)>store.clock()+timedelta(minutes=2): choices.append((dt,n))
    return min(choices)[1] if choices else None


def settle(store,pid):
    n=winner(store,pid)
    if n is None: return None
    p=store.poll(pid);old=store.session(p['session_id'])
    with store.transaction():
        sid=store.confirm_slot(pid,n,0)
        s=store.session(sid)
        for m in store.participants(sid):
            # These members explicitly voted for this exact time; their old RSVPs are not copied.
            store.db.execute("INSERT INTO rsvps VALUES(?,?,?,'coming','Agreed unanimously in replacement poll',?)",(sid,s['revision'],m['user_id'],stamp(store.clock())))
            store.db.execute('INSERT INTO commitments VALUES(?,?,?,?,NULL)',(sid,s['revision'],m['user_id'],stamp(store.clock())))
        store.db.execute("UPDATE replacement_requests SET state='resolved' WHERE session_id=? AND revision=?",(sid,old['revision']))
        store.db.execute('INSERT INTO auto_moves VALUES(?,?,?,?,?,0)',(pid,sid,s['revision'],old['start_at'],s['start_at']))
    return sid


def finish_round(store,pid):
    p=store.poll(pid);s=store.session(p['session_id'])
    if p['status']!='open' or p['revision']!=s['revision']: return False
    ids={m['user_id'] for m in store.participants(s['id'])}
    complete=all(store.poll_answer(pid,uid,n)!='waiting' for uid in ids for n in range(len(p['options'])))
    if store.clock()<datetime.fromisoformat(p['deadline']) and not complete: return False
    if winner(store,pid) is not None: return False
    with store.transaction():
        store.db.execute("UPDATE polls SET status='no_agreement' WHERE id=?",(pid,))
        for uid in ids:
            store.db.execute("INSERT INTO replacement_requests VALUES(?,?,?,NULL,'pending',?) ON CONFLICT(session_id,revision,user_id) DO UPDATE SET proposed_at=NULL,state='pending',updated_at=excluded.updated_at",(s['id'],s['revision'],uid,stamp(store.clock())))
        store.log(0,'reschedule_no_agreement',{'poll':pid,'session':s['id']})
    return True


async def update(bot,pid):
    settle(bot.store,pid)
    await flush(bot)
    closed=finish_round(bot.store,pid)
    await bot.update_poll(pid,create=True)
    if closed:
        from .flow import SuggestionReminder
        p=bot.store.poll(pid)
        await bot.notify_once(f'no-agreement:{pid}',
            '📅 **No time works for all four yet.** Everyone: suggest a new alternative. We will vote again; the original time has not moved.',
            view=SuggestionReminder(bot,p['session_id'],p['revision']))


async def flush(bot):
    async with bot.move_lock:
        await _flush(bot)


async def _flush(bot):
    from .bot import when
    for move in bot.store.rows('SELECT * FROM auto_moves WHERE announced=0 ORDER BY poll_id'):
        sid=move['session_id']
        await bot.update_card(sid,create=True)
        await bot.update_poll(move['poll_id'])
        await bot.notify_once(f"auto-move:{move['poll_id']}",
            f"✅ **All four agreed — class #{sid} automatically moved.**\nPrevious: {when(move['old_start'])}\nNew: {when(move['new_start'])}\nYour votes count as RSVPs for this time. Reminders are updated.")
        with bot.store.db: bot.store.db.execute('UPDATE auto_moves SET announced=1 WHERE poll_id=?',(move['poll_id'],))
