"""Exercise provider boundaries without credentials, texts or real charges."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from mizan.beeminder import Beeminder
from mizan.bot import MizanBot
from mizan.core import RuleError, Store, parse_local
from mizan.wuphf import Wuphf


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    clock = [parse_local("2026-10-31 23:51", "America/Chicago")]
    store = Store(tmp_path / "db.sqlite", clock=lambda: clock[0])
    store.set("guild_id", 123)
    store.set("channel_id", 456)
    for uid in range(1, 5):
        store.member(uid, f"Member {uid}")
    monkeypatch.setenv("BEEMINDER_ENABLED", "true")
    monkeypatch.setenv("BEEMINDER_START_MONTH", "2026-10")
    monkeypatch.setenv(
        "BEEMINDER_BINDINGS_JSON",
        json.dumps(
            [
                {
                    "discord_id": i,
                    "username": f"user{i}",
                    "goal": "arabic",
                    "token_env": f"BEE_{i}",
                    "contract_verified": True,
                }
                for i in range(1, 5)
            ]
        ),
    )
    yield store, clock
    store.close()


def valid_goal(store):
    t = store.clock().timestamp()
    return dict(
        goal_type="drinker",
        kyoom=True,
        currate=0,
        curval=0,
        fullroad=[[t - 1000, 0, 0], [t + 1000, 0, 0]],
        deadline=0,
        aggday="sum",
        pledge=5,
    )


def test_all_group_members_receive_same_result_and_retry_reconciles(fixture):
    s, c = fixture
    b = Beeminder(s)
    remote = {}
    posts = []
    fail_once = {"user1"}

    async def fake(client, binding, method, path, data=None, user_resource=False):
        name = binding["username"]
        if user_resource:
            return {"timezone": "America/Chicago"}
        if method == "GET" and path == ".json":
            return valid_goal(s)
        if method == "GET":
            return list(remote.get(name, {}).values())
        if method == "POST":
            posts.append(name)
            point = {"id": name + "-point", "requestid": data["requestid"], "value": data["value"]}
            remote.setdefault(name, {})[data["requestid"]] = point
            if name in fail_once:
                fail_once.remove(name)
                raise TimeoutError("Ambiguous response")
            return point
        raise AssertionError("Unexpected financial operation")

    b.request = fake

    async def run():
        await b.sync(None, "2026-10")
        assert s.one("SELECT state FROM deliveries WHERE binding='user1/arabic'")["state"] == "failed"
        await b.sync(None, "2026-10")
        assert all(x["state"] == "sent" for x in s.rows("SELECT * FROM deliveries"))
        assert posts == ["user1", "user2", "user3", "user4"]
        assert all(list(points.values())[0]["value"] == 1 for points in remote.values())

    asyncio.run(run())


@pytest.mark.parametrize(
    "bad",
    [
        {"goal_type": "hustler"},
        {"currate": 2},
        {"deadline": 3600},
        {"aggday": "max"},
        {"pledge": 270},
        {"fullroad": None},
        {"curval": -1},
    ],
)
def test_financial_misconfiguration_blocks_every_write(fixture, bad):
    s, c = fixture
    b = Beeminder(s)
    posts = []

    async def fake(client, binding, method, path, data=None, user_resource=False):
        if method == "POST":
            posts.append(data)
            raise AssertionError("Must not write")
        if user_resource:
            return {"timezone": "America/Chicago"}
        if path == ".json":
            return {**valid_goal(s), **bad}
        return []

    b.request = fake
    asyncio.run(b.sync(None, "2026-10"))
    assert not posts
    assert all(x["state"] == "failed" for x in s.rows("SELECT * FROM deliveries"))


def test_group_hiatus_does_not_remove_financial_responsibility(fixture):
    s, c = fixture
    b = Beeminder(s)
    s.member(4, "Member 4", False)
    assert b.configured()
    b.prepare("2026-10")
    assert len(s.rows("SELECT * FROM deliveries")) == 4


def test_no_financial_failure_posted_after_midnight(fixture):
    s, c = fixture
    b = Beeminder(s)
    posts = []

    async def fake(client, binding, method, path, data=None, user_resource=False):
        if method == "POST":
            posts.append(data)
            raise AssertionError("Must not post after midnight")
        if user_resource:
            c[0] = parse_local("2026-11-01 00:01", s.tz)
            return {"timezone": "America/Chicago"}
        if path == ".json":
            return valid_goal(s)
        return []

    b.request = fake
    asyncio.run(b.sync(None, "2026-10"))
    assert not posts


def test_waiving_absence_retains_move_penalty(fixture):
    s, c = fixture
    c[0] = parse_local("2026-10-01 09:00", s.tz)
    sid = s.create_class(c[0] + timedelta(days=3), 60, "IACC", "Arabic", 1)
    with s.transaction():
        s.penalty(sid, 1, -1, "Moved class after accepting", 1)
    s.set_attendance(sid, 1, "absent", 1)
    assert s.standings("2026-10")[-1]["points"] == -2
    s.set_attendance(sid, 1, "excused", 1)
    assert next(x["points"] for x in s.standings("2026-10") if x["user_id"] == 1) == -1
    s.waive(sid, 1, 1, "Emergency")
    assert all(x["points"] == 0 for x in s.standings("2026-10"))


def test_scheduler_does_not_repeat_reminders_or_award_from_rsvp(fixture, monkeypatch):
    s, c = fixture
    monkeypatch.setenv("BEEMINDER_ENABLED", "false")
    c[0] = parse_local("2026-10-02 09:00", s.tz)
    sid = s.create_class(c[0] + timedelta(hours=23), 60, "IACC", "Review", 1)
    for uid in range(1, 5):
        s.rsvp(sid, uid, "coming")

    async def run():
        bot = MizanBot(s)
        s.set("panel_message_id", 1)
        s.set("weekly_invites", False)
        messages = []

        async def notify(text, **kwargs):
            messages.append(text)
            return SimpleNamespace(id=1)

        bot.notify = notify
        await bot.tick()
        await bot.tick()
        assert len([x for x in messages if "Class within 24 hours" in x]) == 1
        assert s.monthly_count("2026-10") == 0
        c[0] += timedelta(hours=25)
        await bot.tick()
        await bot.tick()
        assert len([x for x in messages if "did class" in x]) == 1
        assert s.monthly_count("2026-10") == 0
        await bot.close()

    asyncio.run(run())


def test_wuphf_uses_real_class_text_and_no_retry_after_timeout(fixture, monkeypatch):
    s, c = fixture
    c[0] = parse_local("2026-10-03 06:00", s.tz)
    for key, value in {
        "TWILIO_ACCOUNT_SID": "ACtest",
        "TWILIO_AUTH_TOKEN": "private",
        "TWILIO_PHONE_NUMBER": "+15555550100",
        "PHONE_A": "+15555550101",
        "WUPHF_CONTACTS_JSON": '{"1":"PHONE_A"}',
    }.items():
        monkeypatch.setenv(key, value)
    w = Wuphf(s)
    w.consent(1, True, True, True)

    class FakeContext:
        async def __aenter__(self):
            raise TimeoutError()

        async def __aexit__(self, *args):
            return None

    class Client:
        count = 0

        def post(self, *args, **kwargs):
            self.count += 1
            return FakeContext()

    client = Client()

    async def run():
        with pytest.raises(RuleError, match="uncertain"):
            await w.send(client, 1, "sms", "Class at IACC after Fajr.")
        with pytest.raises(RuleError, match="hour"):
            await w.send(client, 1, "sms", "Class at IACC after Fajr.")
        assert client.count == 1

    asyncio.run(run())
