from __future__ import annotations

import io
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import aiohttp
import discord
from aiohttp import web
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

from .beeminder import Beeminder
from .content import NUDGE, RESOURCES, roast
from .core import RuleError, Store, month_of, parse_local, stamp
from .wuphf import Wuphf
from . import ui, consensus, progress
from .prayer import SOURCE, TIMETABLE, ensure_weekly

log = logging.getLogger("mizan")


def safe(text):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(text)))


def when(iso):
    return f"<t:{int(datetime.fromisoformat(iso).timestamp())}:F>"


from .flow import Invitation as ClassView, Recap, SuggestionReminder


class ReportModal(discord.ui.Modal, title="Report a class problem"):
    reason = discord.ui.TextInput(
        label="What's changed?",
        placeholder="I can't make it / someone is missing / class did not happen",
        max_length=500,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, bot, sid):
        super().__init__()
        self.bot, self.sid = bot, sid

    async def on_submit(self, interaction):
        self.bot.check_guild(interaction)
        self.bot.store.disruption(self.sid, interaction.user.id, str(self.reason))
        await interaction.response.send_message(
            "Reported. The TA will decide whether the rest still meet or class needs moving. No automatic penalty.",
            ephemeral=True,
        )
        await self.bot.notify(
            f"⚠️ Class #{self.sid}: <@{interaction.user.id}> reports: {safe(self.reason)}\nTA: **Still meeting?** Keep the class, or use /cancel then /poll. Assign responsibility only after checking what happened."
        )

    async def on_error(self, interaction, error):
        await self.bot.respond_error(interaction, error)


class PollView(discord.ui.View):
    """Each proposed time has the same two explicit responses as a class RSVP."""
    def __init__(self, bot, pid, slot):
        super().__init__(timeout=None)
        self.bot=bot
        for available,title,style in [(True,'I can come',discord.ButtonStyle.success),(False,"I can't",discord.ButtonStyle.danger)]:
            button=discord.ui.Button(label=title,style=style,custom_id=f'mizan:reschedule:{pid}:{slot}:{int(available)}')
            async def answer(interaction, yes=available):
                ui.guard_context(bot,interaction)
                bot.check_guild(interaction)
                await interaction.response.defer(ephemeral=True)
                bot.store.answer_poll(pid,interaction.user.id,slot,yes)
                await consensus.update(bot,pid)
                if yes:
                    await interaction.followup.send('✅ Coming to this proposed time. Your response is visible on its card.',ephemeral=True)
                else:
                    from .flow import suggest_menu
                    p=bot.store.poll(pid)
                    await suggest_menu(bot,interaction,p['session_id'],interaction.user.id,p['revision'])
            button.callback=answer
            self.add_item(button)

    async def on_error(self, interaction, error, item):
        await self.bot.respond_error(interaction,error)


class MizanBot(discord.Client):
    def __init__(self, store):
        super().__init__(
            intents=discord.Intents.default(),
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=True, replied_user=False
            ),
        )
        self.store = store
        self.tree = app_commands.CommandTree(self)
        self.http_client = None
        self.bee = Beeminder(store)
        self.wuphf = Wuphf(store)
        self.last_tick = None
        self.tick_errors = 0
        self.runner = None
        self.test_sessions = {}
        self.move_lock = asyncio.Lock()
        self.poll_render_lock = asyncio.Lock()
        register_commands(self)
        if not hasattr(self,'demo_owner'):
            from .debug import route_commands
            route_commands(self)

    def check_guild(self, interaction):
        expected = int(os.environ.get("DISCORD_GUILD_ID") or self.store.get("guild_id", 0))
        if not interaction.guild_id or not expected or interaction.guild_id != expected:
            raise RuleError("Use this bot in its configured class server.")

    def require_ta(self, interaction):
        self.check_guild(interaction)
        owner = int(os.environ.get("BOT_OWNER_ID", "0"))
        role = self.store.get("ta_role_id")
        if (
            interaction.user.id == owner
            or interaction.permissions.manage_guild
            or (role and any(r.id == role for r in getattr(interaction.user, "roles", [])))
        ):
            return
        raise RuleError("Only a TA or server manager can change class records.")

    async def respond_error(self, interaction, error):
        error = getattr(error, "original", error)
        message = (
            str(error)
            if isinstance(error, RuleError)
            else "Something failed. Your action may have been saved; use /status before retrying."
        )
        if not isinstance(error, RuleError):
            log.error("Interaction failed: %s", type(error).__name__)
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    async def channel(self):
        cid = self.store.get("channel_id")
        return self.get_channel(cid) or await self.fetch_channel(cid) if cid else None

    async def notify(self, text, view=None):
        channel = await self.channel()
        if channel:
            return await channel.send(text[:1950], view=view)

    async def notify_once(self, key, text, view=None):
        if self.store.notice_sent(key):
            return
        message = await self.notify(text, view=view) if view is not None else await self.notify(text)
        if message:
            self.store.mark_notice(key)

    def card(self, sid):
        s = self.store.session(sid)
        month = month_of(datetime.fromisoformat(s["start_at"]), self.store.tz)
        embed = discord.Embed(
            title=f"📚 Shaykh Standly · Class #{sid} · {s['status'].replace('_', ' ').title()}",
            description=f"{when(s['start_at'])}\n📍 {safe(s['location'])}\n**{safe(s['topic'])}**",
            color=0x147D73,
        )
        previous = self.store.latest_lesson(datetime.fromisoformat(s['start_at']))
        if previous:
            embed.add_field(name='Where we left off', value=safe(previous['recap'])[:1000], inline=False)
        embed.add_field(
            name="Preparation",
            value=safe(s["prep"])[:1000] or "Bring the vocabulary book, textbook, and exercise book.",
            inline=False,
        )
        responses = {r["user_id"]: r["response"] for r in self.store.responses(sid)}
        embed.add_field(
            name="Attendance" if s["status"]=="held" else "Can you come?",
            value="\n".join(
                f"{progress.member_line(self.store,sid,m)} · {safe(m['name'])} · 🔥 {progress.personal(self.store,m['user_id'])['current']}"
                for m in self.store.participants(sid)
            )[:1000]
            or "No roster",
            inline=False,
        )
        pending=self.store.rows("SELECT user_id FROM replacement_requests WHERE session_id=? AND revision=? AND state='pending'",(sid,s['revision']))
        if pending:
            embed.add_field(name='Replacement times needed',value=', '.join(f"<@{r['user_id']}>" for r in pending),inline=False)
        embed.add_field(name="RSVP deadline", value=when(s["rsvp_due"]))
        embed.add_field(
            name=f"Group commitment · {month}",
            value=f"**{self.store.monthly_count(month)}/2** classes held\nBeeminder: {'configured; month-end audit' if self.bee.configured() else 'not activated'}",
        )
        agreed=progress.locked(self.store,sid)
        embed.add_field(name='Group agreement',value='✅ 4/4 agreed · time locked' if agreed else '✅ All four attended' if progress.complete(self.store,sid) else '⚠️ All four must attend · no monthly credit yet' if s['status']=='held' else '⏳ Waiting for all four',inline=False)
        embed.add_field(name='Monthly target',value=progress.summary(self.store,month),inline=False)
        streak=progress.group_streak(self.store)
        embed.add_field(name='🔥 Group monthly streak',value=f"{streak['current']} months · best {streak['best']}")
        embed.add_field(
            name="Practice links",
            value="\n".join(f"[{name}]({url})" for name, url in RESOURCES[:2]),
            inline=False,
        )
        local_start = datetime.fromisoformat(s['start_at']).astimezone(ZoneInfo('America/Chicago'))
        iq = TIMETABLE['iqamah'].get(local_start.date().isoformat())
        if iq:
            embed.add_field(name='Fajr at IACC', value=f"[Official timetable]({SOURCE}): iqamah {iq} AM. Class starts at the time above, after prayer.", inline=False)
        embed.set_footer(
            text=f"In person · {s['duration']} minutes · Revision {s['revision']} · Time moves automatically when all four agree"
        )
        return embed

    async def update_card(self, sid, create=False):
        s = self.store.session(sid)
        channel = await self.channel()
        if not channel:
            raise RuleError("Run /setup in the announcements channel first.")
        view = ClassView(self, sid, s["revision"]) if s["status"] == "scheduled" else None
        if s["message_id"]:
            try:
                message = await channel.fetch_message(s["message_id"])
                await message.edit(embed=self.card(sid), view=view)
                return
            except discord.NotFound:
                pass
        if create:
            people = ' '.join(f"<@{p['user_id']}>" for p in self.store.participants(sid))
            message = await channel.send(content=f"📚 {people} Can you make this class? Tap your response below.", embed=self.card(sid), view=view)
            with self.store.db:
                self.store.db.execute("UPDATE sessions SET message_id=? WHERE id=?", (message.id, sid))

    def poll_embed(self, pid, slot=None):
        p=self.store.poll(pid)
        people=self.store.participants(p['session_id'])
        embed=discord.Embed(title=f"📅 Reschedule · Class #{p['session_id']}",color=0xE0A526,
            description="Tap **Update my availability**, select every workable time, then save. **All four must agree** before class moves. The original class stays until then.\n"+f"Closes {when(p['deadline'])}")
        for n in range(len(p['options'])) if slot is None else [slot]:
            answers=[(m,self.store.poll_answer(pid,m['user_id'],n)) for m in people]
            count=sum(a=='yes' for _,a in answers)
            lines=[f"{ {'yes':'✅','no':'❌','waiting':'⏳'}[a]} **{safe(m['name'])}** · { {'yes':'Coming','no':"Can't",'waiting':'Awaiting response'}[a]}" for m,a in answers]
            embed.add_field(name=f"Proposed time {n+1} · {count}/4 coming",value=when(p['options'][n])+"\n\n"+"\n".join(lines),inline=False)
        status={'open':'Voting open · First unanimous time locks automatically','confirmed':'✅ Agreed — class moved automatically','no_agreement':'No common time — suggest another time'}.get(p['status'],p['status'])
        embed.set_footer(text=status+' · Availability votes do not add Al-Mizan deductions')
        return embed

    async def update_poll(self, pid, create=False):
        async with self.poll_render_lock:
            await self._render_poll(pid,create)

    async def _render_poll(self, pid, create=False):
        p=self.store.poll(pid)
        channel=await self.channel()
        if not channel: raise RuleError('Run /setup first.')
        active=p['status']=='open' and self.store.clock()<datetime.fromisoformat(p['deadline'])
        from .availability import SharedPollView
        summary_view=SharedPollView(self,pid) if active else SuggestionReminder(self,p['session_id'],p['revision']) if p['status']=='no_agreement' else None
        if p['message_id']:
            try:
                await (await channel.fetch_message(p['message_id'])).edit(embed=self.poll_embed(pid),view=summary_view)
            except discord.NotFound:
                p['message_id']=None
        if not p['message_id'] and create:
            people=' '.join(f"<@{m['user_id']}>" for m in self.store.participants(p['session_id']))
            msg=await channel.send(content=f'📅 {people} **Rescheduling: choose every time you can attend.**',embed=self.poll_embed(pid),view=summary_view)
            with self.store.db: self.store.db.execute('UPDATE polls SET message_id=? WHERE id=?',(msg.id,pid))
        # Retire older separate option cards without deleting messages or their history.
        for row in self.store.rows('SELECT * FROM poll_cards WHERE poll_id=?',(pid,)):
            try:
                await (await channel.fetch_message(row['message_id'])).edit(content='📅 Use the shared rescheduling card above to update availability.',embed=None,view=None)
            except discord.NotFound: pass
            with self.store.db:
                self.store.db.execute('DELETE FROM poll_cards WHERE poll_id=? AND slot=?',(pid,row['slot']))

    async def setup_hook(self):
        self.http_client = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self.add_view(ui.Panel(self))
        from .debug import DebugPanel
        self.add_view(DebugPanel(self))
        for s in self.store.rows(
            "SELECT * FROM sessions WHERE status='scheduled' AND message_id IS NOT NULL"
        ):
            self.add_view(ClassView(self, s["id"], s["revision"]), message_id=s["message_id"])
            self.add_view(ui.ConflictView(self, s["id"], s["revision"]))
            self.add_view(SuggestionReminder(self, s["id"], s["revision"]))
            self.add_view(Recap(self, s["id"], s["revision"]))
        from .availability import SharedPollView
        for p in self.store.rows("SELECT * FROM polls WHERE status IN ('open','no_agreement') AND message_id IS NOT NULL"):
            self.add_view(SharedPollView(self,p['id']) if p['status']=='open' else SuggestionReminder(self,p['session_id'],p['revision']),message_id=p['message_id'])
        for s in self.store.rows("SELECT * FROM sessions WHERE status='held'"):
            self.add_view(Recap(self, s['id'], s['revision']))
        guild = os.environ.get("DISCORD_GUILD_ID")
        if not guild:
            raise RuntimeError(
                "DISCORD_GUILD_ID is required; do not expose commands across unrelated servers."
            )
        target = discord.Object(id=int(guild))
        self.tree.copy_global_to(guild=target)
        await self.tree.sync(guild=target)
        app = web.Application()

        async def health(request):
            healthy = (
                self.is_ready()
                and self.last_tick is not None
                and (self.store.clock() - self.last_tick).total_seconds() < 180
                and self.tick_errors < 3
            )
            return web.json_response(
                {"service": "al-mizan", "ready": healthy}, status=200 if healthy else 503
            )

        app.router.add_get("/health", health)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        await web.TCPSite(self.runner, "0.0.0.0", int(os.environ.get("PORT", "8080"))).start()
        self.scheduler.start()

    async def close(self):
        for demo in self.test_sessions.values():
            await demo.close()
        self.test_sessions.clear()
        self.scheduler.cancel()
        if self.http_client:
            await self.http_client.close()
        if self.runner:
            await self.runner.cleanup()
        await super().close()

    @tasks.loop(seconds=30)
    async def scheduler(self):
        try:
            await self.tick()
            self.last_tick = self.store.clock()
            self.tick_errors = 0
        except Exception as exc:
            self.tick_errors += 1
            log.error("Scheduler failed: %s", type(exc).__name__)

    @scheduler.before_loop
    async def before_scheduler(self):
        await self.wait_until_ready()
        from .debug import ensure
        await ensure(self)
        channel=await self.channel()
        panel=self.store.get('panel_message_id')
        if channel and panel:
            try:
                await (await channel.fetch_message(panel)).edit(content='🦆 **Shaykh Standly · Arabic Class**\nRSVPs, class progress and lessons. Private rehearsals live in #shaykh-debug.',view=ui.Panel(self))
            except discord.NotFound: self.store.set('panel_message_id',None)
        for s in self.store.rows("SELECT * FROM sessions WHERE status='scheduled' AND message_id IS NOT NULL"):
            await self.update_card(s['id'])

        for p in self.store.rows("SELECT id FROM polls WHERE status='open' AND message_id IS NOT NULL"):
            await self.update_poll(p['id'],create=True)

    async def tick(self):
        if not self.store.get("channel_id"):
            return
        await ui.ensure_panel(self)
        await consensus.flush(self)
        for poll in self.store.rows("SELECT id FROM polls WHERE status='open'"):
            if consensus.winner(self.store,poll['id']) is not None:
                await consensus.update(self,poll['id'])
        try:
            weekly_sid = ensure_weekly(self.store)
            if weekly_sid:
                session = self.store.session(weekly_sid)
                if session['status'] == 'scheduled' and not session['message_id']:
                    await self.update_card(weekly_sid, create=True)
        except RuleError as exc:
            await self.notify_once('calendar-missing:' + self.store.clock().strftime('%Y-%m-%d'), str(exc))
        now = self.store.clock()
        local = now.astimezone(ZoneInfo(self.store.tz))
        month = month_of(now, self.store.tz)
        for s in self.store.rows("SELECT * FROM sessions WHERE status='scheduled'"):
            sid, rev = s["id"], s["revision"]
            start = datetime.fromisoformat(s["start_at"])
            end = datetime.fromisoformat(s["confirmation_due"])
            responses = {r["user_id"]: r["response"] for r in self.store.responses(sid)}
            if datetime.fromisoformat(s["rsvp_due"]) <= now < start:
                for p in self.store.participants(sid):
                    if p["user_id"] not in responses:
                        key = f"rsvp:{sid}:{rev}:{p['user_id']}"
                        if not self.store.notice_sent(key):
                            try:
                                user = self.get_user(p["user_id"]) or await self.fetch_user(p["user_id"])
                                await user.send(
                                    f"Class #{sid}: {NUDGE}\n{when(s['start_at'])}\nUse the class announcement buttons in the server."
                                )
                                self.store.mark_notice(key)
                            except discord.HTTPException:
                                await self.notify_once(key, f"<@{p['user_id']}> Class #{sid}: {NUDGE}")
                conflicts = [uid for uid, r in responses.items() if r in {"unavailable", "change"}]
                if conflicts:
                    await self.notify_once(
                        f"conflict:{sid}:{rev}",
                        f"⚠️ Class #{sid} has {len(conflicts)} scheduling conflict(s). Everyone must agree on a replacement; suggest and vote in the rescheduling poll.",
                    )
            remaining = (start - now).total_seconds()
            if 0 < remaining <= 86400:
                label = "soon" if remaining <= 3600 else "day"
                await self.notify_once(
                    f"reminder:{sid}:{rev}:{label}",
                    f"📚 {'Class starts within an hour' if label == 'soon' else 'Class within 24 hours'}: #{sid}, {when(s['start_at'])}\n📍 {safe(s['location'])} · {safe(s['topic'])}\nPreparation: {safe(s['prep']) or 'Bring all three books.'}",
                )
        # Ask the TA after each class, including held classes still missing a recap.
        for s in self.store.rows("SELECT * FROM sessions WHERE status IN ('scheduled','held')"):
            if now < datetime.fromisoformat(s['confirmation_due']):
                continue
            notes=self.store.one('SELECT 1 FROM lesson_notes WHERE session_id=? AND revision=?',(s['id'],s['revision']))
            if notes: continue
            end_local=datetime.fromisoformat(s['confirmation_due']).astimezone(ZoneInfo(self.store.tz))
            if local.date()!=end_local.date() and local.hour<9: continue
            guild=self.get_guild(int(os.environ.get('DISCORD_GUILD_ID') or self.store.get('guild_id',0)))
            ta=getattr(self,'demo_owner',None) or self.store.get('ta_user_id') or (guild.owner_id if guild else None)
            mention=f'<@{ta}> ' if ta else 'TA: '
            await self.notify_once(
                f"recap:{s['id']}:{s['revision']}:{local.date()}",
                mention+f"did class #{s['id']} happen? **What did you cover, and what are we doing next?** Add a short recap below; it will carry into next week's announcement. Accepted RSVPs count as present; record exceptions first.",
                view=Recap(self,s['id'],s['revision']))
        for req in self.store.rows("SELECT r.* FROM replacement_requests r JOIN sessions s ON s.id=r.session_id AND s.revision=r.revision WHERE r.state='pending' AND s.status='scheduled'"):
            if now < datetime.fromisoformat(req['updated_at'])+timedelta(hours=12): continue
            await self.notify_once(f"suggestion:{req['session_id']}:{req['revision']}:{req['user_id']}:{local.date()}",
                f"<@{req['user_id']}> you can't make class — please suggest a time you CAN attend.",
                view=SuggestionReminder(self,req['session_id'],req['revision']))
        for p in self.store.rows("SELECT * FROM polls WHERE status='open'"):
            pid = p["id"]
            if now >= datetime.fromisoformat(p["deadline"]):
                await consensus.update(self,pid)
            elif now >= datetime.fromisoformat(p["deadline"]) - timedelta(hours=4):
                options=self.store.poll(pid)['options']
                missing=[f"<@{m['user_id']}>" for m in self.store.participants(p['session_id'])
                    if any(self.store.poll_answer(pid,m['user_id'],n)=='waiting' for n in range(len(options)))]
                if missing:
                    await self.notify_once(
                        f"pollnudge:{pid}",
                        " ".join(missing)
                        + f" please use Update my availability on reschedule #{pid}, select every workable time, and save.",
                    )
        if len(self.store.roster())==4:
            d=progress.danger(self.store)
            # Monthly danger belongs on the scheduling card, not in a duplicate message.
            signature=(d['level'],d['held'],d['confirmed'])
            if getattr(self,'progress_signature',None)!=signature:
                for c in self.store.rows("SELECT id FROM sessions WHERE status='scheduled' AND message_id IS NOT NULL"):
                    await self.update_card(c['id'])
                self.progress_signature=signature
        if len(self.store.roster()) != 4:
            await self.notify_once(
                'roster-needed',
                'TA: use **Class roster** on the control panel to select the four participants. Weekly Saturday invitations start automatically once all four are registered.',
            )
        if (
            self.bee.configured()
            and (local + timedelta(days=1)).month != local.month
            and (local.hour, local.minute) >= (23, 50)
        ):
            try:
                results = await self.bee.sync(self.http_client, month)
                for result in results:
                    await self.notify_once(f"bee:{month}:{result}", "🐝 " + result)
            except RuleError as exc:
                await self.notify_once(
                    f"bee-blocked:{month}", "🐝 Monthly financial audit needs TA action: " + str(exc)
                )
        if self.bee.configured() and local.day == 1:
            prev = month_of(now - timedelta(days=1), self.store.tz)
            if prev >= self.bee.start_month:
                sent = self.store.one(
                    "SELECT COUNT(*) n FROM deliveries WHERE month=? AND state='sent'", (prev,)
                )["n"]
                if sent < len(self.bee.bindings):
                    await self.notify_once(
                        f"bee-missed:{prev}",
                        f"🐝 {prev}: monthly Beeminder audit incomplete. TA must reconcile with Beeminder; no automatic backdated failure will be sent.",
                    )
        if local.day == 1:
            prev = month_of(now - timedelta(days=1), self.store.tz)
            board = "\n".join(f"{safe(m['name'])}: {m['points']:+d}" for m in self.store.standings(prev))
            await self.notify_once(
                f"monthly:{month}",
                f"⚖️ **{prev} final standings**\n{board}\nClasses held: {self.store.monthly_count(prev)}/2.\n**{month} starts at zero. History is preserved.**",
            )


def register_commands(bot):
    tree, store = bot.tree, bot.store

    @tree.error
    async def error(interaction, error):
        await bot.respond_error(interaction, error)

    @tree.command(description="Configure the class announcement channel and TA role (server managers only)")
    @app_commands.default_permissions(manage_guild=True)
    async def setup(
        interaction: discord.Interaction, channel: discord.TextChannel | None = None, ta_role: discord.Role | None = None
    ):
        bot.require_ta(interaction)
        if not interaction.permissions.manage_guild and interaction.user.id != int(
            os.getenv("BOT_OWNER_ID", "0")
        ):
            raise RuleError("A server manager must configure the TA role.")
        channel = channel or interaction.channel
        if not isinstance(channel, discord.TextChannel):
            raise RuleError("Choose a text channel for announcements.")
        if channel.guild.id != interaction.guild_id:
            raise RuleError("Choose a channel in this server.")
        store.set("guild_id", interaction.guild_id)
        store.set("ta_user_id", interaction.user.id)
        if store.get('channel_id') != channel.id:
            store.set('panel_message_id', None)
        store.set("channel_id", channel.id)
        store.set("ta_role_id", ta_role.id if ta_role else None)
        store.set("timezone", "America/Chicago")
        await interaction.response.send_message(
            "Configured: Saturday after Fajr congregation, 60 minutes, IACC Plano, Dallas time. Use Class roster to select four people; Saturday invitations then start automatically using IACC Fajr iqamah + 15 minutes. Change this in Status & settings.",
            ephemeral=True,
        )

    @tree.command(description="Add a participant or toggle a hiatus; teachers are participants too")
    async def member(interaction: discord.Interaction, person: discord.Member | None = None, active: bool = True):
        bot.require_ta(interaction)
        if person is None:
            return await ui.route(bot, interaction, 'member')
        store.member(person.id, person.display_name, active)
        await interaction.response.send_message(
            f"{safe(person.display_name)}: {'active for future classes' if active else 'hiatus for future classes'}. Existing session rosters stay intact; use /attendance excused for those.",
            ephemeral=True,
        )

    @tree.command(description="Preview next Saturday after IACC Fajr; defaults filled, tap to announce")
    async def schedule(
        interaction: discord.Interaction,
        start: str | None = None,
        topic: str = "Arabic class · continue our current lesson",
        prep: str = "",
        location: str = "IACC Plano · after Fajr congregation",
        duration: int = 60,
        rsvp_deadline: str | None = None,
    ):
        bot.require_ta(interaction)
        if start is None:
            return await ui.route(bot, interaction, 'schedule')
        dt = parse_local(start, store.tz)
        due = parse_local(rsvp_deadline, store.tz) if rsvp_deadline else None
        sid = store.create_class(dt, duration, location, topic, interaction.user.id, prep, due)
        await interaction.response.defer(ephemeral=True)
        await bot.update_card(sid, create=True)
        await interaction.followup.send(
            f"Class #{sid} announced. Default RSVP deadline is 48 hours before class, or soon if scheduled late.",
            ephemeral=True,
        )

    @tree.command(description="Show the latest class card, current location, and responses")
    async def nextclass(interaction: discord.Interaction):
        bot.check_guild(interaction)
        sessions = store.rows("SELECT * FROM sessions WHERE status='scheduled' ORDER BY start_at")
        if not sessions:
            await interaction.response.send_message(
                "No scheduled class. A TA can use /schedule; use /status to see unresolved makeups.",
                ephemeral=True,
            )
            return
        s = sessions[0]
        await interaction.response.send_message(
            embed=bot.card(s["id"]), view=ClassView(bot, s["id"], s["revision"]), ephemeral=True
        )

    @tree.command(description="Respond to a class invitation")
    async def rsvp(
        interaction: discord.Interaction,
        class_id: int | None = None,
        response: Literal["coming", "unavailable"] | None = None,
        note: str = "",
    ):
        bot.check_guild(interaction)
        if class_id is None or response is None:
            return await ui.route(bot, interaction, 'rsvp', class_id=class_id)
        from .flow import respond
        await respond(bot,interaction,class_id,interaction.user.id,response=='coming',store.session(class_id)['revision'])

    @tree.command(description="Report a missing person, last-minute conflict, or class that did not happen")
    async def report(interaction: discord.Interaction, class_id: int | None = None, reason: str | None = None):
        bot.check_guild(interaction)
        if class_id is None or reason is None:
            return await ui.route(bot, interaction, 'report', class_id=class_id)
        store.ensure_participant(class_id, interaction.user.id)
        store.disruption(class_id, interaction.user.id, reason)
        await interaction.response.send_message(
            "Reported. No penalty assigned to the reporter.", ephemeral=True
        )
        await bot.notify(
            f"⚠️ Class #{class_id}: <@{interaction.user.id}> reports {safe(reason)}.\nTA: are the rest still meeting? If yes, keep class. If no, /cancel then /poll."
        )

    @tree.command(description="Open a replacement poll; times separated by semicolons, Dallas time")
    async def poll(interaction: discord.Interaction, class_id: int | None = None, options: str | None = None, deadline: str | None = None):
        bot.require_ta(interaction)
        if class_id is None or options is None or deadline is None:
            return await ui.route(bot, interaction, 'poll', class_id=class_id)
        slots = [stamp(parse_local(x.strip(), store.tz)) for x in options.split(";")]
        pid = store.open_poll(class_id, slots, parse_local(deadline, store.tz), interaction.user.id)
        await interaction.response.defer(ephemeral=True)
        await bot.update_poll(pid, create=True)
        await interaction.followup.send(
            f"Poll #{pid} posted. Original class is unchanged until you confirm.", ephemeral=True
        )

    @tree.command(description="Confirm a poll option; optionally identify who broke an accepted commitment")
    async def confirm_slot(
        interaction: discord.Interaction,
        poll_id: int | None = None,
        option: int | None = None,
        responsible: discord.Member | None = None,
        excused: bool = False,
        accept_conflicts: bool = False,
    ):
        bot.require_ta(interaction)
        if poll_id is None or option is None:
            return await ui.route(bot, interaction, 'confirm_slot', poll_id=poll_id)
        p = store.poll(poll_id)
        await interaction.response.defer(ephemeral=True)
        await consensus.update(bot,poll_id)
        await interaction.followup.send('Times lock automatically when all four vote for the same option. There is no TA override.',ephemeral=True)

    @tree.command(description="Mark class as not held and needing replacement; no automatic group blame")
    async def cancel(
        interaction: discord.Interaction,
        class_id: int | None = None,
        reason: str | None = None,
        responsible: discord.Member | None = None,
        excused: bool = False,
    ):
        bot.require_ta(interaction)
        if class_id is None or reason is None:
            return await ui.route(bot, interaction, 'cancel', class_id=class_id)
        store.cancel(class_id, interaction.user.id, reason, responsible.id if responsible else None, excused)
        await interaction.response.defer(ephemeral=True)
        await bot.update_card(class_id)
        await bot.notify(
            f"Class #{class_id} **needs a replacement**. {safe(reason)}\nTA: /poll with alternatives. No class credit was recorded."
        )
        if responsible and not excused:
            await bot.notify_once(
                f"cancel-roast:{class_id}:{responsible.id}",
                f"<@{responsible.id}> **−2 · Confirmed class disruption**\n" + roast(0),
            )
        await interaction.followup.send("Recorded. Open /poll to find a replacement.", ephemeral=True)

    @tree.command(
        description="Record an exception to trusted attendance; partial attendance earns 0 pending review"
    )
    async def attendance(
        interaction: discord.Interaction,
        class_id: int | None = None,
        person: discord.Member | None = None,
        status: Literal["present", "absent", "excused", "partial"] | None = None,
        reason: str = "",
    ):
        bot.require_ta(interaction)
        if class_id is None or person is None or status is None:
            return await ui.route(bot, interaction, 'attendance', class_id=class_id)
        store.set_attendance(class_id, person.id, status, interaction.user.id, reason)
        await interaction.response.send_message(
            f"Recorded {safe(person.display_name)}: {status}. Only all-four attendance counts toward 2/month.", ephemeral=True
        )
        await bot.update_card(class_id)
        if status == "absent":
            await bot.notify_once(
                f"roast:{class_id}:{person.id}",
                f"<@{person.id}> **−2 · Unexcused absence**\n" + roast(class_id + person.id),
            )

    @tree.command(description="Confirm class happened; accepted RSVPs count as present unless corrected")
    async def classheld(interaction: discord.Interaction, class_id: int | None = None):
        bot.require_ta(interaction)
        if class_id is None:
            return await ui.route(bot, interaction, 'classheld')
        changed = store.held(class_id, interaction.user.id)
        await interaction.response.defer(ephemeral=True)
        await bot.update_card(class_id)
        m = month_of(datetime.fromisoformat(store.session(class_id)["start_at"]), store.tz)
        await interaction.followup.send(
            f"{'Confirmed' if changed else 'Already confirmed'}: {store.monthly_count(m)}/2 group classes in {m}. Attendance counted once. Beeminder receives the GROUP month-end result, not individual attendance.",
            ephemeral=True,
        )
        if changed:
            await bot.notify(
                f"✅ Class #{class_id} held. **{store.monthly_count(m)}/2** this month. Al-Mīzān updated."
            )

    @tree.command(description="Reopen a mistaken class confirmation before any financial audit")
    async def reopen(interaction: discord.Interaction, class_id: int | None = None):
        bot.require_ta(interaction)
        if class_id is None:
            return await ui.route(bot, interaction, 'reopen')
        store.reopen(class_id, interaction.user.id)
        await interaction.response.defer(ephemeral=True)
        await bot.update_card(class_id)
        await interaction.followup.send(
            "Reopened; attendance credit removed. Correct the record, then confirm or cancel.", ephemeral=True
        )

    @tree.command(description="Waive a disruption penalty with an audit reason")
    async def waive(interaction: discord.Interaction, class_id: int | None = None, person: discord.Member | None = None, reason: str | None = None):
        bot.require_ta(interaction)
        if class_id is None or person is None or reason is None:
            return await ui.route(bot, interaction, 'waive', class_id=class_id)
        store.waive(class_id, person.id, interaction.user.id, reason)
        await interaction.response.send_message(
            "Penalty waived. For an absence, also mark /attendance excused.", ephemeral=True
        )

    @tree.command(description="Show Al-Mizan standings for this month or a previous YYYY-MM")
    async def mizan(interaction: discord.Interaction, month: str | None = None):
        bot.check_guild(interaction)
        if month is None:
            return await ui.route(bot, interaction, 'mizan')
        month = month or month_of(store.clock(), store.tz)
        from .reports import show_report
        await show_report(bot,interaction,month)

    @tree.command(description="Show class preparation and Quizlet links")
    async def resources(interaction: discord.Interaction):
        bot.check_guild(interaction)
        await interaction.response.send_message(
            "\n".join(f"[{name}]({url})" for name, url in RESOURCES)
            + "\nQuizlet completion is not automatically tracked.",
            ephemeral=True,
        )

    @tree.command(description="See open classes, unresolved makeups, and integration status")
    async def status(interaction: discord.Interaction):
        bot.check_guild(interaction)
        lines = [f"#{s['id']} · {s['status']} · {when(s['start_at'])}" for s in store.next_classes()]
        await interaction.response.send_message(
            "**Class status**\n"
            + ("\n".join(lines) or "No open classes.")
            + f"\nRoster: {len(store.roster())}\nBeeminder: {'enabled' if bot.bee.configured() else 'not activated'}\nWUPHF: {'configured; opt-in required' if os.getenv('TWILIO_ACCOUNT_SID') else 'not configured'}\nSchedule: Saturday after Fajr congregation, IACC Plano. Weekly invitations use the official IACC timetable, plus an adjustable after-prayer allowance. Use /standly for buttons.",
            ephemeral=True,
        )

    @tree.command(description="Privately choose SMS/call nudges and whether Fajr-time nudges are allowed")
    async def nudges(
        interaction: discord.Interaction, sms: bool | None = None, calls: bool | None = None, allow_fajr: bool | None = None
    ):
        bot.check_guild(interaction)
        if sms is None and calls is None and allow_fajr is None:
            return await ui.route(bot, interaction, 'nudges')
        current = store.get(f'nudges:{interaction.user.id}', {})
        sms = current.get('sms',False) if sms is None else sms
        calls = current.get('calls',False) if calls is None else calls
        allow_fajr = current.get('early',False) if allow_fajr is None else allow_fajr
        bot.wuphf.consent(interaction.user.id, sms, calls, allow_fajr)
        await interaction.response.send_message(
            f"Your preferences: SMS {sms}, calls {calls}, Fajr/quiet-hours nudges {allow_fajr}. Use /nudges with both false to stop. Your number must be added privately in Railway; never post it in the channel.",
            ephemeral=True,
        )

    @tree.command(description="TA: send an opted-in member an SMS or voice WUPHF (one per hour)")
    async def wuphf(
        interaction: discord.Interaction, person: discord.Member | None = None, method: Literal["sms", "calls"] = "sms"
    ):
        bot.require_ta(interaction)
        if person is None:
            return await ui.route(bot, interaction, 'wuphf')
        upcoming = store.rows(
            "SELECT * FROM sessions WHERE status='scheduled' AND start_at>? ORDER BY start_at LIMIT 1",
            (stamp(store.clock() - timedelta(hours=2)),),
        )
        if not upcoming:
            raise RuleError("Schedule a class before sending a WUPHF.")
        s = upcoming[0]
        local = (
            datetime.fromisoformat(s["start_at"])
            .astimezone(ZoneInfo(store.tz))
            .strftime("%a %b %d at %I:%M %p %Z")
        )
        message = f"Arabic class {local}, {s['location']}. Please check Discord and confirm your plans."
        await interaction.response.defer(ephemeral=True)
        result = await bot.wuphf.send(bot.http_client, person.id, method, message)
        await interaction.followup.send(result, ephemeral=True)

    @tree.command(description="TA: read-only validation of the group Beeminder goals; never changes pledges")
    async def beeminder(interaction: discord.Interaction):
        bot.require_ta(interaction)
        await interaction.response.defer(ephemeral=True)
        results = []
        for binding in bot.bee.bindings:
            try:
                g = await bot.bee.validate(bot.http_client, binding)
                results.append(
                    f"{safe(binding['username'])}: checks passed; ${g['pledge']} currently at stake"
                )
            except RuleError as exc:
                results.append(str(exc))
        await interaction.followup.send(
            "\n".join(results)
            or "Not configured. Each person needs a monthly-failure goal and private token. See BEEMINDER.md. No goals or charges have been created by this command.",
            ephemeral=True,
        )

    @tree.command(description="TA: export class records and scores (no tokens or phone numbers)")
    async def export(interaction: discord.Interaction):
        bot.require_ta(interaction)
        payload = {
            t: store.rows("SELECT * FROM " + t)
            for t in ["members", "sessions", "rsvps", "attendance", "ledger", "polls", "votes", "audit", "replacement_requests", "lesson_notes", "auto_moves"]
        }
        file = discord.File(
            io.BytesIO(json.dumps(payload, ensure_ascii=False, indent=2).encode()),
            filename="al-mizan-records.json",
        )
        await interaction.response.send_message("Private class record export.", file=file, ephemeral=True)

    @tree.command(description="TA: test with four simulated members, isolated records and mock providers")
    @app_commands.default_permissions(manage_guild=True)
    async def testmode(interaction: discord.Interaction):
        from .testing import open_test
        await open_test(bot, interaction)

    @tree.command(description="Open Shaykh Standly's buttons and menus")
    async def standly(interaction: discord.Interaction):
        await ui.route(bot, interaction, 'standly')

    @tree.command(description="Open Shaykh Standly's buttons and menus")
    async def help(interaction: discord.Interaction):
        await ui.route(bot, interaction, 'help')


def main():
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("Set DISCORD_BOT_TOKEN and DISCORD_GUILD_ID privately. See README.md.")
    if os.getenv("RAILWAY_ENVIRONMENT_ID") and not os.getenv("RAILWAY_VOLUME_MOUNT_PATH"):
        raise SystemExit(
            "Attach a persistent Railway volume before starting; refusing ephemeral class records."
        )
    store = Store(os.environ.get("DATABASE_PATH", "data/mizan.sqlite3"))
    bot = MizanBot(store)
    try:
        bot.run(token, log_handler=None)
    finally:
        store.close()


if __name__ == "__main__":
    main()
