"""Private date/time selection and explicit, Dallas-local typed alternatives."""
import calendar
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord

from .core import RuleError, parse_local
from .ui import Form, Menu, label, send


def parse_suggestion(text, store):
    """ISO timestamps, today/tomorrow, weekdays, and named calendar dates."""
    text=text.strip()
    try:
        return parse_local(text,store.tz)
    except RuleError:
        # Preserve ISO errors, especially DST ambiguity.
        if re.match(r'^\d{4}-\d{2}-\d{2}[T ]',text):
            raise
    match=re.fullmatch(r'(.+?)\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?',text,re.I)
    if not match:
        raise RuleError('Try Friday at 4pm, tomorrow 3:30pm, Nov 27 2026 4pm, or YYYY-MM-DD HH:MM. All times are Dallas time.')
    day_text,hour,minute,ampm=match.groups()
    hour,minute=int(hour),int(minute or 0)
    if ampm:
        if not 1<=hour<=12: raise RuleError('Use an hour from 1 to 12 with AM or PM.')
        hour=hour%12+(12 if ampm.lower()=='pm' else 0)
    elif ':' not in match.group(0)[len(day_text):]:
        raise RuleError('Include AM or PM, such as 4pm, or use 24-hour time such as 16:00.')
    if hour>23 or minute>59: raise RuleError('Choose a valid hour and minute.')
    now=store.clock().astimezone(ZoneInfo(store.tz))
    day_text=day_text.lower().strip()
    if day_text in {'today','tomorrow'}:
        day=now.date()+timedelta(days=day_text=='tomorrow')
    else:
        weekdays={name.lower():n for n,name in enumerate(calendar.day_name)}
        weekdays.update({name.lower():n for n,name in enumerate(calendar.day_abbr)})
        weekday=day_text.removeprefix('next ')
        if weekday in weekdays:
            delta=(weekdays[weekday]-now.weekday())%7
            if day_text.startswith('next ') and delta==0: delta=7
            day=now.date()+timedelta(days=delta)
            if delta==0 and (hour,minute)<=(now.hour,now.minute): day+=timedelta(days=7)
        else:
            day=None
            for fmt in ['%Y-%m-%d','%b %d %Y','%B %d %Y','%b %d','%B %d']:
                try:
                    if '%Y' in fmt:
                        day=datetime.strptime(day_text.replace(',',''),fmt).date()
                    else:
                        for year in range(now.year,now.year+5):
                            try:
                                candidate=datetime.strptime(f'{day_text.replace(",", "")} {year}',fmt+' %Y').date()
                            except ValueError: continue
                            if candidate>=now.date():
                                day=candidate
                                break
                        if day is None: continue
                    break
                except ValueError: continue
            if day is None: raise RuleError('Choose a date with the picker, or type Friday 4pm / Nov 27 2026 4pm.')
    return parse_local(f'{day} {hour:02d}:{minute:02d}',store.tz)


async def pick_datetime(bot, interaction, chosen, *, initial=None, back=None, month=None, time_stage=False):
    """Nothing is published until the caller explicitly saves the resulting draft."""
    now=bot.store.clock().astimezone(ZoneInfo(bot.store.tz))
    initial=initial or (now+timedelta(days=1)).replace(hour=bot.store.get('class_hour',16),minute=0,second=0,microsecond=0)
    initial=initial.astimezone(ZoneInfo(bot.store.tz))
    month=month or initial.date().replace(day=1)
    view=Menu(bot)
    async def typed(i):
        async def submitted(j,values):
            dt=parse_suggestion(values[0],bot.store)
            if dt<=bot.store.clock()+timedelta(minutes=5): raise RuleError('Choose a time more than five minutes in the future.')
            await chosen(j,dt)
        await i.response.send_modal(Form(bot,'Type a date and time',[
            ('When · Dallas time','',True,'Friday 4pm / tomorrow 3:30pm / Nov 27 4pm')],submitted))
    if not time_stage:
        for first,last in [(1,16),(17,calendar.monthrange(month.year,month.month)[1])]:
            days=[month.replace(day=n) for n in range(first,last+1) if month.replace(day=n)>=now.date()]
            if days:
                async def day_picked(i,v):
                    day=datetime.fromisoformat(v).date()
                    dt=initial.replace(year=day.year,month=day.month,day=day.day)
                    await pick_datetime(bot,i,chosen,initial=dt,back=back,time_stage=True)
                view.select(f'Choose day · {first}–{last}',[
                    discord.SelectOption(label=d.strftime('%a %b %d'),value=d.isoformat()) for d in days],day_picked)
        async def previous(i):
            await pick_datetime(bot,i,chosen,initial=initial,back=back,month=(month-timedelta(days=1)).replace(day=1))
        async def following(i):
            await pick_datetime(bot,i,chosen,initial=initial,back=back,month=(month.replace(day=28)+timedelta(days=4)).replace(day=1))
        if month>now.date().replace(day=1): view.button('← Previous month',previous,row=2)
        view.button('Next month →',following,row=2)
        text=f'**Choose a date · {month:%B %Y}**\n```\n{calendar.TextCalendar().formatmonth(month.year,month.month).strip()}\n```\nPick a day below, then choose a time. Dallas time throughout.'
    else:
        async def hour_picked(i,v):
            await pick_datetime(bot,i,chosen,initial=initial.replace(hour=int(v)),back=back,time_stage=True)
        async def minute_picked(i,v):
            await pick_datetime(bot,i,chosen,initial=initial.replace(minute=int(v)),back=back,time_stage=True)
        view.select('Hour · AM / PM',[
            discord.SelectOption(label=f'{h%12 or 12} {"AM" if h<12 else "PM"}',value=str(h),default=h==initial.hour) for h in range(24)],hour_picked)
        minutes=sorted({0,15,30,45,initial.minute})
        view.select('Minute',[
            discord.SelectOption(label=f':{m:02d}',value=str(m),default=m==initial.minute) for m in minutes],minute_picked)
        for h in [15,16,17]:
            async def quick(i,hour=h):
                await pick_datetime(bot,i,chosen,initial=initial.replace(hour=hour,minute=0),back=back,time_stage=True)
            view.button(f'{h-12} PM',quick,row=2)
        async def save(i):
            dt=parse_local(initial.strftime('%Y-%m-%d %H:%M'),bot.store.tz)
            if dt<=bot.store.clock()+timedelta(minutes=5): raise RuleError('Choose a time more than five minutes in the future.')
            await chosen(i,dt)
        async def date_again(i):
            await pick_datetime(bot,i,chosen,initial=initial,back=back)
        view.button('Use this time',save,style=discord.ButtonStyle.success,row=3)
        view.button('Change date',date_again,row=3)
        text=f'**Choose a time**\n{label(initial)}\nUse the menus or a quick afternoon time, then tap **Use this time**. This only updates your draft.'
    view.button('Type instead',typed,row=4)
    if back: view.button('Back to suggestions',back,row=4)
    await send(interaction,text,view)
