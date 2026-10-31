"""Persistent Discord → SMS → call jobs; responses cancel the remaining steps."""
from datetime import datetime,timedelta
import discord
from .core import RuleError,stamp
from .ui import Menu,guard_context

ACTIVE=('waiting_sms','waiting_call')


def recover_interrupted(store):
    with store.db:
        store.db.execute("UPDATE wuphf_jobs SET state='uncertain',reason='Restart during provider request' WHERE state IN ('sending_sms','sending_call')")


def still_needed(store,job):
    if job['source']!='auto':return True
    s=store.session(job['session_id'])
    if s['status']!='scheduled' or s['revision']!=job['revision'] or store.clock()>=datetime.fromisoformat(s['start_at']):return False
    if not store.one('SELECT 1 FROM session_members WHERE session_id=? AND user_id=?',(s['id'],job['user_id'])):return False
    # Any real RSVP (including can't attend) ends the unanswered-RSVP escalation.
    return not store.one('SELECT 1 FROM rsvps WHERE session_id=? AND revision=? AND user_id=?',(s['id'],s['revision'],job['user_id']))


def enqueue_auto(bot,s,uid):
    try:bot.wuphf.phone(uid)
    except RuleError:return None # No provider or private phone mapping yet.
    now=bot.store.clock()
    with bot.store.db:
        bot.store.db.execute("INSERT OR IGNORE INTO wuphf_jobs(user_id,actor,source,session_id,revision,created_at,sms_due,call_delay) VALUES(?,0,'auto',?,?,?,?,120)",(uid,s['id'],s['revision'],stamp(now),stamp(now+timedelta(hours=2))))
    return bot.store.one("SELECT id FROM wuphf_jobs WHERE source='auto' AND session_id=? AND revision=? AND user_id=?",(s['id'],s['revision'],uid))['id']


class Acknowledge(Menu):
    def __init__(self,bot,jid):
        super().__init__(bot,persistent=True)
        async def ack(i):
            guard_context(bot,i);bot.check_guild(i)
            job=bot.store.one('SELECT * FROM wuphf_jobs WHERE id=?',(jid,))
            if not job or (i.user.id!=job['user_id'] and not hasattr(bot,'demo_owner')):raise RuleError('This WUPHF is for the mentioned person. Only they can acknowledge it.')
            with bot.store.db:bot.store.db.execute("UPDATE wuphf_jobs SET state='acknowledged' WHERE id=? AND state IN ('waiting_sms','waiting_call','sending_sms','sending_call')",(jid,))
            await i.response.edit_message(content=f"✅ <@{job['user_id']}> checked in. The telephone department stands down.",view=None)
        self.button('I’m here — stop this WUPHF',ack,custom_id=f'standly:wuphf-ack:{jid}',style=discord.ButtonStyle.success)


async def create_manual(bot,interaction,uid):
    bot.check_guild(interaction)
    bot.wuphf.phone(uid)
    prefs=bot.wuphf.preferences(uid)
    if not prefs.get('sms') or not prefs.get('calls'):raise RuleError('This person has stopped phone nudges. They can use /resumenudges themselves.')
    s=bot.store
    if s.one("SELECT id FROM wuphf_jobs WHERE user_id=? AND state IN ('waiting_sms','waiting_call','sending_sms','sending_call')",(uid,)):
        raise RuleError('A WUPHF is already pending for this person. They can acknowledge it on its card.')
    now=s.clock()
    with s.db:
        jid=s.db.execute("INSERT INTO wuphf_jobs(user_id,actor,source,created_at,sms_due,call_delay) VALUES(?,?,'manual',?,?,60)",(uid,interaction.user.id,stamp(now),stamp(now))).lastrowid
        s.log(interaction.user.id,'wuphf_requested',{'job':jid,'recipient':uid})
    await interaction.response.defer(ephemeral=True)
    channel=await bot.channel()
    msg=await channel.send(f'📣 **WUPHF!** <@{uid}> — <@{interaction.user.id}> is looking for you.\nأَيْنَ أَنْتَ؟ حَتَّى هَاتِفُكَ يَسْأَلُ عَنْكَ!\nEven your phone is asking where you are.\nText queued now; one call an hour after the text if you haven’t checked in. Quiet hours and shared limits apply.',view=Acknowledge(bot,jid))
    with s.db:s.db.execute('UPDATE wuphf_jobs SET message_id=? WHERE id=?',(msg.id,jid))
    await process(bot)
    await interaction.followup.send('📣 WUPHF created. The mentioned person can stop it with **I’m here**. They can stop all phone nudges with /stopnudges.',ephemeral=True)
    return jid


async def process(bot):
    async with bot.wuphf_lock:
        await _process(bot)


async def _process(bot):
    s=bot.store
    # A process crash during a provider request is ambiguous: never repeat it or advance to a call.
    for job in s.rows("SELECT * FROM wuphf_jobs WHERE state IN ('waiting_sms','waiting_call') ORDER BY id"):
        uid=job['user_id']
        if job['source']=='manual' and s.clock()>=datetime.fromisoformat(job['created_at'])+timedelta(hours=24):
            with s.db:s.db.execute("UPDATE wuphf_jobs SET state='expired' WHERE id=?",(job['id'],))
            continue
        if not still_needed(s,job):
            with s.db:s.db.execute("UPDATE wuphf_jobs SET state='answered' WHERE id=?",(job['id'],))
            continue
        prefs=bot.wuphf.preferences(uid)
        if not prefs.get('sms') or not prefs.get('calls'):
            with s.db:s.db.execute("UPDATE wuphf_jobs SET state='stopped' WHERE id=?",(job['id'],))
            continue
        mode='sms' if job['state']=='waiting_sms' else 'calls'
        due=job['sms_due'] if mode=='sms' else job['call_due']
        if not due or s.clock()<datetime.fromisoformat(due):continue
        try:bot.wuphf.check(uid,mode)
        except RuleError as exc:
            if 'hiatus' in str(exc):
                with s.db:s.db.execute("UPDATE wuphf_jobs SET state='stopped' WHERE id=?",(job['id'],))
            continue # Quiet hours, cooldown, daily cap, or unconfigured provider: wait.
        sending='sending_sms' if mode=='sms' else 'sending_call'
        with s.db:s.db.execute('UPDATE wuphf_jobs SET state=? WHERE id=?',(sending,job['id']))
        if job['source']=='auto':
            session=s.session(job['session_id'])
            url=f"https://discord.com/channels/{s.get('guild_id')}/{s.get('channel_id')}/{session['message_id']}" if session['message_id'] else 'Please open Discord.'
            text=f"Arabic class: please RSVP in Discord. {url} Your textbook has answered more questions than you today."
        else:
            gid=s.get('guild_id');cid=s.get('channel_id')
            text=f"Someone in Arabic Class is looking for you. Tap I'm here on your WUPHF card to stop the follow-up: https://discord.com/channels/{gid}/{cid}/{job['message_id']}"
        if mode=='calls':
            text='Your Arabic class RSVP is still missing. Please open Discord and answer the class card. The duck is waiting.' if job['source']=='auto' else 'Someone in Arabic Class is looking for you. Please open Discord and tap I am here on your WUPHF card.'
        try:await bot.wuphf.send(bot.http_client,uid,mode,text)
        except Exception as exc:
            with s.db:
                s.db.execute("UPDATE wuphf_jobs SET state='uncertain',reason=? WHERE id=?",(type(exc).__name__,job['id']))
            await bot.notify_once(f"wuphf-error:{job['id']}",f"TA: WUPHF #{job['id']} could not be confirmed. It will not retry or escalate; check the phone provider before trying again.")
            continue
        sent=stamp(s.clock())
        with s.db:
            if not still_needed(s,job):
                s.db.execute("UPDATE wuphf_jobs SET state='answered' WHERE id=? AND state=?",(job['id'],sending))
            elif mode=='sms':s.db.execute("UPDATE wuphf_jobs SET state='waiting_call',sms_sent=?,call_due=? WHERE id=? AND state=?",(sent,stamp(s.clock()+timedelta(minutes=job['call_delay'])),job['id'],sending))
            else:s.db.execute("UPDATE wuphf_jobs SET state='done',call_sent=? WHERE id=? AND state=?",(sent,job['id'],sending))
