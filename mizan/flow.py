"""Two-button RSVP, required replacement suggestions, and weekly lesson handoff."""
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import discord
from .core import RuleError,stamp,parse_local
from .prayer import upcoming,fajr_slot
from .ui import Menu,Form,label,safe,send,guard_context
from .content import roast


class Invitation(discord.ui.View):
    def __init__(self,bot,sid,revision):
        super().__init__(timeout=None)
        self.bot=bot
        for text,coming in [('I can come',True),("I can't",False)]:
            button=discord.ui.Button(label=text,style=discord.ButtonStyle.success if coming else discord.ButtonStyle.secondary,
                custom_id=f"mizan:rsvp:{sid}:{revision}:{'coming' if coming else 'unavailable'}")
            async def answer(i,value=coming):
                bot.check_guild(i)
                await respond(bot,i,sid,i.user.id,value,revision)
            button.callback=answer
            self.add_item(button)
    async def on_error(self,i,error,item): await self.bot.respond_error(i,error)


async def respond(bot,i,sid,uid,coming,revision):
    guard_context(bot,i)
    bot.check_guild(i)
    penalty=bot.store.answer(sid,uid,coming,revision)
    await i.response.defer()
    await bot.update_card(sid)
    if coming:
        if hasattr(bot,'demo_owner'):
            from .testing import simulated_rsvp
            return await simulated_rsvp(bot,i,sid,uid,'Saved: I can come.')
        await send(i,'✅ You’re coming. See you after Fajr at IACC.')
    else:
        await suggest_menu(bot,i,sid,uid,revision)
        await bot.notify_once(f'decline:{sid}:{revision}:{uid}:{penalty["points"]}',
            f"<@{uid}> can't make class. **Al-Mizan {penalty['points']}**. They need to suggest another time. The time moves automatically only when all four agree.\n"+roast(sid+uid))


async def suggest_menu(bot,i,sid,uid,revision):
    guard_context(bot,i)
    bot.check_guild(i)
    if uid!=i.user.id and not hasattr(bot,'demo_owner'): raise RuleError('Suggest a time for yourself.')
    s=bot.store.session(sid)
    response=bot.store.one('SELECT response FROM rsvps WHERE session_id=? AND revision=? AND user_id=?',(sid,revision,uid))
    rescheduling=bot.store.one("SELECT 1 FROM polls WHERE session_id=? AND revision=? AND status IN ('open','no_agreement')",(sid,revision))
    if (not response or response['response'] not in {'unavailable','change'}) and not rescheduling and s['status']!='needs_replacement':
        raise RuleError("Choose I can't on the invitation first.")
    if s['revision']!=revision: raise RuleError('This invitation changed. Open the current class.')
    options=[]
    poll=bot.store.one("SELECT id FROM polls WHERE session_id=? AND revision=? AND status='open' AND deadline>? ORDER BY id DESC LIMIT 1",(sid,revision,stamp(bot.store.clock())))
    if poll: options.extend(datetime.fromisoformat(x) for x in bot.store.poll(poll['id'])['options'])
    try:
        sats=upcoming(bot.store,3)
        options+=sats
        options.append(fajr_slot(sats[0].astimezone(ZoneInfo('America/Chicago')).date()+timedelta(days=1),bot.store.get('fajr_offset',15)))
    except RuleError: pass
    options=sorted({x for x in options if x>bot.store.clock()+timedelta(minutes=5) and stamp(x)!=s['start_at']})[:20]
    view=ReplacementTimes(bot,sid,uid,revision,options)
    penalty=bot.store.one('SELECT points FROM penalties WHERE session_id=? AND user_id=?',(sid,uid))
    await send(i,f"**You can’t make this class. When can you come instead?**\nAl-Mizan: **{penalty['points'] if penalty else 0}** (early −1; within 24 hours −2). A TA can excuse emergencies.\nSelect every time you can attend (up to three), then tap **Suggest selected times**. We’ll ask the group about them together. Closing this menu leaves your suggestion pending.",view)


class ReplacementTimes(Menu):
    def __init__(self,bot,sid,uid,revision,options):
        super().__init__(bot)
        self.selected=[]
        if options:
            select=discord.ui.Select(placeholder='When CAN you come?',min_values=1,max_values=min(3,len(options)),
                options=[discord.SelectOption(label=label(x),value=stamp(x)) for x in options])
            async def picked(i):
                self.selected=[datetime.fromisoformat(v) for v in select.values]
                for option in select.options: option.default=option.value in select.values
                # Selecting changes only this private menu; save publishes the whole batch.
                await i.response.edit_message(view=self)
            select.callback=picked
            self.add_item(select)
            async def save(i): await submit_suggestions(bot,i,sid,uid,revision,self.selected)
            self.button('Suggest selected times',save,style=discord.ButtonStyle.success)
        async def custom(i):
            async def submitted(j,values):
                times=self.selected+[parse_local(v,bot.store.tz) for v in values if v.strip()]
                await submit_suggestions(bot,j,sid,uid,revision,times)
            await i.response.send_modal(Form(bot,'Other replacement times · Dallas',[
                ('Time 1','',True,'YYYY-MM-DD HH:MM'),('Time 2 (optional)','',False,'YYYY-MM-DD HH:MM'),('Time 3 (optional)','',False,'YYYY-MM-DD HH:MM')],submitted))
        self.button('Other dates / times',custom)


async def submit_suggestion(bot,i,sid,uid,revision,dt):
    return await submit_suggestions(bot,i,sid,uid,revision,[dt])


async def submit_suggestions(bot,i,sid,uid,revision,times):
    guard_context(bot,i)
    bot.check_guild(i)
    if uid!=i.user.id and not hasattr(bot,'demo_owner'): raise RuleError('Suggest times for yourself.')
    times=list(dict.fromkeys(times))
    pid=bot.store.suggest_many(sid,uid,times,revision)
    await i.response.defer()
    await bot.update_card(sid)
    from .consensus import update
    await update(bot,pid)
    view=None
    if hasattr(bot,'demo_owner'):
        from .testing import simulated_votes,TestControls
        view=Menu(bot)
        async def again(j): await simulated_votes(bot,j)
        async def home(j): await send(j,'🧪 Continue the class-week rehearsal.',TestControls(bot.root,bot))
        view.button('Answer as another member',again)
        view.button('Test home',home)
    await send(i,'📅 Suggested:\n'+'\n'.join('• **'+label(dt)+'**' for dt in times)+'\nThe shared card shows every proposed time. Your availability is saved for these times; everyone else can select all that work. Class moves automatically when all four agree.',view)


class SuggestionReminder(Menu):
    def __init__(self,bot,sid,revision):
        super().__init__(bot,persistent=True)
        async def suggest(i):
            bot.check_guild(i)
            await suggest_menu(bot,i,sid,i.user.id,revision)
        self.button('Suggest my replacement time',suggest,custom_id=f'standly:suggest:{sid}:{revision}')


class Recap(Menu):
    def __init__(self,bot,sid,revision):
        super().__init__(bot,persistent=True)
        async def write(i):
            bot.require_ta(i)
            s=bot.store.session(sid)
            if s['revision']!=revision: raise RuleError('This class changed. Open Class controls.')
            if bot.store.clock()<datetime.fromisoformat(s['confirmation_due']): raise RuleError('Add the recap after class ends.')
            previous=bot.store.one('SELECT * FROM lesson_notes WHERE session_id=?',(sid,)) or {}
            async def submitted(j,v):
                bot.require_ta(j)
                if bot.store.session(sid)['revision']!=revision: raise RuleError('This class changed. Reopen the recap.')
                # Retain a draft if attendance still needs an exception, so typing is not lost.
                bot.store.set(f'recap_draft:{sid}',v)
                try:
                    bot.store.save_lesson(sid,j.user.id,*v)
                except RuleError as exc:
                    return await send(j, '**Your recap draft is saved.**\n'+str(exc)+'\nUse Attendance exception below, then reopen the recap; your text will be filled in.',Recap(bot,sid,revision))
                await j.response.defer()
                await bot.update_card(sid)
                for future in bot.store.rows("SELECT * FROM sessions WHERE status='scheduled' AND start_at>?",(s['start_at'],)):
                    latest=bot.store.latest_lesson(datetime.fromisoformat(future['start_at']))
                    if latest and latest['session_id']==sid:
                        with bot.store.db:
                            bot.store.db.execute('UPDATE sessions SET topic=?,prep=? WHERE id=?',(v[1],v[2],future['id']))
                        await bot.update_card(future['id'])
                await send(j,'✅ Attendance and recap saved. Only all-four attendance counts toward 2/month. Next week’s invitation will include this recap and next lesson.')
            draft=bot.store.get(f'recap_draft:{sid}',[])
            fields=[('What did we cover?',previous.get('recap') or (draft[0] if draft else ''),True,'Chapter / pages / what you finished'),
                    ('What are we doing next?',previous.get('next_lesson') or (draft[1] if draft else s['topic']),True,'Next lesson / chapter / pages'),
                    ('Preparation for next class',previous.get('prep') or (draft[2] if draft else s['prep']),False,'Homework / vocab / bring your books')]
            await i.response.send_modal(Form(bot,'Class recap → next week',fields,submitted))
        self.button('Class happened · add recap',write,custom_id=f'standly:recap:{sid}:{revision}',style=discord.ButtonStyle.success)
        async def exceptions(i):
            from .ui import class_action
            await class_action(bot,i,'attendance',sid)
        self.button('Attendance exception',exceptions,custom_id=f'standly:recap-attendance:{sid}:{revision}')
        async def missed(i):
            from .ui import class_action
            await class_action(bot,i,'cancel',sid)
        self.button("Class didn't happen",missed,custom_id=f'standly:recap-missed:{sid}:{revision}')
