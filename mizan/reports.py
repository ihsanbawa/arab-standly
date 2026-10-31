"""Readable monthly Al-Mizan balances backed by the actual points ledger."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import discord
from . import progress
from .core import month_of, month_bounds
from .ui import Menu, safe, label


def report_data(store,month):
    start,end=month_bounds(month,store.tz)
    members=[]
    for row in store.standings(month):
        uid=row['user_id']
        entries=store.rows('SELECT * FROM ledger WHERE user_id=? AND month=? ORDER BY updated_at DESC,event_key',(uid,month))
        earned=sum(max(e['points'],0) for e in entries)
        lost=-sum(min(e['points'],0) for e in entries)
        attended=store.one("SELECT COUNT(*) n FROM attendance a JOIN sessions s ON s.id=a.session_id WHERE a.user_id=? AND a.status='present' AND s.status='held' AND s.start_at>=? AND s.start_at<?",(uid,start,end))['n']
        members.append(dict(row,earned=earned,lost=lost,entries=entries,attended=attended))
    return dict(month=month,members=members,count=store.monthly_count(month),end=datetime.fromisoformat(end))


def report_embed(store,month,uid=None,page=0):
    data=report_data(store,month)
    current=month_of(store.clock(),store.tz)
    embed=discord.Embed(title=f'⚖️ المِيزَان · Al-Mizan · {month}',color=0xD5AB42)
    if uid is None:
        embed.description='Monthly balances · choose a member below to see exactly what earned or lost points.'
        for member in data['members'][:20]:
            rank=1+sum(m['points']>member['points'] for m in data['members'])
            medal={1:'🥇',2:'🥈',3:'🥉'}.get(rank,'⚖️')
            embed.add_field(name=f"{medal} {safe(member['name'][:80])} · {member['points']:+d} points",
                value=f"🟢 Earned **+{member['earned']}** · 🔴 Lost **−{member['lost']}**\n📚 Attended **{member['attended']}** class(es) this month"+(f"\n🔥 Attendance streak: {progress.personal(store,member['user_id'])['current']} · best {progress.personal(store,member['user_id'])['best']}" if month==current else ''),inline=False)
        if not data['members']: embed.add_field(name='No members yet',value='Add your class roster to start tracking points.')
        embed.add_field(name='📚 Group commitment',value=f"**{data['count']}/2** classes attended by all four"+('\n'+progress.summary(store) if month==current else '\nMonthly target met ✅' if data['count']>=2 else '\nMonthly target missed ❌' if month<current else '\nUpcoming month'),inline=False)
        if month==current:
            streak=progress.group_streak(store)
            embed.add_field(name='🔥 Group streak',value=f"{streak['current']} successful months · best {streak['best']}",inline=False)
    else:
        member=next(m for m in data['members'] if m['user_id']==uid)
        embed.description=f"**{safe(member['name'])} · {member['points']:+d} points**\nEarned +{member['earned']} · Lost −{member['lost']} · Attended {member['attended']} class(es)"
        pages=max(1,(len(member['entries'])+7)//8)
        page=max(0,min(page,pages-1))
        for entry in member['entries'][page*8:(page+1)*8]:
            session=store.one('SELECT start_at FROM sessions WHERE id=?',(entry['session_id'],)) if entry['session_id'] else None
            date=label(session['start_at']) if session else label(entry['updated_at'])
            reason={'present':'Class attended','absent':'Recorded absent','excused':'Excused absence','partial':'Partial attendance'}.get(entry['reason'],entry['reason'])
            if reason=="Can't attend scheduled class": reason='Last-minute class decline' if entry['points']==-2 else 'Class decline'
            embed.add_field(name=f"{'🟢' if entry['points']>=0 else '🔴'} {entry['points']:+d} · {safe(reason[:140])}",value=f"{date}"+(f" · Class #{entry['session_id']}" if session else ''),inline=False)
        if not member['entries']: embed.add_field(name='No point changes this month',value='Balance starts at zero. Points appear after class attendance or a scheduling disruption.')
        embed.add_field(name='Point history',value=f'Page {page+1}/{pages} · {len(member["entries"])} recorded changes. Corrections and waivers are reflected in this balance.',inline=False)
    reset=f"Next fresh start: {label(data['end'])}. History preserved." if month==current else 'Each month starts at zero; earlier balances stay in history.'
    embed.add_field(name='How the scales work',value='Attendance **+1** · decline/confirmed disruption **−1** · last-minute decline/no-show **−2** · excused absence **0**. Strongest disruption deduction per person/class; no stacking. Replacement availability votes **0**.',inline=False)
    embed.set_footer(text=reset+' Al-Mizan points are separate from Beeminder; no money is charged by this report.')
    return embed


class ReportView(Menu):
    def __init__(self,bot,month,uid=None,page=0):
        super().__init__(bot)
        data=report_data(bot.store,month)
        now=bot.store.clock().astimezone(ZoneInfo(bot.store.tz))
        months=[]
        for _ in range(12):
            months.append(now.strftime('%Y-%m'))
            now=now.replace(day=1)-timedelta(days=1)
        months=list(dict.fromkeys([month]+months))[:25]
        async def choose_month(i,v): await show_report(bot,i,v,details=True)
        self.select('Choose month',[discord.SelectOption(label=m,value=m,default=m==month) for m in months],choose_month)
        if data['members']:
            async def choose_person(i,v): await show_report(bot,i,month,int(v))
            self.select('See a member’s point history',[discord.SelectOption(label=m['name'][:100],value=str(m['user_id']),default=m['user_id']==uid) for m in data['members'][:25]],choose_person)
        if uid is not None:
            member=next(m for m in data['members'] if m['user_id']==uid)
            async def back(i): await show_report(bot,i,month,details=True)
            self.button('Monthly overview',back)
            if page>0:
                async def previous(i): await show_report(bot,i,month,uid,page-1)
                self.button('Previous changes',previous)
            if (page+1)*8<len(member['entries']):
                async def more(i): await show_report(bot,i,month,uid,page+1)
                self.button('More changes',more)


async def show_report(bot,interaction,month=None,uid=None,page=0,details=False):
    bot.check_guild(interaction)
    month=month or month_of(bot.store.clock(),bot.store.tz)
    if uid is None and not details and not hasattr(bot,'demo_owner'):
        if not interaction.response.is_done():await interaction.response.defer(ephemeral=True)
        state=await public_board(bot,month,force=True)
        url=f"https://discord.com/channels/{interaction.guild_id}/{state['channel']}/{state['message']}"
        await interaction.followup.send(f'⚖️ [Open the shared Al-Mizan standings]({url})',ephemeral=True)
        return
    embed=report_embed(bot.store,month,uid,page)
    view=ReportView(bot,month,uid,page)
    kwargs=dict(content=None,embed=embed,view=view)
    message=getattr(interaction,'message',None)
    if message and getattr(message.flags,'ephemeral',False):
        if interaction.response.is_done(): await interaction.edit_original_response(**kwargs)
        else: await interaction.response.edit_message(**kwargs)
    elif interaction.response.is_done(): await interaction.followup.send(**kwargs,ephemeral=True)
    else: await interaction.response.send_message(**kwargs,ephemeral=True)


def standings_embed(store,month,period='month'):
    data=report_data(store,month)
    totals={r['user_id']:r['points'] for r in store.rows('SELECT user_id,SUM(points) points FROM ledger GROUP BY user_id')}
    members=[dict(m,total=totals.get(m['user_id'],0)) for m in data['members']]
    key='total' if period=='all' else 'points'
    members.sort(key=lambda m:(-m[key],m['name']))
    current=month_of(store.clock(),store.tz)
    embed=discord.Embed(title='⚖️ المِيزَان · AL-MIZAN',description=f'**{month}** · The scales are watching.\n'+('📜 All-time standings' if period=='all' else '🏆 Monthly standings'),color=0xD5AB42)
    if members:
        leaders=[m for m in members if m[key]==members[0][key]]
        if len(leaders)==len(members) and members[0][key]==0:
            headline='⚖️ A level playing field'
            lead='Everyone starts at zero. The crown is waiting.'
        else:
            headline='👑 All-time leader' if period=='all' else '👑 Month winner' if month<current else '👑 Leading this month'
            lead=' & '.join(safe(m['name']) for m in leaders)+f" · **{members[0][key]:+d} points**"
            if len(leaders)>1:lead+=' · tied'
        embed.add_field(name=headline,value=lead,inline=False)
    for member in members[:20]:
        rank=1+sum(m[key]>member[key] for m in members)
        medal={1:'🥇',2:'🥈',3:'🥉'}.get(rank,f'{rank}.')
        embed.add_field(name=f"{medal} {safe(member['name'][:80])}",value=f"**This month {member['points']:+d}** · All-time **{member['total']:+d}**\nEarned +{member['earned']} · Lost {('-'+str(member['lost'])) if member['lost'] else '0'} · 🔥 {progress.personal(store,member['user_id'])['current']}",inline=False)
    if not members:embed.add_field(name='The scales are ready',value='The TA can add the four class members with Class roster.')
    embed.add_field(name='📚 Together',value=f"**{data['count']}/2** full-group classes this month · {'Target met ✅' if data['count']>=2 else 'Let’s get everyone in the room.'}",inline=False)
    embed.set_footer(text='Monthly scores reset; all-time scores and streak history stay. · Attendance +1 · Early decline −1 · Last-minute/no-show −2')
    return embed


class PublicReportView(Menu):
    def __init__(self,bot,month):
        super().__init__(bot,persistent=True)
        for title,period in [('🏆 This month','month'),('📜 All-time','all')]:
            async def choose(i,value=period):
                await i.response.defer()
                await public_board(bot,month,value,force=True)
            self.button(title,choose,custom_id=f'standly:mizan-public:{month}:{period}')
        async def receipts(i):
            await show_report(bot,i,month,details=True)
        self.button('Point history',receipts,custom_id=f'standly:mizan-public:{month}:history')


async def public_board(bot,month=None,period=None,force=False):
    month=month or month_of(bot.store.clock(),bot.store.tz)
    async with bot.report_lock:
        key=f'public_mizan:{month}'
        state=bot.store.get(key,{})
        period=period or state.get('period','month')
        embed=standings_embed(bot.store,month,period)
        import json
        signature=json.dumps(embed.to_dict(),sort_keys=True)
        if not force and state.get('signature')==signature and state.get('channel')==bot.store.get('channel_id'):return state
        channel=await bot.channel()
        if not channel:return {}
        view=PublicReportView(bot,month)
        message=None
        if state.get('message') and state.get('channel')==channel.id:
            try:
                message=await channel.fetch_message(state['message'])
                await message.edit(embed=embed,view=view)
            except discord.NotFound:message=None
        if message is None:message=await channel.send(embed=embed,view=view)
        state={'message':message.id,'channel':channel.id,'period':period,'signature':signature}
        bot.store.set(key,state)
        return state
