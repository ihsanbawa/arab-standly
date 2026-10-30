import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from mizan.beeminder import Beeminder
from mizan.core import RuleError, Store, parse_local, stamp
from mizan.wuphf import Wuphf

UTC = timezone.utc


@pytest.fixture
def world(tmp_path):
    clock = [datetime(2026, 10, 1, 12, tzinfo=UTC)]
    s = Store(tmp_path / "test.sqlite", clock=lambda: clock[0])
    s.set("guild_id", 123)
    for uid, name in enumerate(["A", "B", "C", "D"], 1):
        s.member(uid, name)
    yield s, clock
    s.close()


def create(s, day=3):
    return s.create_class(datetime(2026, 10, day, 12, tzinfo=UTC), 60, "IACC", "Arabic", 1)


def accept_all(s, sid):
    for uid in range(1, 5):
        s.rsvp(sid, uid, "coming")


def test_rsvp_does_not_award_attendance(world):
    s, c = world
    sid = create(s)
    accept_all(s, sid)
    assert s.monthly_count("2026-10") == 0
    assert all(x["points"] == 0 for x in s.standings("2026-10"))


def test_confirmation_is_idempotent_and_group_count(world):
    s, c = world
    sid = create(s)
    accept_all(s, sid)
    c[0] = datetime(2026, 10, 3, 14, tzinfo=UTC)
    assert s.held(sid, 1)
    assert not s.held(sid, 1)
    assert s.monthly_count("2026-10") == 1
    assert [x["points"] for x in s.standings("2026-10")] == [1] * 4


def test_unanswered_people_not_assumed_present(world):
    s, c = world
    sid = create(s)
    s.rsvp(sid, 1, "coming")
    c[0] = datetime(2026, 10, 3, 14, tzinfo=UTC)
    with pytest.raises(RuleError, match="Not everyone"):
        s.held(sid, 1)
    for uid in range(2, 5):
        s.set_attendance(sid, uid, "excused", 1)
    s.held(sid, 1)
    assert {r["user_id"]: r["points"] for r in s.standings("2026-10")} == {1: 1, 2: 0, 3: 0, 4: 0}


def test_no_confirmation_before_end(world):
    s, c = world
    sid = create(s)
    accept_all(s, sid)
    with pytest.raises(RuleError):
        s.held(sid, 1)


def test_absence_correction_and_waiver(world):
    s, c = world
    sid = create(s)
    accept_all(s, sid)
    c[0] = datetime(2026, 10, 3, 14, tzinfo=UTC)
    s.set_attendance(sid, 4, "absent", 1)
    s.held(sid, 1)
    assert next(r["points"] for r in s.standings("2026-10") if r["user_id"] == 4) == -2
    s.set_attendance(sid, 4, "excused", 1, "Emergency")
    assert next(r["points"] for r in s.standings("2026-10") if r["user_id"] == 4) == 0
    assert s.monthly_count("2026-10") == 0  # All four must be present for monthly credit.


def test_poll_does_not_cancel_and_can_select_multiple(world):
    s, c = world
    sid = create(s)
    pid = s.open_poll(
        sid, [stamp(c[0] + timedelta(days=5)), stamp(c[0] + timedelta(days=6))], c[0] + timedelta(days=1), 1
    )
    s.vote(pid, 1, 0)
    s.vote(pid, 1, 1)
    assert len(s.rows("SELECT * FROM votes")) == 2
    s.vote(pid, 1, 0)
    assert len(s.rows("SELECT * FROM votes")) == 1
    assert s.session(sid)["status"] == "scheduled"


def test_closed_poll_cannot_vote(world):
    s, c = world
    sid = create(s)
    pid = s.open_poll(
        sid, [stamp(c[0] + timedelta(days=5)), stamp(c[0] + timedelta(days=6))], c[0] + timedelta(hours=1), 1
    )
    c[0] += timedelta(hours=2)
    with pytest.raises(RuleError):
        s.vote(pid, 1, 0)


def test_move_preserves_commitment_even_after_withdrawal(world):
    s, c = world
    sid = create(s, day=10)
    accept_all(s, sid)
    s.rsvp(sid, 1, "unavailable")
    pid = s.open_poll(
        sid, [stamp(c[0] + timedelta(days=12)), stamp(c[0] + timedelta(days=13))], c[0] + timedelta(days=2), 1
    )
    for uid in range(1,5): s.vote(pid,uid,0)
    s.confirm_slot(pid, 0, 1, responsible=1)
    assert s.responses(sid) == []
    assert s.session(sid)["revision"] == 1
    assert next(r["points"] for r in s.standings("2026-10") if r["user_id"] == 1) == -1
    with pytest.raises(RuleError):
        s.rsvp(sid, 1, "coming", revision=0)
    with pytest.raises(RuleError):
        s.confirm_slot(pid, 0, 1, responsible=1)


def test_late_withdrawal_is_minus_two(world):
    s, c = world
    sid = create(s)
    accept_all(s, sid)
    c[0] = datetime(2026, 10, 3, 0, tzinfo=UTC)
    s.rsvp(sid, 2, "change")
    pid = s.open_poll(
        sid, [stamp(c[0] + timedelta(days=2)), stamp(c[0] + timedelta(days=3))], c[0] + timedelta(hours=4), 1
    )
    for uid in range(1,5): s.vote(pid,uid,0)
    s.confirm_slot(pid, 0, 1, responsible=2)
    assert next(r["points"] for r in s.standings("2026-10") if r["user_id"] == 2) == -2


def test_reporter_not_punished_and_penalties_dont_stack(world):
    s, c = world
    sid = create(s)
    s.disruption(sid, 2, "Someone is missing")
    assert s.rows("SELECT * FROM ledger") == []
    s.cancel(sid, 1, "No class", responsible=3)
    s.cancel(sid, 1, "Duplicate report", responsible=3)
    assert len(s.rows("SELECT * FROM ledger")) == 1
    assert next(r["points"] for r in s.standings("2026-10") if r["user_id"] == 3) == -2


def test_monthly_reset_is_query_not_data_deletion(world):
    s, c = world
    sid = create(s)
    accept_all(s, sid)
    c[0] = datetime(2026, 10, 3, 14, tzinfo=UTC)
    s.held(sid, 1)
    assert all(x["points"] == 0 for x in s.standings("2026-11"))
    assert all(x["points"] == 1 for x in s.standings("2026-10"))


def test_hiatus_affects_future_roster_only(world):
    s, c = world
    sid = create(s)
    s.member(4, "D", False)
    later = create(s, 10)
    assert len(s.participants(sid)) == 4
    assert len(s.participants(later)) == 3


def test_reopen_removes_credit(world):
    s, c = world
    sid = create(s)
    accept_all(s, sid)
    c[0] = datetime(2026, 10, 3, 14, tzinfo=UTC)
    s.held(sid, 1)
    s.reopen(sid, 1)
    assert s.monthly_count("2026-10") == 0
    assert s.rows("SELECT * FROM ledger WHERE event_key LIKE 'attend:%'") == []


def test_database_persists_views_votes_scores(world, tmp_path):
    s, c = world
    sid = create(s)
    accept_all(s, sid)
    path = tmp_path / "persist.sqlite"
    disk = Store(path, clock=lambda: c[0])
    disk.member(1, "A")
    ds = disk.create_class(c[0] + timedelta(days=1), 60, "IACC", "Arabic", 1)
    disk.rsvp(ds, 1, "coming")
    disk.close()
    reopened = Store(path, clock=lambda: c[0])
    assert reopened.responses(ds)[0]["response"] == "coming"
    reopened.close()


def test_dst_rejects_ambiguous_or_nonexistent_times():
    for date in ["2026-11-01 01:30", "2026-03-08 02:30"]:
        with pytest.raises(RuleError):
            parse_local(date, "America/Chicago")
    assert parse_local("2026-11-07 06:30", "America/Chicago").hour == 12


def test_outsider_and_duplicate_class_rejected(world):
    s, c = world
    sid = create(s)
    with pytest.raises(RuleError):
        s.rsvp(sid, 999, "coming")
    with pytest.raises(RuleError):
        create(s)


def configure_bee(monkeypatch):
    monkeypatch.setenv("BEEMINDER_ENABLED", "true")
    monkeypatch.setenv("BEEMINDER_START_MONTH", "2026-10")
    monkeypatch.setenv(
        "BEEMINDER_BINDINGS_JSON",
        json.dumps(
            [
                {
                    "discord_id": i + 1,
                    "username": f"p{i}",
                    "goal": "arabic-monthly",
                    "token_env": f"BEE_{i}",
                    "contract_verified": True,
                }
                for i in range(4)
            ]
        ),
    )


def test_month_end_group_result_exact_no_carryover(world, monkeypatch):
    s, c = world
    configure_bee(monkeypatch)
    for day in [3, 10, 17]:
        sid = create(s, day)
        accept_all(s, sid)
        c[0] = datetime(2026, 10, day, 14, tzinfo=UTC)
        s.held(sid, 1)
    c[0] = parse_local("2026-10-31 23:51", s.tz)
    b = Beeminder(s)
    b.prepare("2026-10")
    b.prepare("2026-10")
    assert len(s.rows("SELECT * FROM deliveries")) == 4
    assert {r["value"] for r in s.rows("SELECT * FROM deliveries")} == {0}
    assert s.monthly_count("2026-11") == 0
    with pytest.raises(RuleError, match="audit"):
        s.reopen(sid, 1)


def test_short_month_reports_failure_for_everyone(world, monkeypatch):
    s, c = world
    configure_bee(monkeypatch)
    c[0] = parse_local("2026-10-31 23:51", s.tz)
    Beeminder(s).prepare("2026-10")
    assert {r["value"] for r in s.rows("SELECT * FROM deliveries")} == {1}


def test_financial_audit_blocks_unconfirmed_and_backdating(world, monkeypatch):
    s, c = world
    configure_bee(monkeypatch)
    create(s)
    c[0] = parse_local("2026-10-31 23:51", s.tz)
    with pytest.raises(RuleError, match="confirmation"):
        Beeminder(s).prepare("2026-10")
    c[0] = parse_local("2026-11-01 00:01", s.tz)
    with pytest.raises(RuleError, match="final 10"):
        Beeminder(s).prepare("2026-10")


def test_wuphf_requires_opt_in_quiet_hours_and_cooldown(world, monkeypatch):
    s, c = world
    for k, v in {
        "TWILIO_ACCOUNT_SID": "ACtest",
        "TWILIO_AUTH_TOKEN": "private",
        "TWILIO_PHONE_NUMBER": "+15550000000",
        "PHONE_A": "+15550000001",
        "WUPHF_CONTACTS_JSON": '{"1":"PHONE_A"}',
    }.items():
        monkeypatch.setenv(k, v)
    w = Wuphf(s)
    with pytest.raises(RuleError, match="opted"):
        w.reserve(1, "sms", "Class")
    w.consent(1, True, False, False)
    c[0] = parse_local("2026-10-03 05:30", s.tz)
    with pytest.raises(RuleError, match="Quiet"):
        w.reserve(1, "sms", "Class")
    w.consent(1, True, False, True)
    assert w.reserve(1, "sms", "Class") == "+15550000001"
    with pytest.raises(RuleError, match="hour"):
        w.reserve(1, "sms", "Class")
    with pytest.raises(RuleError, match="opted"):
        w.reserve(1, "calls", "Class")
    w.consent(1, False, False, False)
    with pytest.raises(RuleError, match="opted"):
        w.reserve(1, "sms", "Class")


def test_discord_adapter_registers_persistent_components(world):
    from mizan.bot import ClassView, MizanBot, PollView

    async def run():
        s, c = world
        sid = create(s)
        bot = MizanBot(s)
        view = ClassView(bot, sid, 0)
        assert view.is_persistent()
        assert PollView(bot, 1, 3).is_persistent()
        assert {"setup", "schedule", "poll", "classheld", "mizan", "wuphf", "nudges"} <= {
            x.name for x in bot.tree.get_commands()
        }
        assert len(bot.card(sid).to_dict()["fields"]) > 0
        await bot.close()

    asyncio.run(run())


def test_ta_authorization(world, monkeypatch):
    from types import SimpleNamespace as NS

    from mizan.bot import MizanBot

    s, c = world
    monkeypatch.setenv("DISCORD_GUILD_ID", "123")
    bot = MizanBot(s)
    outsider = NS(guild_id=999, user=NS(id=1, roles=[]), permissions=NS(manage_guild=True))
    with pytest.raises(RuleError):
        bot.require_ta(outsider)
    member = NS(guild_id=123, user=NS(id=1, roles=[]), permissions=NS(manage_guild=False))
    with pytest.raises(RuleError):
        bot.require_ta(member)
    store_role = NS(id=55)
    s.set("ta_role_id", 55)
    member.user.roles = [store_role]
    bot.require_ta(member)
