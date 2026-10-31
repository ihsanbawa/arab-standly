"""One private Arabic welcome per member, with safe replay and DM-blocked fallback."""
import os
import discord


def welcome_embed():
    embed=discord.Embed(title='السَّلَامُ عَلَيْكُمْ! أَهْلًا وَسَهْلًا 👋',
        description='**As-salāmu ʿalaykum! Ahlan wa sahlan — welcome!**\nI’m **Shaykh Standly** 🦆, your Arabic class companion. I keep our four-person class organised, accountable, and occasionally roasted.',color=0xD5AB42)
    embed.add_field(name='📚 Where and when',value='In person at **IACC Plano**, Saturdays **after Fajr congregation**, for **60 minutes**. The class card shows the exact date and time. Our group aims to attend **at least two classes each calendar month**.',inline=False)
    embed.add_field(name='✅ Tell us if you can come',value='A class card appears in **#general** midweek. Tap **I can come** or **I can’t**. Even after confirming, use **I can’t** if your plans change.',inline=False)
    embed.add_field(name='📅 Need another time?',value='Select **all** your workable replacement times and save. Everyone can update availability on the shared card. Class moves automatically once **all four agree**.',inline=False)
    embed.add_field(name='⚖️ المِيزَان · The scales',value='The public report shows who’s leading this month, everyone’s scores and all-time standings. Attendance earns **+1**; a decline costs **−1**, or **−2 within 24 hours**. Unexcused absence costs **−2**. Deductions don’t stack for the same disruption. Emergencies can be excused by the TA. Monthly scores reset; lifetime scores and streak history stay.',inline=False)
    embed.add_field(name='📝 After class',value='The TA confirms attendance and adds what we covered and what comes next. That appears in the next invitation. Confirming an RSVP alone does not earn attendance points.',inline=False)
    embed.add_field(name='📣 Optional WUPHF nudges',value='**My nudges** lets you choose SMS, calls, and Fajr-hours reminders once configured. These are **off until you opt in**. You can switch them off at any time.',inline=False)
    embed.add_field(name='🦆 Your next step',value='Open **#general → Next class / RSVP**. Use **Al-Mizan** for the shared standings, **Practice links** for Quizlet, or **My nudges** for your preferences. The TA adds the four class members to the roster. Ask the TA if anything looks wrong.',inline=False)
    embed.set_footer(text='بِسْمِ اللهِ، لِنَبْدَأْ! · In the name of Allah, let’s begin! · Arabic roasts may contain useful vocabulary.')
    return embed


async def deliver(bot,member):
    if getattr(member,'bot',False): return False
    expected=int(os.getenv('DISCORD_GUILD_ID') or bot.store.get('guild_id',0))
    if member.guild.id!=expected: return False
    async with bot.welcome_lock:
        key=f'welcome:sent:{member.id}'
        if bot.store.get(key): return False
        view=discord.ui.View()
        cid=bot.store.get('channel_id')
        if cid: view.add_item(discord.ui.Button(label='Open Arabic Class',url=f'https://discord.com/channels/{expected}/{cid}'))
        try: await member.send(embed=welcome_embed(),view=view)
        except discord.Forbidden:
            bot.store.set(f'welcome:blocked:{member.id}',True)
            return False
        bot.store.set(key,True)
        return True


async def open_private(bot,interaction):
    bot.check_guild(interaction)
    await interaction.response.send_message(embed=welcome_embed(),ephemeral=True)
    bot.store.set(f'welcome:seen:{interaction.user.id}',True)
