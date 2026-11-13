"""Inbound text replies: RSVP or check in by SMS when Discord buttons do not show up.

Twilio POSTs each reply to /twilio/sms. The request is trusted only if its
X-Twilio-Signature matches TWILIO_WEBHOOK_URL (the exact public URL configured in the
Twilio console) signed with the account auth token. Without that variable the route
stays off and texts keep pointing people to Discord.
"""
import base64
import hashlib
import hmac
import logging
import os
import re
from xml.sax.saxutils import escape

import discord
from aiohttp import web

from .content import roast
from .core import RuleError, stamp

log = logging.getLogger("mizan.inbound")

YES = {"yes", "y", "yeah", "yep", "yup", "coming", "in", "naam", "1"}
NO = {"no", "n", "nope", "cant", "cannot", "out", "la", "2"}
HERE = {"here", "ack", "ok", "okay", "present"}
STOP = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}
START = {"start", "unstop"}
HELP = "Reply YES or NO to RSVP for the next class, or HERE to check in. STOP ends texts."


def signature(url, params, token):
    payload = url + "".join(key + params[key] for key in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()


def keyword(body):
    words = re.sub(r"[^a-z0-9 ]", "", (body or "").lower()).split()
    return words[0] if words else ""


def open_class(store, uid):
    return store.one(
        "SELECT s.* FROM sessions s JOIN session_members m ON m.session_id=s.id "
        "WHERE s.status='scheduled' AND m.user_id=? AND s.start_at>? ORDER BY s.start_at LIMIT 1",
        (uid, stamp(store.clock())),
    )


async def check_in(bot, uid):
    """Acknowledge every pending member-triggered WUPHF for this person."""
    jobs = bot.store.rows(
        "SELECT id,message_id FROM wuphf_jobs WHERE user_id=? AND source='manual' "
        "AND state IN ('waiting_sms','waiting_call','sending_sms','sending_call')",
        (uid,),
    )
    for job in jobs:
        with bot.store.db:
            bot.store.db.execute("UPDATE wuphf_jobs SET state='acknowledged' WHERE id=?", (job["id"],))
        if not job["message_id"]:
            continue
        try:
            channel = await bot.channel()
            message = await channel.fetch_message(job["message_id"])
            await message.edit(
                content=f"✅ <@{uid}> checked in by text. The telephone department stands down.", view=None
            )
        except discord.HTTPException:
            pass
    return len(jobs)


async def rsvp(bot, uid, coming):
    session = open_class(bot.store, uid)
    if not session:
        return "There is no open class to RSVP for right now."
    sid, revision = session["id"], session["revision"]
    penalty = bot.store.answer(sid, uid, coming, revision)
    await bot.update_card(sid)
    if coming:
        return f"Saved: you're coming to class #{sid}. See you after Fajr."
    await bot.notify_once(
        f'decline:{sid}:{revision}:{uid}:{penalty["points"]}',
        f"<@{uid}> can't make class (answered by text). **Al-Mizan {penalty['points']}**. They need to suggest "
        "another time. The time moves automatically only when all four agree.\n" + roast(sid + uid, bot.store, uid),
    )
    return (
        f"Saved: you can't make class #{sid}. Al-Mizan {penalty['points']}. "
        "Open Discord to suggest another time; the class only moves when all four agree."
    )


async def handle(bot, phone, body):
    """Return the text to send back. Never raises: Twilio must always get an answer."""
    uid = bot.wuphf.member_for(phone)
    if uid is None:
        return "This number is not on the class roster."
    word = keyword(body)
    try:
        if word in STOP:
            bot.store.set(f"nudges:{uid}", {"sms": False, "calls": False, "early": False})
            with bot.store.db:
                bot.store.db.execute(
                    "UPDATE wuphf_jobs SET state='stopped' WHERE user_id=? "
                    "AND state IN ('waiting_sms','waiting_call','sending_sms','sending_call')",
                    (uid,),
                )
            return "Texts stopped. Discord reminders still work."
        if word in START:
            bot.store.set(f"nudges:{uid}", {"sms": True, "calls": True, "early": False})
            return "Texts resumed. Quiet hours are 9 PM to 8 AM."
        if word in HERE:
            return (
                "Checked in. The telephone department stands down."
                if await check_in(bot, uid)
                else "Nobody is looking for you right now. " + HELP
            )
        if word in YES or word in NO:
            await check_in(bot, uid)
            return await rsvp(bot, uid, word in YES)
        return HELP
    except RuleError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - a text reply must not take the web server down.
        log.error("Inbound text failed: %s", type(exc).__name__)
        return "Something failed. Please answer in Discord."


def attach(bot, app):
    async def sms(request):
        url, token = os.getenv("TWILIO_WEBHOOK_URL"), os.getenv("TWILIO_AUTH_TOKEN")
        if not url or not token:
            return web.Response(status=404)
        form = {key: str(value) for key, value in (await request.post()).items()}
        given = request.headers.get("X-Twilio-Signature", "")
        if not hmac.compare_digest(given, signature(url, form, token)):
            return web.Response(status=403)
        reply = await handle(bot, form.get("From", ""), form.get("Body", ""))
        return web.Response(
            text=f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{escape(reply)}</Message></Response>',
            content_type="text/xml",
        )

    app.router.add_post("/twilio/sms", sms)
