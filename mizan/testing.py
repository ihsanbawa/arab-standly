"""Per-TA, in-memory sandbox. No real records, phone requests or financial writes."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import discord

from .bot import MizanBot
from .core import RuleError, Store, month_of
from .prayer import ensure_weekly
from .ui import Menu, Panel, label, send
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

    async def notify(self,text):
        return await self.demo_channel.send(text)

    def card(self,sid):
        embed=super().card(sid)
        embed.title='🧪 TEST ONLY · '+embed.title
        return embed

    async def close(self):
        if self.demo_closed: return
        self.demo_closed=True
        await super().close()
        self.store.close()


class TestControls(Menu):
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


async def simulated_rsvp(demo,interaction):
    demo.check_guild(interaction)
    sessions=demo.store.rows("SELECT * FROM sessions WHERE status='scheduled' ORDER BY start_at")
    if not sessions: raise RuleError('In test class controls, schedule a test class first.')
    view=Menu(demo)
    async def class_selected(i,value):
        sid=int(value)
        revision=demo.store.session(sid)['revision']
        people=Menu(demo)
        async def person_selected(j,uid):
            options=Menu(demo)
            async def response_selected(k,response):
                demo.check_guild(k)
                demo.store.rsvp(sid,int(uid),response,revision=revision)
                await k.response.defer(ephemeral=True)
                await demo.update_card(sid)
                await k.followup.send(f'TEST: saved {response} for the simulated member. Real people were not contacted.',ephemeral=True)
            options.select('Simulate their response',[discord.SelectOption(label=l,value=v) for l,v in [('Coming','coming'),('Running late','late'),("Can't attend",'unavailable'),('Request another time','change')]],response_selected)
            await send(j,'🧪 Choose a simulated response.',options)
        people.select('Play which member?',[discord.SelectOption(label=p['name'][:100],value=str(p['user_id'])) for p in demo.store.participants(sid)],person_selected)
        await send(i,'🧪 Choose a simulated class member.',people)
    view.select('Which test class?',[discord.SelectOption(label=label(s['start_at']),value=str(s['id'])) for s in sessions[:25]],class_selected)
    await send(interaction,'🧪 You can answer for each of the four members without needing their accounts.',view)


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
                await k.response.defer(ephemeral=True)
                await demo.update_poll(p['id'])
                await k.followup.send('TEST: toggled the simulated member’s availability. Select again to toggle it back.',ephemeral=True)
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
                await j.followup.send('🧪 Test clock is now '+label(target)+'. Use class controls to record exceptions and confirm whether it happened. Real time and records are unchanged.',ephemeral=True)
            times.button(title,moved)
        await send(i,'🧪 Choose a test time. Rewinding is allowed only here, to test early and last-minute changes.',times)
    view.select('Choose the test class',[discord.SelectOption(label=label(s['start_at']),value=str(s['id'])) for s in sessions[:25]],selected)
    await send(interaction,'🧪 Simulate reminders, last-minute changes or the post-class confirmation.',view)


async def open_test(root,interaction):
    root.require_ta(interaction)
    demo=root.test_sessions.get(interaction.user.id)
    if demo is None or demo.demo_closed:
        demo=DemoBot(root,interaction.user.id,interaction.user.display_name)
        root.test_sessions[interaction.user.id]=demo
    demo.check_guild(interaction)
    await send(interaction,'🧪 **Shaykh Standly · Test mode**\nOnly you see these test messages. Four simulated members are ready. Test records are separate and disappear on reset or deployment.\n\n1. Simulate a weekly invitation or open test controls and schedule one.\n2. Play the other members to try different RSVPs.\n3. Move the test clock, test rescheduling, attendance and Al-Mizan.\n\nSMS/calls are mocked and financial writes are disabled here. Return to the normal server panel for real class actions.',TestControls(root,demo))
