"""Move the configured class channel while keeping records and old history intact."""
import os
import discord


async def configure_channel(bot):
    target=os.getenv('CLASS_CHANNEL_ID')
    if not target or hasattr(bot,'demo_owner'): return
    target=int(target)
    if bot.store.get('channel_id')==target:return
    channel=bot.get_channel(target) or await bot.fetch_channel(target)
    gid=int(os.getenv('DISCORD_GUILD_ID') or bot.store.get('guild_id',0))
    if not isinstance(channel,discord.TextChannel) or channel.guild.id!=gid:
        raise ValueError('CLASS_CHANNEL_ID must be a text channel in the class server.')
    old=bot.store.get('channel_id')
    with bot.store.transaction():
        bot.store.db.execute("INSERT INTO settings VALUES('channel_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(target),))
        bot.store.db.execute("DELETE FROM settings WHERE key='panel_message_id' OR key LIKE 'public_mizan:%'")
        bot.store.db.execute("UPDATE sessions SET message_id=NULL WHERE status IN ('scheduled','needs_replacement')")
        bot.store.db.execute("UPDATE polls SET message_id=NULL WHERE status IN ('open','no_agreement')")
        bot.store.db.execute('DELETE FROM poll_cards')
        bot.store.log(0,'class_channel_moved',{'from':old,'to':target})
    # Recreate only live controls; old messages and score history stay intact.
    for s in bot.store.rows("SELECT id FROM sessions WHERE status IN ('scheduled','needs_replacement')"):
        await bot.update_card(s['id'],create=True)
    for p in bot.store.rows("SELECT id FROM polls WHERE status IN ('open','no_agreement')"):
        await bot.update_poll(p['id'],create=True)
