"""Button-first Discord workflows. Commands remain optional shortcuts."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import discord

from .core import RuleError, month_of, parse_local, stamp
from .prayer import LOCATION, SOURCE, TIMETABLE, defaults, fajr_slot, upcoming


RSVP_LABELS = {'coming':'Coming', 'late':'Running late', 'unavailable':"Can't attend", 'change':'Requested another time'}


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


def label(dt):
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    return dt.astimezone(ZoneInfo('America/Chicago')).strftime('%a %b %d · %I:%M %p %Z')


class Menu(discord.ui.View):
    def __init__(self, bot, *, persistent=False):
        super().__init__(timeout=None if persistent else 900)
        self.bot = bot

    async def interaction_check(self, interaction):
        self.bot.check_guild(interaction)
        return True

    async def on_error(self, interaction, error, item):
        await self.bot.respond_error(interaction, error)

    def button(self, label, callback, *, style=discord.ButtonStyle.secondary, custom_id=None, row=None):
        b = discord.ui.Button(label=label, style=style, custom_id=custom_id, row=row)
        b.callback = callback
        self.add_item(b)
        return b

    def select(self, placeholder, options, callback):
        s = discord.ui.Select(placeholder=placeholder, options=options)
        async def selected(interaction):
            await callback(interaction, s.values[0])
        s.callback = selected
        self.add_item(s)
        return s


async def send(interaction, text, view=None):
    message = getattr(interaction, 'message', None)
    private_menu = bool(getattr(getattr(message, 'flags', None), 'ephemeral', False)) and not getattr(message, 'embeds', [])
    if private_menu:
        # Keep guided menus in place; never replace the public panel or a class card.
        if interaction.response.is_done():
            await interaction.edit_original_response(content=text, embed=None, view=view)
        else:
            await interaction.response.edit_message(content=text, embed=None, view=view)
    elif interaction.response.is_done():
        await interaction.followup.send(text, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(text, view=view, ephemeral=True)


class ConflictView(Menu):
    def __init__(self, bot, sid, revision):
        super().__init__(bot, persistent=True)
        async def keep(i):
            bot.require_ta(i)
            guarded(bot, sid, revision)
            if bot.store.session(sid)['status'] != 'scheduled':
                raise RuleError('This class is no longer scheduled. Open Class controls.')
            with bot.store.db:
                bot.store.log(i.user.id, 'original_time_kept', {'session': sid})
            await send(i, 'Original time kept. The rest can still meet; no penalty was assigned. You can review attendance after class.')
        async def alternatives(i):
            bot.require_ta(i)
            guarded(bot, sid, revision)
            await poll_preview(bot, i, sid)
        self.button('Keep original time', keep, custom_id=f'standly:keep:{sid}:{revision}')
        self.button('Find alternative times', alternatives, custom_id=f'standly:alternatives:{sid}:{revision}', style=discord.ButtonStyle.primary)


async def invoke(bot, interaction, name, **kwargs):
    # Reuse the command's permission checks, financial guards and audit trail.
    await bot.tree.get_command(name).callback(interaction, **kwargs)


def guarded(bot, sid, revision):
    if bot.store.session(sid)['revision'] != revision:
        raise RuleError('This class has changed. Open its controls again.')


class Panel(Menu):
    def __init__(self, bot):
        super().__init__(bot, persistent=True)
        for title, action in [('Next class / RSVP','nextclass'), ('Schedule class','schedule'),
                              ('Class controls','manage'), ('Al-Mizan','mizan'),
                              ('Practice links','resources'), ('My nudges','nudges'),
                              ('Class roster','member'), ('Class status','status'), ('TA settings','settings')]:
            async def clicked(i, a=action):
                await route(bot, i, a)
            self.button(title, clicked, custom_id=f'standly:home:{action}')
        if not hasattr(bot, 'demo_owner'):
            async def test(i): await route(bot, i, 'test')
            self.button('Test mode', test, custom_id='standly:home:test')


async def ensure_panel(bot):
    if bot.store.get('panel_message_id'):
        return
    channel = await bot.channel()
    if not channel:
        return
    msg = await channel.send('🦆 **Shaykh Standly · Arabic Class**\nTap a button below. Saturday after Fajr at IACC · 60 minutes.\nRSVPs, rescheduling, attendance and Al-Mizan all live here.', view=Panel(bot))
    bot.store.set('panel_message_id', msg.id)


class Form(discord.ui.Modal):
    def __init__(self, bot, title, fields, submit):
        super().__init__(title=title)
        self.bot, self.submit = bot, submit
        for name, value, required, placeholder in fields:
            self.add_item(discord.ui.TextInput(label=name, default=value or None,
                required=required, placeholder=placeholder, max_length=500,
                style=discord.TextStyle.short))

    async def on_submit(self, interaction):
        self.bot.check_guild(interaction)
        await self.submit(interaction, [str(x) for x in self.children])

    async def on_error(self, interaction, error):
        await self.bot.respond_error(interaction, error)


async def schedule_preview(bot, interaction, spec=None):
    bot.require_ta(interaction)
    if spec is None:
        try:
            spec = defaults(bot.store)
        except RuleError:
            async def custom(i, v):
                await schedule_preview(bot, i, {**defaults(bot.store, parse_local(v[0], bot.store.tz)), 'topic': v[1]})
            await interaction.response.send_modal(Form(bot, 'No verified IACC time loaded', [
                ('Verified start · Dallas time','',True,'YYYY-MM-DD HH:MM'),
                ('Lesson',bot.store.get('class_topic','Arabic class'),True,'')], custom))
            return
    start = spec['start']
    day = start.astimezone(ZoneInfo('America/Chicago')).date()
    iq = TIMETABLE['iqamah'].get(day.isoformat())
    text = (f"**Announce this class?**\n📅 {label(start)} · {spec['duration']} minutes\n"
            f"📍 {safe(spec['location'])}\n📖 {safe(spec['topic'])}\n{safe(spec['prep'])}\n")
    if iq:
        text += f"\n[IACC timetable]({SOURCE}): Fajr iqamah **{iq} AM**. Our default class start adds **{bot.store.get('fajr_offset',15)} minutes** for prayer; adjust if needed.\n"
    else:
        text += '\nCustom start time — verify with IACC.\n'
    text += f"\nRoster: **{len(bot.store.roster())}/4**. Invite everyone and use Class roster before announcing."
    view = Menu(bot)
    async def announce(i):
        bot.require_ta(i)
        if len(bot.store.roster()) != 4:
            raise RuleError('Select the four class members with Class roster first, so everyone receives this invitation.')
        await invoke(bot,i,'schedule',start=start.astimezone(ZoneInfo('America/Chicago')).isoformat(),
                     topic=spec['topic'], prep=spec['prep'], location=spec['location'], duration=spec['duration'])
    view.button('Announce & ask for RSVPs',announce,style=discord.ButtonStyle.success)
    async def edit(i):
        async def submitted(j, v):
            try:
                duration=int(v[4])
            except ValueError:
                raise RuleError('Duration must be a number of minutes.')
            updated=dict(start=parse_local(v[0],bot.store.tz),topic=v[1],prep=v[2],location=v[3],duration=duration)
            await schedule_preview(bot,j,updated)
        await i.response.send_modal(Form(bot,'Edit class (defaults filled)',[
            ('Start · Dallas time',start.astimezone(ZoneInfo('America/Chicago')).strftime('%Y-%m-%d %H:%M'),True,'YYYY-MM-DD HH:MM'),
            ('Lesson / topic',spec['topic'],True,''),('Preparation',spec['prep'],False,''),
            ('Location',spec['location'],True,''),('Minutes',str(spec['duration']),True,'60')],submitted))
    view.button('Edit details',edit)
    try:
        slots=upcoming(bot.store)
    except RuleError:
        slots=[]
    if slots:
        async def date_selected(i,v):
            await schedule_preview(bot,i,{**spec,'start':datetime.fromisoformat(v)})
        view.select('Choose another Saturday',[
            discord.SelectOption(label=label(d),value=stamp(d)) for d in slots],date_selected)
    await send(interaction,text,view)


async def choose_class(bot, interaction, action, sid=None):
    if action not in {'rsvp','report','nextclass'}:
        bot.require_ta(interaction)
    if sid is not None:
        return await class_action(bot,interaction,action,sid)
    if action in {'rsvp','report','nextclass','poll','cancel'}:
        rows=bot.store.rows("SELECT * FROM sessions WHERE status IN ('scheduled','needs_replacement') ORDER BY start_at DESC LIMIT 25")
    else:
        rows=bot.store.rows('SELECT * FROM sessions ORDER BY start_at DESC LIMIT 25')
    if not rows:
        view=Menu(bot)
        async def schedule(i): await route(bot,i,'schedule')
        view.button('Schedule class',schedule)
        await send(interaction,'No classes yet. Start with Schedule class.',view)
        return
    if len(rows)==1:
        return await class_action(bot,interaction,action,rows[0]['id'])
    view=Menu(bot)
    async def selected(i,v): await class_action(bot,i,action,int(v))
    view.select('Which class?',[
        discord.SelectOption(label=f"{label(s['start_at'])} · {s['status'].replace('_',' ')}"[:100],
            description=s['topic'][:100],value=str(s['id'])) for s in rows],selected)
    await send(interaction,'Choose a class — no class number needed.',view)


async def confirm(bot, interaction, text, callback, sid=None):
    view=Menu(bot)
    rev=bot.store.session(sid)['revision'] if sid is not None else None
    done=False
    async def yes(i):
        nonlocal done
        bot.require_ta(i)
        if sid is not None: guarded(bot,sid,rev)
        if done: raise RuleError('This action was already submitted. Open Status to check the result.')
        done=True
        try:
            await callback(i)
        except Exception:
            done=False
            raise
    view.button('Confirm',yes,style=discord.ButtonStyle.primary)
    async def no(i): await i.response.edit_message(content='No changes made.',view=None)
    view.button('Back / leave unchanged',no)
    await send(interaction,text,view)


async def choose_person(bot, interaction, sid, callback, *, title='Choose a person', optional=False):
    people=bot.store.participants(sid) if sid is not None else bot.store.roster()
    view=Menu(bot)
    options=[discord.SelectOption(label=p['name'][:100],value=str(p['user_id'])) for p in people[:24]]
    if optional: options.insert(0,discord.SelectOption(label='No individual penalty',value='none'))
    if not options: raise RuleError('Add the class roster first.')
    async def selected(i,v):
        bot.require_ta(i)
        if sid is not None and v!='none': bot.store.ensure_participant(sid,int(v))
        person=None if v=='none' else SimpleNamespace(id=int(v),display_name=next(p['name'] for p in people if p['user_id']==int(v)))
        await callback(i,person)
    view.select(title,options,selected)
    await send(interaction,title,view)


async def class_action(bot, interaction, action, sid):
    s=bot.store.session(sid)
    if action in {'rsvp','nextclass'}:
        from .bot import ClassView
        await interaction.response.send_message(embed=bot.card(sid),view=ClassView(bot,sid,s['revision']) if s['status']=='scheduled' else None,ephemeral=True)
        return
    if action=='report':
        from .bot import ReportModal
        bot.store.ensure_participant(sid,interaction.user.id)
        await interaction.response.send_modal(ReportModal(bot,sid))
        return
    bot.require_ta(interaction)
    rev=s['revision']
    if action=='manage':
        view=Menu(bot)
        for name,a in [('Reschedule / poll','poll'),('Attendance exception','attendance'),
                       ('Class happened','classheld'),("Class didn't happen",'cancel'),
                       ('Reopen confirmation','reopen'),('Waive penalty','waive'),('Confirm poll time','confirm_slot')]:
            async def clicked(i,action=a):
                guarded(bot,sid,rev)
                await class_action(bot,i,action,sid)
            view.button(name,clicked)
        await send(interaction,f"**Class controls · {label(s['start_at'])}**\n{safe(s['topic'])} · {s['status'].replace('_',' ')}",view)
    elif action=='poll':
        await poll_preview(bot,interaction,sid)
    elif action=='confirm_slot':
        await choose_poll(bot,interaction,sid=sid)
    elif action=='attendance':
        async def person_selected(i,p):
            view=Menu(bot)
            async def state(j,v):
                guarded(bot,sid,rev)
                async def apply(k): await invoke(bot,k,'attendance',class_id=sid,person=p,status=v,reason='Recorded through class controls')
                await confirm(bot,j,f"Record **{safe(p.display_name)}: {v}** for {label(s['start_at'])}?\nUnexcused absence: −2 and an Arabic roast. Present earns +1 only once class is confirmed held; excused/partial earn 0.",apply,sid)
            view.select('Attendance status',[discord.SelectOption(label=l,value=v) for l,v in [('Present','present'),('Absent · unexcused (−2)','absent'),('Excused (0)','excused'),('Partial (0)','partial')]],state)
            await send(i,f'Attendance for **{safe(p.display_name)}**',view)
        await choose_person(bot,interaction,sid,person_selected)
    elif action=='cancel':
        async def person_selected(i,p):
            async def apply(j): await invoke(bot,j,'cancel',class_id=sid,reason='Class did not happen; replacement needed',responsible=p)
            await confirm(bot,i,f"Mark **{label(s['start_at'])}** as not held and needing a replacement?\n"+ (f"**{safe(p.display_name)} receives −2 and an Arabic roast.**" if p else 'No individual penalty will be assigned.'),apply,sid)
        await choose_person(bot,interaction,sid,person_selected,title='Who is responsible? Choose no penalty for an excuse or group decision.',optional=True)
    elif action in {'classheld','reopen'}:
        async def apply(i): await invoke(bot,i,action,class_id=sid)
        text = ('Confirm this class happened? Accepted RSVPs count as present; record any attendance exceptions first. This counts toward the group’s two classes this month.' if action=='classheld' else 'Reopen this class? Its held-class and attendance credit will be removed. Financially audited months remain locked.')
        await confirm(bot,interaction,f"**{label(s['start_at'])}**\n{text}",apply,sid)
    elif action=='waive':
        async def person_selected(i,p):
            async def submitted(j,v):
                guarded(bot,sid,rev)
                await invoke(bot,j,'waive',class_id=sid,person=p,reason=v[0])
            await i.response.send_modal(Form(bot,'Why waive the penalty?', [('Reason','',True,'Emergency / mistaken record / other reason')],submitted))
        await choose_person(bot,interaction,sid,person_selected)


async def poll_preview(bot,interaction,sid,slots=None):
    bot.require_ta(interaction)
    revision=bot.store.session(sid)['revision']
    if slots is None:
        # Two future weekends, plus a Sunday option when available in the source.
        sats=upcoming(bot.store,2)
        slots=list(sats)
        sunday=sats[0].astimezone(ZoneInfo('America/Chicago')).date()+timedelta(days=1)
        try: slots.append(fajr_slot(sunday,bot.store.get('fajr_offset',15)))
        except RuleError: pass
        slots=sorted(set(slots))
    if len(slots)<2: raise RuleError('Choose at least two custom future times; the verified calendar ends this year.')
    view=Menu(bot)
    async def post(i):
        bot.require_ta(i)
        guarded(bot,sid,revision)
        due=min(bot.store.clock()+timedelta(hours=24),min(slots)-timedelta(hours=1))
        if due <= bot.store.clock(): raise RuleError('These times are too soon. Choose later alternatives.')
        await invoke(bot,i,'poll',class_id=sid,options=';'.join(stamp(d) for d in slots),deadline=stamp(due))
    view.button('Post availability poll',post,style=discord.ButtonStyle.success)
    async def edit(i):
        async def submitted(j,v):
            await poll_preview(bot,j,sid,[parse_local(x,bot.store.tz) for x in v if x.strip()])
        values=[d.astimezone(ZoneInfo('America/Chicago')).strftime('%Y-%m-%d %H:%M') for d in slots]
        await i.response.send_modal(Form(bot,'Alternative times · Dallas',[(f'Option {n+1}',values[n] if n<len(values) else '',n<2,'YYYY-MM-DD HH:MM') for n in range(3)],submitted))
    view.button('Edit alternatives',edit)
    await send(interaction,'**Proposed replacement times**\n'+'\n'.join(f'• {label(d)}' for d in slots)+'\n\nPoll runs up to 24 hours, ending at least an hour before the earliest option. Original class stays unchanged until you confirm a replacement.',view)


async def choose_poll(bot,interaction,sid=None,pid=None):
    bot.require_ta(interaction)
    if pid is None:
        rows=bot.store.rows("SELECT * FROM polls WHERE status='open'"+(' AND session_id=?' if sid else '')+' ORDER BY id DESC LIMIT 25',(sid,) if sid else ())
        if not rows: raise RuleError('No open replacement poll. Choose Reschedule / poll in Class controls.')
        if len(rows)>1:
            view=Menu(bot)
            async def selected(i,v): await choose_poll(bot,i,pid=int(v))
            view.select('Which replacement poll?',[discord.SelectOption(label=f"Class {label(bot.store.session(p['session_id'])['start_at'])}",value=str(p['id'])) for p in rows],selected)
            return await send(interaction,'Choose a replacement poll.',view)
        pid=rows[0]['id']
    p=bot.store.poll(pid)
    sid=p['session_id']
    view=Menu(bot)
    async def slot_selected(i,v):
        idx=int(v)
        available={r['user_id'] for r in bot.store.rows('SELECT user_id FROM votes WHERE poll_id=? AND slot=?',(pid,idx))}
        conflicts=[m for m in bot.store.participants(sid) if m['user_id'] not in available]
        async def person_selected(j,person):
            async def apply(k): await invoke(bot,k,'confirm_slot',poll_id=pid,option=idx+1,responsible=person,accept_conflicts=bool(conflicts))
            text=f"Move class to **{label(p['options'][idx])}**? Everyone must RSVP again.\n"
            text+=('**Not marked available:** '+', '.join(safe(m['name']) for m in conflicts)+'. Confirming overrides these availability conflicts.\n') if conflicts else 'Everyone marked this option available.\n'
            text+=(f'Assign the accepted-commitment penalty to **{safe(person.display_name)}** (−1 or −2 for last minute, if they previously accepted).' if person else 'No individual penalty assigned.')
            await confirm(bot,j,text,apply,sid)
        await choose_person(bot,i,sid,person_selected,title='Responsibility for moving class',optional=True)
    view.select('Choose the new time',[discord.SelectOption(label=label(d),value=str(n)) for n,d in enumerate(p['options'])],slot_selected)
    await send(interaction,'Choose a time, then review availability and any Al-Mizan penalty before confirming.',view)


async def roster_menu(bot, interaction, active=True):
    bot.require_ta(interaction)
    view=Menu(bot)
    select=discord.ui.UserSelect(placeholder='Select class members',min_values=1,max_values=4)
    async def selected(i):
        bot.require_ta(i)
        people=list(select.values)
        if any(p.bot for p in people): raise RuleError('Choose people, not bots.')
        if active:
            ids={p['user_id'] for p in bot.store.roster()} | {p.id for p in people}
            if len(ids)>4: raise RuleError('This is a four-person class. Put inactive members on hiatus first.')
        for person in people:
            if getattr(getattr(person, 'guild', None), 'id', None) != i.guild_id:
                raise RuleError('Invite these people to Arabic Class first, then select them here.')
        for person in people: bot.store.member(person.id,person.display_name,active)
        await send(i,('Added to future classes: ' if active else 'On hiatus for future classes: ')+', '.join(safe(p.display_name) for p in people)+'. Existing class rosters stay unchanged.')
    select.callback=selected
    view.add_item(select)
    async def toggle(i): await roster_menu(bot,i,not active)
    view.button('Put members on hiatus' if active else 'Add / reactivate members',toggle)
    async def me(i):
        bot.require_ta(i)
        if len(bot.store.roster())>=4 and not any(p['user_id']==i.user.id for p in bot.store.roster()):
            raise RuleError('The four-person roster is full.')
        bot.store.member(i.user.id,i.user.display_name)
        await send(i,'You are on the roster. Invite the other three, then select them here.')
    view.button('Add me',me)
    await send(interaction,'**Class roster**\n'+('\n'.join(safe(p['name']) for p in bot.store.roster()) or 'No members yet.')+'\nChoose up to four people at once. Weekly invitations begin automatically when all four are on the roster.',view)


async def nudge_preferences(bot,interaction):
    prefs=bot.store.get(f'nudges:{interaction.user.id}',{'sms':False,'calls':False,'early':False})
    view=Menu(bot)
    for key,title in [('sms','SMS'),('calls','Phone calls'),('early','Fajr / quiet hours')]:
        async def toggle(i,k=key):
            current=bot.store.get(f'nudges:{i.user.id}',{'sms':False,'calls':False,'early':False})
            current[k]=not current.get(k,False)
            bot.wuphf.consent(i.user.id,current.get('sms',False),current.get('calls',False),current.get('early',False))
            await nudge_preferences(bot,i)
        view.button(f"{title}: {'ON' if prefs.get(key) else 'OFF'}",toggle)
    async def stop(i):
        bot.wuphf.consent(i.user.id,False,False,False)
        await nudge_preferences(bot,i)
    view.button('Turn all off',stop,style=discord.ButtonStyle.danger)
    await send(interaction,'**Your private nudge preferences**\nTap to toggle. Quiet hours are 9 PM–8 AM unless you explicitly allow Fajr-time nudges. A number must be configured privately; never post it here.',view)


async def wuphf_menu(bot,interaction):
    bot.require_ta(interaction)
    async def selected(i,p):
        view=Menu(bot)
        for title,method in [('Send SMS','sms'),('Place phone call','calls')]:
            async def clicked(j,m=method):
                async def apply(k): await invoke(bot,k,'wuphf',person=p,method=m)
                await confirm(bot,j,f"Send a {'text message' if m=='sms' else 'phone call'} to **{safe(p.display_name)}** about the next class? Their opt-in, quiet hours and frequency limits still apply.",apply)
            view.button(title,clicked)
        await send(i,'Choose how to nudge '+safe(p.display_name)+'.',view)
    await choose_person(bot,interaction,None,selected,title='Who needs a nudge?')


async def settings_menu(bot,interaction):
    bot.require_ta(interaction)
    view=Menu(bot)
    async def weekly(i):
        bot.require_ta(i)
        bot.store.set('weekly_invites',not bot.store.get('weekly_invites',True))
        await settings_menu(bot,i)
    view.button('Pause weekly invites' if bot.store.get('weekly_invites',True) else 'Enable weekly invites',weekly)
    async def offset(i,v):
        bot.require_ta(i)
        bot.store.set('fajr_offset',int(v))
        await send(i,f'Future new classes will start {v} minutes after IACC Fajr iqamah. Existing invitations keep their announced time.')
    view.select('Time after iqamah',[discord.SelectOption(label=f'{v} minutes after Fajr iqamah',value=str(v),default=v==bot.store.get('fajr_offset',15)) for v in [10,15,20,30]],offset)
    for title,action in [('Status','status'),('Send WUPHF','wuphf'),('Beeminder checks','beeminder'),('Export records','export'),('Use this channel','setup')]:
        async def clicked(i,a=action): await route(bot,i,a)
        view.button(title,clicked)
    async def lesson(i):
        async def submitted(j,v):
            bot.require_ta(j)
            bot.store.set('class_topic',v[0]);bot.store.set('class_prep',v[1])
            await send(j,'Saved for future new invitations. Existing class cards keep their current lesson.')
        await i.response.send_modal(Form(bot,'Default lesson / preparation',[
            ('Current lesson',bot.store.get('class_topic','Arabic class · continue our current lesson'),True,''),
            ('Preparation',bot.store.get('class_prep','Bring all three books.'),False,'')],submitted))
    view.button('Default lesson / prep',lesson)
    await send(interaction,f"**Class settings**\nWeekly invitations: {'ON (waiting for four roster members)' if bot.store.get('weekly_invites',True) and len(bot.store.roster())!=4 else 'ON' if bot.store.get('weekly_invites',True) else 'paused'}.\nSaturday · IACC · 60 minutes · iqamah + {bot.store.get('fajr_offset',15)} minutes.\n[Official calendar]({SOURCE}) loaded for Oct–Dec 2026; later dates require a verified timetable or custom time. Existing invitations never silently change.",view)


async def route(bot,interaction,action,**kwargs):
    bot.check_guild(interaction)
    if action=='test':
        from .testing import open_test
        return await open_test(bot,interaction)
    if action=='schedule': return await schedule_preview(bot,interaction)
    if action in {'rsvp','nextclass','report','manage','poll','cancel','attendance','classheld','reopen','waive'}:
        return await choose_class(bot,interaction,action,kwargs.get('class_id'))
    if action=='confirm_slot': return await choose_poll(bot,interaction,pid=kwargs.get('poll_id'))
    if action=='member': return await roster_menu(bot,interaction)
    if action=='nudges': return await nudge_preferences(bot,interaction)
    if action=='wuphf': return await wuphf_menu(bot,interaction)
    if action=='settings': return await settings_menu(bot,interaction)
    if action in {'help','standly'}: return await send(interaction,'**Shaykh Standly**\nUse the buttons. You can also open any command without filling in arguments.',Panel(bot))
    if action=='mizan':
        view=Menu(bot)
        now=bot.store.clock().astimezone(ZoneInfo(bot.store.tz))
        months=[]
        for _ in range(12):
            months.append(now.strftime('%Y-%m'))
            now=now.replace(day=1)-timedelta(days=1)
        async def selected(i,v): await invoke(bot,i,'mizan',month=v)
        view.select('Choose month',[discord.SelectOption(label=m,value=m) for m in months],selected)
        board=bot.store.standings(months[0])
        await send(interaction,'⚖️ **المِيزَان · '+months[0]+'**\n'+('\n'.join(f"{safe(p['name'])}: **{p['points']:+d}**" for p in board) or 'Add your four class members first.')+f'\nGroup classes held: **{bot.store.monthly_count(months[0])}/2**',view)
        return
    return await invoke(bot,interaction,action)
