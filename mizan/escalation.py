"""Persistent Discord → text → ruder text jobs; responses cancel the remaining steps.

Phone calls are retired. The stored state names 'waiting_call'/'sending_call' and the
call_* columns are kept so existing records and code rollbacks stay compatible: they
now mean the second, follow-up text.
"""
import os
from datetime import datetime,timedelta
import discord
from .core import RuleError,stamp
from .ui import Menu,guard_context
from .content import roast,sms_roast
from .wuphf import fit_sms

ACTIVE=('waiting_sms','waiting_call')


def recover_interrupted(store):
    with store.db:
        store.db.execute("UPDATE wuphf_jobs SET state='uncertain',reason='Restart during provider request' WHERE state IN ('sending_sms','sending_call')")


def replies_enabled():
    """Text replies only work once Twilio is pointed at this bot's webhook."""
    return bool(os.getenv('TWILIO_WEBHOOK_URL'))


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


def may_summon(bot,interaction):
    """Only the class roster (or the bot owner) may set the telephone department on someone."""
    if hasattr(bot,'demo_owner'):return
    uid=interaction.user.id
    if uid==int(os.environ.get('BOT_OWNER_ID') or 0):return
    if not bot.store.one('SELECT 1 FROM members WHERE user_id=? AND active=1',(uid,)):
        raise RuleError('Only class roster members can WUPHF someone.')


async def create_manual(bot,interaction,uid):
    bot.check_guild(interaction)
    may_summon(bot,interaction)
    bot.wuphf.phone(uid)
    prefs=bot.wuphf.preferences(uid)
    if not prefs.get('sms'):raise RuleError('This person has stopped phone nudges. They can use /resumenudges themselves.')
    s=bot.store
    if s.one("SELECT id FROM wuphf_jobs WHERE user_id=? AND state IN ('waiting_sms','waiting_call','sending_sms','sending_call')",(uid,)):
        raise RuleError('A WUPHF is already pending for this person. They can acknowledge it on its card.')
    now=s.clock()
    with s.db:
        jid=s.db.execute("INSERT INTO wuphf_jobs(user_id,actor,source,created_at,sms_due,call_delay) VALUES(?,?,'manual',?,?,60)",(uid,interaction.user.id,stamp(now),stamp(now))).lastrowid
        s.log(interaction.user.id,'wuphf_requested',{'job':jid,'recipient':uid})
    await interaction.response.defer(ephemeral=True)
    channel=await bot.channel()
    how='Tap **I’m here** or reply HERE to the text.' if replies_enabled() else 'Tap **I’m here** to make it stop.'
    msg=await channel.send(f'📣 **WUPHF!** <@{uid}> — <@{interaction.user.id}> is looking for you.\n{roast(jid,s,uid)}\nText queued now; a ruder one in an hour if you haven’t checked in, and everyone here gets to watch. {how} Quiet hours and shared limits apply.',view=Acknowledge(bot,jid))
    with s.db:s.db.execute('UPDATE wuphf_jobs SET message_id=? WHERE id=?',(msg.id,jid))
    await process(bot)
    await interaction.followup.send('📣 WUPHF created. The mentioned person can stop it with **I’m here**. They can stop all phone nudges with /stopnudges.',ephemeral=True)
    return jid


def sms_text(bot,job,followup):
    """Opener, Arabic roast, English roast, reply instruction; wuphf.fit_sms drops whole lines to stay within 3 parts."""
    s=bot.store;uid=job['user_id']
    arabic,english=sms_roast(s,uid)
    if job['source']=='auto':
        session=s.session(job['session_id'])
        if replies_enabled():action="Reply YES if you're coming or NO if you can't."
        elif session['message_id']:action=f"RSVP in Discord: https://discord.com/channels/{s.get('guild_id')}/{s.get('channel_id')}/{session['message_id']}"
        else:action='Open Discord and RSVP.'
        opener='Second notice, and the whole class channel has been told.' if followup else 'Your Arabic class RSVP is overdue.'
        return fit_sms(opener,action,arabic,english)
    actor=s.one('SELECT name FROM members WHERE user_id=?',(job['actor'],))
    who=actor['name'] if actor else 'Someone'
    if replies_enabled():action='Reply HERE to check in.'
    else:action=f"Tap I'm here on your WUPHF card: https://discord.com/channels/{s.get('guild_id')}/{s.get('channel_id')}/{job['message_id']}"
    opener='Still hiding? The class channel is watching.' if followup else f'{who} is looking for you in Arabic class.'
    return fit_sms(opener,action,arabic,english)


def public_text(bot,job,followup):
    uid=job['user_id'];line=roast(job['id']+uid,bot.store,uid)
    if job['source']=='auto':
        head=f"📣 <@{uid}> still hasn’t RSVPed for Class #{job['session_id']}. Second text sent." if followup else f"📣 <@{uid}> ignored the RSVP for Class #{job['session_id']}, so the telephone department texted them."
    else:
        head=f"📣 <@{uid}> still hasn’t checked in. Second, ruder text sent."
    return f'{head}\n{line}'


async def announce(bot,text):
    """The public shaming is best-effort: a Discord failure must not disturb job state."""
    try:await bot.notify(text)
    except discord.HTTPException:pass


async def process(bot):
    async with bot.wuphf_lock:
        await _process(bot)


async def _process(bot):
    s=bot.store
    # A process crash during a provider request is ambiguous: never repeat it or advance to the follow-up.
    for job in s.rows("SELECT * FROM wuphf_jobs WHERE state IN ('waiting_sms','waiting_call') ORDER BY id"):
        uid=job['user_id']
        if job['source']=='manual' and s.clock()>=datetime.fromisoformat(job['created_at'])+timedelta(hours=24):
            with s.db:s.db.execute("UPDATE wuphf_jobs SET state='expired' WHERE id=?",(job['id'],))
            continue
        if not still_needed(s,job):
            with s.db:s.db.execute("UPDATE wuphf_jobs SET state='answered' WHERE id=?",(job['id'],))
            continue
        if not bot.wuphf.preferences(uid).get('sms'):
            with s.db:s.db.execute("UPDATE wuphf_jobs SET state='stopped' WHERE id=?",(job['id'],))
            continue
        followup=job['state']=='waiting_call'
        due=job['call_due'] if followup else job['sms_due']
        if not due or s.clock()<datetime.fromisoformat(due):continue
        try:bot.wuphf.check(uid,'sms')
        except RuleError as exc:
            if 'hiatus' in str(exc):
                with s.db:s.db.execute("UPDATE wuphf_jobs SET state='stopped' WHERE id=?",(job['id'],))
            continue # Quiet hours, cooldown, daily cap, or unconfigured provider: wait.
        sending='sending_call' if followup else 'sending_sms'
        with s.db:s.db.execute('UPDATE wuphf_jobs SET state=? WHERE id=?',(sending,job['id']))
        try:await bot.wuphf.send(bot.http_client,uid,'sms',sms_text(bot,job,followup))
        except Exception as exc:
            with s.db:
                s.db.execute("UPDATE wuphf_jobs SET state='uncertain',reason=? WHERE id=?",(type(exc).__name__,job['id']))
            await bot.notify_once(f"wuphf-error:{job['id']}",f"TA: WUPHF #{job['id']} could not be confirmed. It will not retry or escalate; check the phone provider before trying again.")
            continue
        sent=stamp(s.clock())
        with s.db:
            if not still_needed(s,job):
                s.db.execute("UPDATE wuphf_jobs SET state='answered' WHERE id=? AND state=?",(job['id'],sending))
                continue
            if followup:s.db.execute("UPDATE wuphf_jobs SET state='done',call_sent=? WHERE id=? AND state=?",(sent,job['id'],sending))
            else:s.db.execute("UPDATE wuphf_jobs SET state='waiting_call',sms_sent=?,call_due=? WHERE id=? AND state=?",(sent,stamp(s.clock()+timedelta(minutes=job['call_delay'])),job['id'],sending))
        # The manual card already named them in public; every other text gets its own post.
        if followup or job['source']=='auto':await announce(bot,public_text(bot,job,followup))
