"""Text replies: RSVP and check in by SMS, trusted only with a valid Twilio signature."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from mizan import inbound
from mizan.bot import MizanBot
from mizan.core import Store, parse_local, stamp

URL = "https://standly.example.test/twilio/sms"
PHONES = {1: "+15555550101", 2: "+15555550102"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.delenv("DISCORD_GUILD_ID", raising=False)
    env = {
        "TWILIO_ACCOUNT_SID": "ACtest",
        "TWILIO_AUTH_TOKEN": "private",
        "TWILIO_PHONE_NUMBER": "+15555550100",
        "WUPHF_CONTACTS_JSON": '{"1":"PHONE_A","2":"PHONE_B"}',
        "PHONE_A": PHONES[1],
        "PHONE_B": PHONES[2],
        "TWILIO_WEBHOOK_URL": URL,
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    c = [parse_local("2026-11-05 09:00", "America/Chicago")]
    s = Store(tmp_path / "db", clock=lambda: c[0])
    s.set("guild_id", 123)
    s.set("channel_id", 456)
    for u in range(1, 5):
        s.member(u, f"Member {u}")
    sid = s.create_class(c[0] + timedelta(days=2), 60, "IACC", "Arabic", 1)
    yield s, c, sid
    s.close()


def bot_for(s):
    bot = MizanBot(s)
    bot.update_card = AsyncMock()
    bot.notify_once = AsyncMock()
    return bot


def run(coro):
    return asyncio.run(coro)


def test_signature_matches_twilio_reference_algorithm():
    # Worked example from Twilio's request-validation documentation.
    params = {
        "CallSid": "CA1234567890ABCDE",
        "Caller": "+12349013030",
        "Digits": "1234",
        "From": "+12349013030",
        "To": "+18005551212",
    }
    assert (
        inbound.signature("https://mycompany.com/myapp.php?foo=1&bar=2", params, "12345")
        == "0/KCTR6DLpKmkAf8muzZqo1nDgQ="
    )


@pytest.mark.parametrize("body", ["YES", " yes! ", "Y", "Yep, coming", "1"])
def test_yes_reply_records_rsvp_and_refreshes_card(world, body):
    s, c, sid = world

    async def go():
        bot = bot_for(s)
        reply = await inbound.handle(bot, PHONES[1], body)
        assert "coming" in reply
        assert s.one("SELECT response FROM rsvps WHERE user_id=1")["response"] == "coming"
        bot.update_card.assert_awaited_once_with(sid)
        await bot.close()

    run(go())


def test_no_reply_declines_with_penalty_and_public_roast(world):
    s, c, sid = world

    async def go():
        bot = bot_for(s)
        reply = await inbound.handle(bot, PHONES[2], "No")
        assert "Al-Mizan -1" in reply and "suggest another time" in reply
        assert s.one("SELECT response FROM rsvps WHERE user_id=2")["response"] == "unavailable"
        assert s.one("SELECT points FROM penalties WHERE user_id=2")["points"] == -1
        assert s.one("SELECT state FROM replacement_requests WHERE user_id=2")["state"] == "pending"
        posted = bot.notify_once.call_args.args[1]
        assert "<@2>" in posted and "answered by text" in posted and "||" in posted
        await bot.close()

    run(go())


def test_reply_ends_the_automatic_escalation(world):
    from mizan.escalation import enqueue_auto, process

    s, c, sid = world

    async def go():
        bot = bot_for(s)
        bot.wuphf.send = AsyncMock()
        bot.notify = AsyncMock()
        enqueue_auto(bot, s.session(sid), 1)
        c[0] += timedelta(hours=2)
        await process(bot)
        await inbound.handle(bot, PHONES[1], "yes")
        c[0] += timedelta(hours=2)
        await process(bot)
        assert bot.wuphf.send.await_count == 1
        assert s.one("SELECT state FROM wuphf_jobs")["state"] == "answered"
        await bot.close()

    run(go())


def test_here_checks_in_and_clears_the_wuphf_card(world):
    s, c, sid = world

    async def go():
        bot = bot_for(s)
        message = SimpleNamespace(edit=AsyncMock())
        bot.channel = AsyncMock(return_value=SimpleNamespace(fetch_message=AsyncMock(return_value=message)))
        with s.db:
            s.db.execute(
                "INSERT INTO wuphf_jobs(user_id,actor,source,created_at,sms_due,call_delay,message_id,state) "
                "VALUES(1,3,'manual',?,?,60,99,'waiting_call')",
                (stamp(c[0]), stamp(c[0])),
            )
        assert "Checked in" in await inbound.handle(bot, PHONES[1], "here")
        assert s.one("SELECT state FROM wuphf_jobs")["state"] == "acknowledged"
        assert "checked in by text" in message.edit.call_args.kwargs["content"]
        assert "Nobody is looking" in await inbound.handle(bot, PHONES[1], "here")
        await bot.close()

    run(go())


def test_stop_start_unknown_numbers_and_gibberish(world):
    s, c, sid = world

    async def go():
        bot = bot_for(s)
        assert "not on the class roster" in await inbound.handle(bot, "+15555559999", "yes")
        assert "not on the class roster" in await inbound.handle(bot, "", "yes")
        assert not s.rows("SELECT * FROM rsvps")
        assert await inbound.handle(bot, PHONES[1], "what is this") == inbound.HELP
        assert await inbound.handle(bot, PHONES[1], "") == inbound.HELP
        assert "stopped" in await inbound.handle(bot, PHONES[1], "STOP")
        assert s.get("nudges:1")["sms"] is False
        assert "resumed" in await inbound.handle(bot, PHONES[1], "start")
        assert s.get("nudges:1")["sms"] is True
        await bot.close()

    run(go())


def test_closed_class_reply_explains_instead_of_failing(world):
    s, c, sid = world

    async def go():
        bot = bot_for(s)
        s.cancel(sid, 1, "Class cancelled")
        assert "no open class" in await inbound.handle(bot, PHONES[1], "yes")
        bot.update_card.side_effect = RuntimeError("discord down")
        sid2 = s.create_class(c[0] + timedelta(days=9), 60, "IACC", "Arabic", 1)
        assert "Something failed" in await inbound.handle(bot, PHONES[1], "yes")
        assert sid2 and s.one("SELECT response FROM rsvps WHERE user_id=1")["response"] == "coming"
        await bot.close()

    run(go())


def test_webhook_requires_configuration_and_a_valid_signature(world, monkeypatch):
    s, c, sid = world

    async def go():
        bot = bot_for(s)
        app = web.Application()
        inbound.attach(bot, app)
        form = {"From": PHONES[1], "Body": "yes", "To": "+15555550100"}
        async with TestClient(TestServer(app)) as client:
            bad = await client.post("/twilio/sms", data=form, headers={"X-Twilio-Signature": "forged"})
            assert bad.status == 403
            missing = await client.post("/twilio/sms", data=form)
            assert missing.status == 403
            assert not s.rows("SELECT * FROM rsvps")
            good = await client.post(
                "/twilio/sms", data=form, headers={"X-Twilio-Signature": inbound.signature(URL, form, "private")}
            )
            assert good.status == 200 and good.content_type == "text/xml"
            assert "<Message>Saved: you&apos;re coming" in (await good.text()).replace("'", "&apos;")
            assert s.one("SELECT response FROM rsvps WHERE user_id=1")["response"] == "coming"
            monkeypatch.delenv("TWILIO_WEBHOOK_URL")
            off = await client.post(
                "/twilio/sms", data=form, headers={"X-Twilio-Signature": inbound.signature(URL, form, "private")}
            )
            assert off.status == 404
        await bot.close()

    run(go())
