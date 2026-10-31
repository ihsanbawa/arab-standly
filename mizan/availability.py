"""One public availability board; private, atomic per-member ballots."""
from datetime import datetime
import discord
from .core import RuleError, stamp
from .ui import Menu, label, safe, guard_context


def save_ballot(store,pid,uid,options,selected):
    """A saved ballot answers exactly the options the person actually saw."""
    with store.transaction():
        p=store.poll(pid)
        if p['options']!=options:
            raise RuleError('A new time was added while you were choosing. Reopen Update my availability to include it.')
        choices=set(selected)
        if not choices<=set(range(len(options))): raise RuleError('Unknown proposed time.')
        for n in range(len(options)):
            store.answer_poll(pid,uid,n,n in choices)
        if not choices:
            store.db.execute("INSERT INTO replacement_requests VALUES(?,?,?,NULL,'pending',?) ON CONFLICT(session_id,revision,user_id) DO UPDATE SET proposed_at=NULL,state='pending',updated_at=excluded.updated_at",(p['session_id'],p['revision'],uid,stamp(store.clock())))
    return choices


class SharedPollView(Menu):
    def __init__(self,bot,pid):
        super().__init__(bot,persistent=True)
        async def availability(i): await open_ballot(bot,i,pid,i.user.id)
        async def suggest(i):
            from .flow import suggest_menu
            p=bot.store.poll(pid)
            await suggest_menu(bot,i,p['session_id'],i.user.id,p['revision'])
        self.button('Update my availability',availability,style=discord.ButtonStyle.primary,custom_id=f'mizan:availability:{pid}')
        self.button('Suggest another time',suggest,custom_id=f'mizan:availability-suggest:{pid}')


async def open_ballot(bot,interaction,pid,uid):
    guard_context(bot,interaction)
    bot.check_guild(interaction)
    if uid!=interaction.user.id and not hasattr(bot,'demo_owner'):
        raise RuleError('You can only answer for yourself.')
    p=bot.store.poll(pid)
    bot.store.ensure_participant(p['session_id'],uid)
    if p['status']!='open' or bot.store.clock()>=datetime.fromisoformat(p['deadline']):
        raise RuleError('This round is closed. Open the current class card.')
    view=AvailabilityMenu(bot,pid,uid)
    name=next(m['name'] for m in bot.store.participants(p['session_id']) if m['user_id']==uid)
    content=f"**Availability for {safe(name)}**\nSelect **every** time you can attend, then save. Unselected times will be marked ❌. Nothing changes until you save."
    if interaction.response.is_done():
        await interaction.followup.send(content,view=view,ephemeral=True)
    else:
        await interaction.response.send_message(content,view=view,ephemeral=True)


class AvailabilityMenu(Menu):
    def __init__(self,bot,pid,uid):
        super().__init__(bot)
        p=bot.store.poll(pid)
        self.options=list(p['options'])
        self.selected={n for n in range(len(self.options)) if bot.store.poll_answer(pid,uid,n)=='yes'}
        select=discord.ui.Select(placeholder='Select every time you can attend',min_values=0,max_values=len(self.options),options=[
            discord.SelectOption(label=label(dt),value=str(n),default=n in self.selected) for n,dt in enumerate(self.options)])
        async def selection(i):
            self.selected={int(v) for v in select.values}
            for option in select.options: option.default=int(option.value) in self.selected
            await i.response.edit_message(view=self)
        select.callback=selection
        self.add_item(select)
        async def save(i): await self.submit(i,pid,uid,self.selected)
        async def none(i): await self.submit(i,pid,uid,set())
        self.button('Save availability',save,style=discord.ButtonStyle.success)
        self.button('None work for me',none)

    async def submit(self,interaction,pid,uid,selected):
        guard_context(self.bot,interaction)
        self.bot.check_guild(interaction)
        if uid!=interaction.user.id and not hasattr(self.bot,'demo_owner'): raise RuleError('Answer for yourself.')
        save_ballot(self.bot.store,pid,uid,self.options,selected)
        await interaction.response.defer(ephemeral=True)
        from .consensus import update
        await update(self.bot,pid)
        p=self.bot.store.poll(pid)
        text='✅ Saved. The shared card now shows your responses.'
        if p['status']=='confirmed': text='✅ All four agreed! Class moved automatically and all four are marked coming.'
        elif not selected: text='❌ Saved: none of these times work. Please suggest another time below.'
        view=None
        if hasattr(self.bot,'demo_owner'):
            from .testing import TestControls
            view=TestControls(self.bot.root,self.bot)
        await interaction.edit_original_response(content=text,embed=None,view=view)
        if not selected and p['status']!='confirmed':
            from .flow import suggest_menu
            await suggest_menu(self.bot,interaction,p['session_id'],uid,p['revision'])
