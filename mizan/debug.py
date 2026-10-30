"""Keep rehearsals inside the owner's private Discord debug channel."""
import os
import discord
from .core import RuleError
from .ui import Menu


class DebugPanel(Menu):
    def __init__(self,bot):
        super().__init__(bot,persistent=True)
        async def start(i):
            from .testing import open_test
            await open_test(bot,i)
        self.button('Start weekly rehearsal',start,custom_id='standly:debug:start',style=discord.ButtonStyle.primary)


def check_channel(root,interaction):
    expected=root.store.get('debug_channel_id')
    if not expected:
        raise RuleError('Create a private #shaykh-debug channel with access for you and the bot; testing will be available there after startup.')
    if getattr(interaction,'channel_id',None)!=expected:
        raise RuleError(f'Test mode lives in <#{expected}>. Open its Start weekly rehearsal button. Class announcements stay in the member channel.')


async def ensure(bot):
    gid=int(os.getenv('DISCORD_GUILD_ID') or bot.store.get('guild_id',0))
    guild=bot.get_guild(gid)
    if not guild: return
    channel=next((c for c in guild.text_channels if c.name=='shaykh-debug'),None)
    if not channel or channel.permissions_for(guild.default_role).view_channel: return
    if channel.id==bot.store.get('channel_id'): return
    bot.store.set('debug_channel_id',channel.id)
    mid=bot.store.get('debug_panel_id')
    if mid:
        try:
            await (await channel.fetch_message(mid)).edit(content='🧪 **Shaykh Standly · Private testing**\nRun the whole week, play all four voters, test automatic rescheduling, streaks and danger alerts. No real class records, SMS/calls or financial writes.',view=DebugPanel(bot))
            return
        except discord.NotFound: pass
    msg=await channel.send('🧪 **Shaykh Standly · Private testing**\nRun the whole week, play all four voters, test automatic rescheduling, streaks and danger alerts. No real class records, SMS/calls or financial writes.',view=DebugPanel(bot))
    bot.store.set('debug_panel_id',msg.id)


def get_demo(root,interaction):
    root.require_ta(interaction)
    check_channel(root,interaction)
    from .testing import DemoBot
    demo=root.test_sessions.get(interaction.user.id)
    if demo is None or demo.demo_closed:
        demo=DemoBot(root,interaction.user.id,interaction.user.display_name)
        root.test_sessions[interaction.user.id]=demo
    demo.check_guild(interaction)
    return demo


def route_commands(bot):
    # Parsed command arguments are preserved; only the backing class store changes.
    from functools import wraps
    for command in bot.tree.get_commands():
        if command.name=='testmode': continue
        original=command.callback
        def wrap(callback,name):
            @wraps(callback)
            async def dispatch(interaction,*args,**kwargs):
                if bot.store.get('debug_channel_id') and getattr(interaction,'channel_id',None)==bot.store.get('debug_channel_id'):
                    demo=get_demo(bot,interaction)
                    return await demo.tree.get_command(name).callback(interaction,*args,**kwargs)
                return await callback(interaction,*args,**kwargs)
            return dispatch
        command._callback=wrap(original,command.name)
