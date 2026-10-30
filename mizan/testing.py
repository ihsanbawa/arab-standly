"""Per-TA, in-memory sandbox. No real records, phone requests or financial writes."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import discord

from .bot import MizanBot
from .core import RuleError, Store, month_of
from .prayer import ensure_weekly
from .ui import ConflictView, Menu, Panel, RSVP_LABELS, label, safe, send
from .wuphf import Wuphf


class DemoWuphf(Wuphf):
    def phone(self, uid):
        # Override only the provider configuration, retaining consent/quiet-hour/rate rules.
        return '+15555550101'

    async def send(self, client, uid, mode, message):
        self.reserve(uid, mode, message)
        with self.store.db:
            self.store.log(uid, 'test_nudge', {'method':mode,'message':message})
        return f'TEST ONLY: simulated {mode}. No text or call was sent. Consent, quiet hours and cooldown checks ran normally.'


class DemoChannel:
    def __init__(self, bot):
        self.bot, self.messages = bot, {}

    async def send(self, content=None, **kwargs):
        content='🧪 **TEST ONLY — isolated class**\n'+(content or '')
        if kwargs.get('view') is None:
            kwargs.pop('view', None)
        kwargs['allowed_mentions']=discord.AllowedMentions.none()
        i=self.bot.current_interaction
        if not i.response.is_done():
            await i.response.defer(ephemeral=True)
        msg=await i.followup.send(content,ephemeral=True,wait=True,**kwargs)
        self.messages[msg.id]=msg
        return msg

    async def fetch_message(self, mid):
        # Ephemeral webhook messages expire; recreate the preview instead of using real channels.
        if mid not in self.messages:
            raise RuleError('That test preview expired. Reset Test mode to start again.')
        return self.messages[mid]


class DemoBot(MizanBot):
    def __init__(self, root, owner, name):
        self.root, self.demo_owner, self.current_interaction = root, owner, None
        self.demo_closed=False
        self.test_clock=root.store.clock()
        store=Store(':memory:',clock=lambda:self.test_clock)
        store.set('guild_id',root.store.get('guild_id'))
        store.set('channel_id',-1)
        store.set('panel_message_id',-1)
        store.set('weekly_invites',False)
        store.member(owner,name+' (test)')
        for uid in (2,3,4): store.member(uid,f'Test member {uid}')
        super().__init__(store)
        self.wuphf=DemoWuphf(store)
        self.bee=SimpleNamespace(configured=lambda:False,bindings=[],start_month='9999-01')
        self.demo_channel=DemoChannel(self)

    def check_guild(self, interaction):
        if self.demo_closed: raise RuleError('This test was reset or the bot restarted. Open Test mode again.')
        self.root.require_ta(interaction)
        from .debug import check_channel
        check_channel(self.root,interaction)
        if interaction.user.id != self.demo_owner:
            raise RuleError('This test session belongs to another TA. Open your own Test mode.')
        self.current_interaction=interaction

    def get_user(self, uid):
        async def dm(text):
            return await self.demo_channel.send(f'Simulated private nudge to test member {uid}: '+text)
        return SimpleNamespace(id=uid, send=dm)

    async def fetch_user(self, uid):
        return self.get_user(uid)

    async def channel(self):
        return self.demo_channel

    async def notify(self,text,view=None):
        return await self.demo_channel.send(text,view=view)

    def card(self,sid):
        embed=super().card(sid)
        embed.title='🧪 TEST ONLY · '+embed.title
        return embed

    async def respond_error(self, interaction, error):
        error = getattr(error, 'original', error)
        message = str(error) if isinstance(error, RuleError) else 'The test action did not finish. The test clock or records may have changed; use Test home to inspect or reset this rehearsal.'
        await send(interaction, '🧪 **Test mode**\n' + message, TestControls(self.root, self))

    async def close(self):
        if self.demo_closed: return
        self.demo_closed=True
        await super().close()
        self.store.close()


class AdvancedControls(Menu):
    def __init__(self,root,demo):
        super().__init__(demo)
        async def controls(i):
            await send(i,'🧪 **TEST ONLY**\nThese are the same class controls, using four simulated members and separate records. No real announcements, messages, calls or Beeminder writes.',Panel(demo))
        self.button('Open test class controls',controls,style=discord.ButtonStyle.primary)
        async def rsvp(i): await simulated_rsvp(demo,i)
        self.button('Play the other members',rsvp)
        async def votes(i): await simulated_votes(demo,i)
        self.button('Play poll votes',votes)
        async def clock(i): await clock_menu(demo,i)
        self.button('Move test clock',clock)
        async def weekly(i):
            demo.check_guild(i)
            demo.store.set('weekly_invites',True)
            try: sid=ensure_weekly(demo.store)
            finally: demo.store.set('weekly_invites',False)
            await i.response.defer(ephemeral=True)
            await demo.update_card(sid,create=True)
            await i.followup.send('TEST: weekly invite simulated. Click the RSVP buttons as yourself, or use Play the other members.',ephemeral=True)
        self.button('Simulate weekly invitation',weekly)
        async def finance(i):
            demo.check_guild(i)
            month=month_of(demo.store.clock(),demo.store.tz)
            count=demo.store.monthly_count(month)
            if demo.store.unsettled(month):
                return await send(i, '🧪 Mock audit blocked: confirm whether all scheduled classes happened before calculating the group result. No financial write was attempted.')
            await send(i,f'🧪 **Mock monthly group result · {month}**\n{count}/2 held classes.\n'+('All four would receive a success result (0 monthly failures).' if count>=2 else 'All four would receive the same missed-target result (1 monthly failure).')+'\nStarting pledge $5; cap $90 is the agreed plan. This preview sends nothing to Beeminder, creates no goals and makes no charges.')
        self.button('Preview group commitment',finance)
        async def reset(i):
            await demo.close()
            root.test_sessions.pop(i.user.id,None)
            await open_test(root,i)
        self.button('Reset test session',reset,style=discord.ButtonStyle.danger)
        async def progress(i): await progress_tests(root,demo,i)
        self.button('Streaks & danger',progress)


class TestControls(Menu):
    def __init__(self,root,demo):
        super().__init__(demo)
        async def midweek(i):
            from .prayer import upcoming
            from zoneinfo import ZoneInfo
            demo.check_guild(i)
            saturday=upcoming(demo.store,1)[0].astimezone(ZoneInfo('America/Chicago')).date()
            demo.test_clock=datetime.combine(saturday-timedelta(days=3),datetime.min.time(),ZoneInfo('America/Chicago'))+timedelta(hours=18)
            demo.store.set('weekly_invites',True)
            await i.response.defer()
            try: await demo.tick()
            finally: demo.store.set('weekly_invites',False)
            await send(i,'🧪 **Wednesday, 6 PM: RSVP check-in sent.**\nNow answer as each member. The class invitation has only I can come / I can’t.',TestControls(root,demo))
        self.button('1 · Run midweek RSVP',midweek,style=discord.ButtonStyle.primary)
        async def members(i): await simulated_rsvp(demo,i)
        self.button('2 · Answer as members',members)
        async def vote(i): await simulated_votes(demo,i)
        self.button('3 · Vote as all four',vote)
        async def after(i):
            demo.check_guild(i)
            rows=demo.store.rows("SELECT * FROM sessions WHERE status='scheduled' ORDER BY start_at LIMIT 1")
            if not rows: raise RuleError('Run the midweek RSVP first.')
            demo.test_clock=datetime.fromisoformat(rows[0]['confirmation_due'])+timedelta(minutes=1)
            await i.response.defer()
            await demo.tick()
            await send(i,'🧪 **Class has ended.** The bot has prompted the TA for a recap. Open that prompt, record any attendance exceptions, then say what you covered and what is next.',TestControls(root,demo))
        self.button('4 · Jump after class',after)
        async def next_week(i):
            from zoneinfo import ZoneInfo
            demo.check_guild(i)
            done=demo.store.one("SELECT s.* FROM sessions s JOIN lesson_notes n ON n.session_id=s.id WHERE s.status='held' ORDER BY s.start_at DESC LIMIT 1")
            if not done: raise RuleError('Add the after-class recap first, so next week has a lesson to carry forward.')
            finished=datetime.fromisoformat(done['start_at']).astimezone(ZoneInfo('America/Chicago')).date()
            day=finished+timedelta(days=((5-finished.weekday())%7 or 7))
            demo.test_clock=datetime.combine(day-timedelta(days=3),datetime.min.time(),ZoneInfo('America/Chicago'))+timedelta(hours=18)
            demo.store.set('weekly_invites',True)
            await i.response.defer()
            try: await demo.tick()
            finally: demo.store.set('weekly_invites',False)
            await send(i,'🧪 **Next Wednesday: new invitation sent.** Check its Where we left off, next lesson, and preparation fields.',TestControls(root,demo))
        self.button('5 · Next week’s announcement',next_week)
        async def tools(i): await send(i,'🧪 Optional test tools',AdvancedControls(root,demo))
        self.button('Streaks & danger tests',tools)
        async def reset(i):
            await demo.close();root.test_sessions.pop(i.user.id,None)
            await open_test(root,i)
        self.button('Reset test session',reset,style=discord.ButtonStyle.danger)


async def simulated_rsvp(demo, interaction, sid=None, uid=None, notice=''):
    demo.check_guild(interaction)
    sessions=demo.store.rows("SELECT * FROM sessions WHERE status='scheduled' ORDER BY start_at")
    if not sessions: raise RuleError('In test class controls, schedule a test class first.')
    if sid is None and len(sessions)>1:
        view=Menu(demo)
        async def selected(i,v): await simulated_rsvp(demo,i,int(v))
        view.select('Which test class?',[discord.SelectOption(label=label(s['start_at']),value=str(s['id'])) for s in sessions[:25]],selected)
        return await send(interaction,'🧪 Choose the class to rehearse.',view)
    sid=sid or sessions[0]['id']
    revision=demo.store.session(sid)['revision']
    people=demo.store.participants(sid)
    uid=uid or people[0]['user_id']
    person=next(p for p in people if p['user_id']==uid)
    responses={r['user_id']:r['response'] for r in demo.store.responses(sid)}
    view=Menu(demo)
    async def selected(i,v): await simulated_rsvp(demo,i,sid,int(v))
    view.select('Act as a member',[discord.SelectOption(label=p['name'][:100],value=str(p['user_id']),default=p['user_id']==uid) for p in people],selected)
    for coming,title in [(True,'I can come'),(False,"I can't")]:
        async def answer(i,value=coming):
            from .flow import respond
            await respond(demo,i,sid,uid,value,revision)
        view.button(title,answer,style=discord.ButtonStyle.success if coming else discord.ButtonStyle.secondary)
    conflicts=[p for p in people if responses.get(p['user_id']) in {'unavailable','change'}]
    text=f"🧪 **Rehearse RSVPs · {label(demo.store.session(sid)['start_at'])}**\nActing as **{safe(person['name'])}**. Pick a member and tap their response.\n\n"
    text+='\n'.join(f"{safe(p['name'])}: **{RSVP_LABELS.get(responses.get(p['user_id']),'Not answered')}**" for p in people)
    if notice: text+='\n\n'+notice
    if conflicts:
        text+='\n\n**Scheduling conflict:** '+', '.join(safe(p['name']) for p in conflicts)+'.\nAnyone who cannot attend must propose another time. Early −1; within 24 hours −2, with no stacked penalties.'
    async def home(i): await open_test(demo.root,i)
    view.button('Test home',home)
    await send(interaction,text,view)


async def simulated_votes(demo,interaction):
    demo.check_guild(interaction)
    polls=demo.store.rows("SELECT * FROM polls WHERE status='open' ORDER BY id DESC LIMIT 25")
    if not polls: raise RuleError('Open a replacement poll in test class controls first.')
    view=Menu(demo)
    async def poll_selected(i,v):
        p=demo.store.poll(int(v))
        people=Menu(demo)
        async def person_selected(j,uid):
            options=Menu(demo)
            async def slot_selected(k,slot):
                demo.check_guild(k)
                demo.store.vote(p['id'],int(uid),int(slot))
                await k.response.defer()
                from .consensus import update
                await update(demo,p['id'])
                if demo.store.poll(p['id'])['status']=='confirmed':
                    return await send(k,'✅ All four agreed. The class moved automatically and the card shows 4/4 coming.',TestControls(demo.root,demo))
                if demo.store.poll(p['id'])['status']=='no_agreement':
                    return await offer_new_round(demo,k,p['session_id'],int(uid),p['revision'])
                await send(k,'🧪 Availability updated. Choose another option to toggle it, or return to choose another member.',options)
            async def another(k): await simulated_votes(demo,k)
            options.button('Choose another member',another)
            async def none(k):
                from .consensus import update
                demo.check_guild(k)
                current=demo.store.poll(p['id'])
                if current['status']!='open' or demo.store.clock()>=datetime.fromisoformat(current['deadline']): raise RuleError('This round is closed.')
                with demo.store.db:
                    demo.store.db.execute('DELETE FROM votes WHERE poll_id=? AND user_id=?',(p['id'],int(uid)))
                    demo.store.db.execute('INSERT INTO votes VALUES(?,?,-1)',(p['id'],int(uid)))
                await k.response.defer()
                await update(demo,p['id'])
                if demo.store.poll(p['id'])['status']=='no_agreement':
                    return await offer_new_round(demo,k,p['session_id'],int(uid),p['revision'])
                await send(k,'🧪 None work recorded. Vote as another member or offer a different time.',options)
            options.button('None work',none)
            options.select('Mark an option available',[discord.SelectOption(label=label(d),value=str(n)) for n,d in enumerate(p['options'])],slot_selected)
            await send(j,'🧪 Toggle every option this member can attend.',options)
        people.select('Play which member?',[discord.SelectOption(label=m['name'][:100],value=str(m['user_id'])) for m in demo.store.participants(p['session_id'])],person_selected)
        await send(i,'🧪 Choose a simulated voter.',people)
    view.select('Which test poll?',[discord.SelectOption(label=f"Poll {p['id']} · class {p['session_id']}",value=str(p['id'])) for p in polls],poll_selected)
    await send(interaction,'🧪 Vote as each simulated class member.',view)


async def clock_menu(demo,interaction):
    demo.check_guild(interaction)
    sessions=demo.store.rows("SELECT * FROM sessions WHERE status='scheduled' ORDER BY start_at")
    if not sessions: raise RuleError('Schedule a test class first.')
    view=Menu(demo)
    async def selected(i,v):
        s=demo.store.session(int(v))
        times=Menu(demo)
        start=datetime.fromisoformat(s['start_at'])
        for title,dt in [('3 days before',start-timedelta(days=3)),('1 hour before',start-timedelta(hours=1)),('After class ends',datetime.fromisoformat(s['confirmation_due'])+timedelta(minutes=1))]:
            async def moved(j,target=dt):
                demo.check_guild(j)
                demo.test_clock=target
                await j.response.defer(ephemeral=True)
                await demo.tick()
                await send(j, '🧪 **Test clock: '+label(target)+'**\nUse test class controls to record exceptions and confirm whether it happened. Real time and records are unchanged.', TestControls(demo.root, demo))
            times.button(title,moved)
        await send(i,'🧪 Choose a test time. Rewinding is allowed only here, to test early and last-minute changes.',times)
    view.select('Choose the test class',[discord.SelectOption(label=label(s['start_at']),value=str(s['id'])) for s in sessions[:25]],selected)
    await send(interaction,'🧪 Simulate reminders, last-minute changes or the post-class confirmation.',view)


async def open_test(root,interaction):
    from .debug import get_demo
    demo=get_demo(root,interaction)
    await send(interaction,'🧪 **Rehearse a whole class week**\nFour simulated members; only you see this.\n\n**1.** Wednesday RSVP nudge.\n**2.** Answer I can come / I can’t for each person. Declines need a replacement time and deduct Al-Mizan.\n**3.** Vote as all four: the time locks automatically.\n**4.** After class, confirm attendance and recap.\n**5.** Preview the next announcement, streaks and monthly danger status.\n\nReal members, records, messages and charges are untouched. Test sessions reset on deployment.',TestControls(root,demo))


async def progress_tests(root,demo,i):
    from . import progress
    demo.check_guild(i)
    view=Menu(demo)
    async def end_month(j):
        import calendar
        from zoneinfo import ZoneInfo
        demo.check_guild(j)
        d=demo.store.clock().astimezone(ZoneInfo(demo.store.tz))
        demo.test_clock=d.replace(day=calendar.monthrange(d.year,d.month)[1],hour=12,minute=0)
        await j.response.defer()
        await demo.tick()
        await send(j,'🧪 End-of-month check:\n'+progress.summary(demo.store),TestControls(root,demo))
    view.button('Jump to month-end danger',end_month)
    async def board(j): await demo.tree.get_command('mizan').callback(j)
    view.button('Show streaks and Al-Mizan',board)
    async def example(j):
        from .core import parse_local
        demo.check_guild(j)
        owner=demo.demo_owner;name=j.user.display_name
        await demo.close()
        fresh=DemoBot(root,owner,name);root.test_sessions[owner]=fresh;fresh.check_guild(j)
        for start in ['2026-10-03 07:00','2026-10-10 07:00','2026-11-07 06:15','2026-11-14 06:15']:
            dt=parse_local(start,fresh.store.tz);fresh.test_clock=dt-timedelta(days=3)
            sid=fresh.store.create_class(dt,60,'IACC Plano','Test streak example',owner)
            for member in fresh.store.roster(): fresh.store.answer(sid,member['user_id'],True,0)
            fresh.test_clock=dt+timedelta(minutes=61);fresh.store.held(sid,owner)
        fresh.test_clock=parse_local('2026-11-15 12:00',fresh.store.tz)
        await send(j,'🧪 Sample records created for October and November: four fully attended classes.\n🔥 Each person: 4-class streak. Group: 2 successful months.\n'+progress.summary(fresh.store),TestControls(root,fresh))
    view.button('Reset + show a 2-month streak',example)
    async def home(j): await open_test(root,j)
    view.button('Test home',home)
    await send(i,'🧪 **Streaks & danger**\nPersonal attendance streaks survive month changes. An unexcused no-show breaks one; an excused absence pauses it. Group streaks track consecutive months with 2 full-group classes.\n'+progress.summary(demo.store),view)


async def offer_new_round(demo,i,sid,uid,revision):
    from .flow import suggest_menu
    view=Menu(demo)
    async def suggest(j): await suggest_menu(demo,j,sid,uid,revision)
    view.button('Suggest a new time as this member',suggest,style=discord.ButtonStyle.primary)
    async def home(j): await open_test(demo.root,j)
    view.button('Test home',home)
    await send(i,'🧪 No time worked for all four. This round is closed; a fresh suggestion starts the next vote automatically. No extra points were deducted for rejecting a poll option.',view)
