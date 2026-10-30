from __future__ import annotations

import io
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

log = logging.getLogger("mizan")


def safe(text):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(text)))


def when(iso):
    return f"<t:{int(datetime.fromisoformat(iso).timestamp())}:F>"


class ClassView(discord.ui.View):
    def __init__(self, bot, sid, revision):
        super().__init__(timeout=None)
        self.bot = bot
        for label, value, style in [
            ("Coming", "coming", discord.ButtonStyle.success),
            ("Running late", "late", discord.ButtonStyle.primary),
            ("Can't attend", "unavailable", discord.ButtonStyle.secondary),
            ("Request another time", "change", discord.ButtonStyle.secondary),
        ]:
            button = discord.ui.Button(
                label=label, style=style, custom_id=f"mizan:rsvp:{sid}:{revision}:{value}"
            )

            async def callback(interaction, response=value):
                await interaction.response.defer(ephemeral=True)
                bot.check_guild(interaction)
                bot.store.rsvp(sid, interaction.user.id, response, revision=revision)
                await bot.update_card(sid)
                await interaction.followup.send(
                    "Saved. Notice is not automatically an excused absence. Use /report if plans break down.",
                    ephemeral=True,
                )
                if response in {"unavailable", "change"}:
                    await bot.notify(
                        f"Class #{sid}: <@{interaction.user.id}> selected **{response}**. TA: keep the time or open /poll. The class has not been cancelled."
                    )

            button.callback = callback
            self.add_item(button)
        report = discord.ui.Button(
            label="Report a problem",
            custom_id=f"mizan:report:{sid}:{revision}",
            style=discord.ButtonStyle.danger,
            row=1,
        )

        async def report_callback(interaction):
            bot.check_guild(interaction)
            bot.store.ensure_participant(sid, interaction.user.id)
            if bot.store.session(sid)["revision"] != revision:
                raise RuleError("This card is outdated. Use /nextclass.")
            await interaction.response.send_modal(ReportModal(bot, sid))

        report.callback = report_callback
        self.add_item(report)

    async def on_error(self, interaction, error, item):
        await self.bot.respond_error(interaction, error)


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
    def __init__(self, bot, pid, count):
        super().__init__(timeout=None)
        for index in range(count):
            button = discord.ui.Button(
                label=f"Option {index + 1}",
                custom_id=f"mizan:poll:{pid}:{index}",
                style=discord.ButtonStyle.secondary,
            )

            async def callback(interaction, slot=index):
                bot.check_guild(interaction)
                await interaction.response.defer(ephemeral=True)
                selected = bot.store.vote(pid, interaction.user.id, slot)
                await bot.update_poll(pid)
                await interaction.followup.send(
                    "Available ✓" if selected else "Availability removed", ephemeral=True
                )

            button.callback = callback
            self.add_item(button)
        none = discord.ui.Button(
            label="None work", custom_id=f"mizan:poll:{pid}:none", style=discord.ButtonStyle.danger
        )

        async def none_callback(interaction):
            bot.check_guild(interaction)
            p = bot.store.poll(pid)
            bot.store.ensure_participant(p["session_id"], interaction.user.id)
            if p["status"] != "open" or bot.store.clock() >= datetime.fromisoformat(p["deadline"]):
                raise RuleError("This poll has closed.")
            with bot.store.db:
                bot.store.db.execute(
                    "DELETE FROM votes WHERE poll_id=? AND user_id=?", (pid, interaction.user.id)
                )
                bot.store.db.execute("INSERT INTO votes VALUES(?,?,-1)", (pid, interaction.user.id))
            await interaction.response.defer(ephemeral=True)
            await bot.update_poll(pid)
            await interaction.followup.send(
                "Recorded: none work. The TA will see your conflict.", ephemeral=True
            )

        none.callback = none_callback
        self.add_item(none)
        self.bot = bot

    async def on_error(self, interaction, error, item):
        await self.bot.respond_error(interaction, error)


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
        register_commands(self)

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

    async def notify(self, text):
        channel = await self.channel()
        if channel:
            return await channel.send(text[:1950])

    async def notify_once(self, key, text):
        if self.store.notice_sent(key):
            return
        message = await self.notify(text)
        if message:
            self.store.mark_notice(key)

    def card(self, sid):
        s = self.store.session(sid)
        month = month_of(datetime.fromisoformat(s["start_at"]), self.store.tz)
        embed = discord.Embed(
            title=f"📚 Arabic class #{sid} · {s['status'].replace('_', ' ').title()}",
            description=f"{when(s['start_at'])}\n📍 {safe(s['location'])}\n**{safe(s['topic'])}**",
            color=0x147D73,
        )
        embed.add_field(
            name="Preparation",
            value=safe(s["prep"])[:1000] or "Bring the vocabulary book, textbook, and exercise book.",
            inline=False,
        )
        responses = {r["user_id"]: r["response"] for r in self.store.responses(sid)}
        embed.add_field(
            name="RSVPs · not attendance",
            value="\n".join(
                f"{safe(m['name'])}: {responses.get(m['user_id'], 'Not answered')}"
                for m in self.store.participants(sid)
            )[:1000]
            or "No roster",
            inline=False,
        )
        embed.add_field(name="RSVP deadline", value=when(s["rsvp_due"]))
        embed.add_field(
            name=f"Group commitment · {month}",
            value=f"**{self.store.monthly_count(month)}/2** classes held\nBeeminder: {'configured; month-end audit' if self.bee.configured() else 'not activated'}",
        )
        embed.add_field(
            name="Practice links",
            value="\n".join(f"[{name}]({url})" for name, url in RESOURCES[:2]),
            inline=False,
        )
        embed.set_footer(
            text=f"In person · {s['duration']} minutes · Revision {s['revision']} · Original time stands until TA confirms a move"
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
            message = await channel.send(embed=self.card(sid), view=view)
            with self.store.db:
                self.store.db.execute("UPDATE sessions SET message_id=? WHERE id=?", (message.id, sid))

    def poll_embed(self, pid):
        p = self.store.poll(pid)
        votes = self.store.rows("SELECT * FROM votes WHERE poll_id=?", (pid,))
        embed = discord.Embed(
            title=f"📅 Replacement poll #{pid} · Class #{p['session_id']}",
            description=f"Select **every** time you can attend. Buttons toggle availability.\nCloses {when(p['deadline'])}\nOriginal time remains unless TA explicitly cancels/moves it.",
            color=0xE0A526,
        )
        for index, option in enumerate(p["options"]):
            users = [v["user_id"] for v in votes if v["slot"] == index]
            embed.add_field(
                name=f"Option {index + 1} · {len(users)}/{len(self.store.participants(p['session_id']))} available",
                value=when(option) + "\n" + (", ".join(f"<@{u}>" for u in users) or "No responses yet"),
                inline=False,
            )
        none = [v["user_id"] for v in votes if v["slot"] == -1]
        if none:
            embed.add_field(name="None work", value=", ".join(f"<@{u}>" for u in none), inline=False)
        embed.set_footer(text=f"{p['status']} · TA confirms with /confirm_slot; votes are not new RSVPs")
        return embed

    async def update_poll(self, pid, create=False):
        p = self.store.poll(pid)
        channel = await self.channel()
        if not channel:
            raise RuleError("Run /setup first.")
        view = (
            PollView(self, pid, len(p["options"]))
            if p["status"] == "open" and self.store.clock() < datetime.fromisoformat(p["deadline"])
            else None
        )
        if p["message_id"]:
            try:
                await (await channel.fetch_message(p["message_id"])).edit(
                    embed=self.poll_embed(pid), view=view
                )
                return
            except discord.NotFound:
                pass
        if create:
            msg = await channel.send(embed=self.poll_embed(pid), view=view)
            with self.store.db:
                self.store.db.execute("UPDATE polls SET message_id=? WHERE id=?", (msg.id, pid))

    async def setup_hook(self):
        self.http_client = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        for s in self.store.rows(
            "SELECT * FROM sessions WHERE status='scheduled' AND message_id IS NOT NULL"
        ):
            self.add_view(ClassView(self, s["id"], s["revision"]), message_id=s["message_id"])
        for p in self.store.rows(
            "SELECT id,options,message_id FROM polls WHERE status='open' AND message_id IS NOT NULL"
        ):
            self.add_view(PollView(self, p["id"], len(json.loads(p["options"]))), message_id=p["message_id"])
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

    async def tick(self):
        if not self.store.get("channel_id"):
            return
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
                        f"⚠️ Class #{sid} has {len(conflicts)} scheduling conflict(s). TA: keep the time or open /poll. No automatic cancellation.",
                    )
            remaining = (start - now).total_seconds()
            if 0 < remaining <= 86400:
                label = "soon" if remaining <= 3600 else "day"
                await self.notify_once(
                    f"reminder:{sid}:{rev}:{label}",
                    f"📚 {'Class starts within an hour' if label == 'soon' else 'Class within 24 hours'}: #{sid}, {when(s['start_at'])}\n📍 {safe(s['location'])} · {safe(s['topic'])}\nPreparation: {safe(s['prep']) or 'Bring all three books.'}",
                )
            if now >= end:
                await self.notify_once(
                    f"confirm:{sid}:{rev}",
                    f"TA: did class #{sid} happen? Everyone who accepted is assumed present; report exceptions with /attendance, then /classheld. **No Beeminder credit until confirmed.** If it didn't happen, /cancel and /poll.",
                )
        for p in self.store.rows("SELECT * FROM polls WHERE status='open'"):
            pid = p["id"]
            if now >= datetime.fromisoformat(p["deadline"]):
                await self.update_poll(pid)
                await self.notify_once(
                    f"pollclosed:{pid}",
                    f"Poll #{pid} has closed. TA: review availability and /confirm_slot, or open another /poll. No automatic move.",
                )
            elif now >= datetime.fromisoformat(p["deadline"]) - timedelta(hours=4):
                responded = {
                    v["user_id"]
                    for v in self.store.rows("SELECT DISTINCT user_id FROM votes WHERE poll_id=?", (pid,))
                }
                missing = [
                    f"<@{m['user_id']}>"
                    for m in self.store.participants(p["session_id"])
                    if m["user_id"] not in responded
                ]
                if missing:
                    await self.notify_once(
                        f"pollnudge:{pid}",
                        " ".join(missing)
                        + f" please answer replacement poll #{pid}. Select all workable slots or None work.",
                    )
        count = self.store.monthly_count(month)
        remaining = self.store.rows(
            "SELECT id FROM sessions WHERE status='scheduled' AND start_at>? AND start_at<?",
            (
                stamp(now),
                stamp(
                    (local.replace(day=28) + timedelta(days=4)).replace(
                        day=1, hour=0, minute=0, second=0, microsecond=0
                    )
                ),
            ),
        )
        if count < 2 and len(remaining) < 2 - count and local.day >= 15:
            await self.notify_once(
                f"risk:{month}:{local.day}",
                f"⚠️ Save this month: **{count}/2** classes held; only {len(remaining)} future sessions are scheduled. TA: /schedule a class or /poll a makeup. Group financial commitment may be at risk.",
            )
        for s in self.store.rows("SELECT id FROM sessions WHERE status='needs_replacement'"):
            await self.notify_once(
                f"replacement:{s['id']}:{local.strftime('%Y-%m-%d')}",
                f"Class #{s['id']} still needs a replacement. TA: /poll with two or more alternatives.",
            )
        # Weekly schedule is prayer-relative, not a fabricated fixed Fajr time.
        if local.weekday() == 0 and not self.store.next_classes():
            await self.notify_once(
                f"schedule-week:{local.strftime('%Y-%W')}",
                "TA: schedule Saturday's 60-minute class after Fajr congregation at IACC. Check the official iqamah time, then /schedule the actual class start. https://planomasjid.org/iacc-prayer-timings-2026/",
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
        interaction: discord.Interaction, channel: discord.TextChannel, ta_role: discord.Role | None = None
    ):
        bot.require_ta(interaction)
        if not interaction.permissions.manage_guild and interaction.user.id != int(
            os.getenv("BOT_OWNER_ID", "0")
        ):
            raise RuleError("A server manager must configure the TA role.")
        if channel.guild.id != interaction.guild_id:
            raise RuleError("Choose a channel in this server.")
        store.set("guild_id", interaction.guild_id)
        store.set("channel_id", channel.id)
        store.set("ta_role_id", ta_role.id if ta_role else None)
        store.set("timezone", "America/Chicago")
        await interaction.response.send_message(
            "Configured: Saturday after Fajr congregation, 60 minutes, IACC Plano, Dallas time. Add your four participants with /member, then /schedule the verified start time.",
            ephemeral=True,
        )

    @tree.command(description="Add a participant or toggle a hiatus; teachers are participants too")
    async def member(interaction: discord.Interaction, person: discord.Member, active: bool = True):
        bot.require_ta(interaction)
        store.member(person.id, person.display_name, active)
        await interaction.response.send_message(
            f"{safe(person.display_name)}: {'active for future classes' if active else 'hiatus for future classes'}. Existing session rosters stay intact; use /attendance excused for those.",
            ephemeral=True,
        )

    @tree.command(description="Schedule an in-person class; use YYYY-MM-DD HH:MM in Dallas time")
    async def schedule(
        interaction: discord.Interaction,
        start: str,
        topic: str,
        prep: str = "",
        location: str = "IACC Plano · after Fajr congregation",
        duration: int = 60,
        rsvp_deadline: str | None = None,
    ):
        bot.require_ta(interaction)
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
        class_id: int,
        response: Literal["coming", "late", "unavailable", "change"],
        note: str = "",
    ):
        bot.check_guild(interaction)
        store.rsvp(class_id, interaction.user.id, response, note)
        await interaction.response.defer(ephemeral=True)
        await bot.update_card(class_id)
        await interaction.followup.send(
            "RSVP saved. A request does not move or cancel class.", ephemeral=True
        )
        if response in {"unavailable", "change"}:
            await bot.notify(
                f"Class #{class_id}: <@{interaction.user.id}> reports **{response}**. {safe(note)}\nTA: keep the time or /poll."
            )

    @tree.command(description="Report a missing person, last-minute conflict, or class that did not happen")
    async def report(interaction: discord.Interaction, class_id: int, reason: str):
        bot.check_guild(interaction)
        store.ensure_participant(class_id, interaction.user.id)
        store.disruption(class_id, interaction.user.id, reason)
        await interaction.response.send_message(
            "Reported. No penalty assigned to the reporter.", ephemeral=True
        )
        await bot.notify(
            f"⚠️ Class #{class_id}: <@{interaction.user.id}> reports {safe(reason)}.\nTA: are the rest still meeting? If yes, keep class. If no, /cancel then /poll."
        )

    @tree.command(description="Open a replacement poll; times separated by semicolons, Dallas time")
    async def poll(interaction: discord.Interaction, class_id: int, options: str, deadline: str):
        bot.require_ta(interaction)
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
        poll_id: int,
        option: int,
        responsible: discord.Member | None = None,
        excused: bool = False,
        accept_conflicts: bool = False,
    ):
        bot.require_ta(interaction)
        p = store.poll(poll_id)
        available = {
            r["user_id"]
            for r in store.rows("SELECT user_id FROM votes WHERE poll_id=? AND slot=?", (poll_id, option - 1))
        }
        conflicts = [m for m in store.participants(p["session_id"]) if m["user_id"] not in available]
        if conflicts and not accept_conflicts:
            raise RuleError(
                "Not everyone marked this option available. Check the conflicts, then explicitly use accept_conflicts:true if proceeding."
            )
        old = store.session(p["session_id"])["start_at"]
        sid = store.confirm_slot(
            poll_id, option - 1, interaction.user.id, responsible.id if responsible else None, excused
        )
        await interaction.response.defer(ephemeral=True)
        await bot.update_card(sid, create=True)
        await bot.update_poll(poll_id)
        s = store.session(sid)
        await bot.notify(
            f"📅 **Class #{sid} moved**\nOld: {when(old)}\nNew: {when(s['start_at'])}\n📍 {safe(s['location'])}\nEveryone: RSVP again. Previous responses do not carry over."
        )
        if responsible and not excused:
            penalty = store.one(
                "SELECT points FROM penalties WHERE session_id=? AND user_id=?", (sid, responsible.id)
            )
            if penalty:
                await bot.notify_once(
                    f"move-roast:{sid}:{s['revision']}:{responsible.id}",
                    f"<@{responsible.id}> **{penalty['points']} · Changed an accepted commitment**\n"
                    + roast(0),
                )
        await interaction.followup.send(
            "Replacement confirmed; reminders now use the new time.", ephemeral=True
        )

    @tree.command(description="Mark class as not held and needing replacement; no automatic group blame")
    async def cancel(
        interaction: discord.Interaction,
        class_id: int,
        reason: str,
        responsible: discord.Member | None = None,
        excused: bool = False,
    ):
        bot.require_ta(interaction)
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
        class_id: int,
        person: discord.Member,
        status: Literal["present", "absent", "excused", "partial"],
        reason: str = "",
    ):
        bot.require_ta(interaction)
        store.set_attendance(class_id, person.id, status, interaction.user.id, reason)
        await interaction.response.send_message(
            f"Recorded {safe(person.display_name)}: {status}.", ephemeral=True
        )
        if status == "absent":
            await bot.notify_once(
                f"roast:{class_id}:{person.id}",
                f"<@{person.id}> **−2 · Unexcused absence**\n" + roast(class_id + person.id),
            )

    @tree.command(description="Confirm class happened; accepted RSVPs count as present unless corrected")
    async def classheld(interaction: discord.Interaction, class_id: int):
        bot.require_ta(interaction)
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
    async def reopen(interaction: discord.Interaction, class_id: int):
        bot.require_ta(interaction)
        store.reopen(class_id, interaction.user.id)
        await interaction.response.defer(ephemeral=True)
        await bot.update_card(class_id)
        await interaction.followup.send(
            "Reopened; attendance credit removed. Correct the record, then confirm or cancel.", ephemeral=True
        )

    @tree.command(description="Waive a disruption penalty with an audit reason")
    async def waive(interaction: discord.Interaction, class_id: int, person: discord.Member, reason: str):
        bot.require_ta(interaction)
        store.waive(class_id, person.id, interaction.user.id, reason)
        await interaction.response.send_message(
            "Penalty waived. For an absence, also mark /attendance excused.", ephemeral=True
        )

    @tree.command(description="Show Al-Mizan standings for this month or a previous YYYY-MM")
    async def mizan(interaction: discord.Interaction, month: str | None = None):
        bot.check_guild(interaction)
        month = month or month_of(store.clock(), store.tz)
        board = "\n".join(f"{safe(r['name'])}: **{r['points']:+d}**" for r in store.standings(month))
        await interaction.response.send_message(
            f"⚖️ **المِيزَان · {month}**\n{board or 'Add members first.'}\n📚 Group classes held: **{store.monthly_count(month)}/2**\nAttendance +1 · confirmed move after accepting −1 · last-minute/no-show −2 · excuses 0. Monthly fresh start; history preserved."
        )

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
            + f"\nRoster: {len(store.roster())}\nBeeminder: {'enabled' if bot.bee.configured() else 'not activated'}\nWUPHF: {'configured; opt-in required' if os.getenv('TWILIO_ACCOUNT_SID') else 'not configured'}\nSchedule: Saturday after Fajr congregation, IACC Plano. TA verifies each actual start time.",
            ephemeral=True,
        )

    @tree.command(description="Privately choose SMS/call nudges and whether Fajr-time nudges are allowed")
    async def nudges(
        interaction: discord.Interaction, sms: bool = False, calls: bool = False, allow_fajr: bool = False
    ):
        bot.check_guild(interaction)
        bot.wuphf.consent(interaction.user.id, sms, calls, allow_fajr)
        await interaction.response.send_message(
            f"Your preferences: SMS {sms}, calls {calls}, Fajr/quiet-hours nudges {allow_fajr}. Use /nudges with both false to stop. Your number must be added privately in Railway; never post it in the channel.",
            ephemeral=True,
        )

    @tree.command(description="TA: send an opted-in member an SMS or voice WUPHF (one per hour)")
    async def wuphf(
        interaction: discord.Interaction, person: discord.Member, method: Literal["sms", "calls"] = "sms"
    ):
        bot.require_ta(interaction)
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
            for t in ["members", "sessions", "rsvps", "attendance", "ledger", "polls", "votes", "audit"]
        }
        file = discord.File(
            io.BytesIO(json.dumps(payload, ensure_ascii=False, indent=2).encode()),
            filename="al-mizan-records.json",
        )
        await interaction.response.send_message("Private class record export.", file=file, ephemeral=True)

    @tree.command(description="Show Al-Mizan's main commands")
    async def help(interaction: discord.Interaction):
        bot.check_guild(interaction)
        await interaction.response.send_message(
            "**Everyone:** /nextclass /rsvp /report /mizan /resources /status /nudges\n**TA:** /member /schedule /poll /confirm_slot /cancel /attendance /classheld /reopen /waive /wuphf /beeminder /export\nWe meet in person. Accepted RSVPs are trusted; a TA confirms class happened before financial accounting. No voice tracking.\n**Rescheduling:** report → TA decides whether rest still meet → poll alternatives → TA confirms → everyone RSVPs again.",
            ephemeral=True,
        )


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
