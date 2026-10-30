"""Exercise user flows, permissions, calendar changes and automatic invitation deduplication."""
import asyncio
import inspect
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import discord
import pytest
from mizan import ui
from mizan.bot import ClassView, MizanBot
from mizan.core import RuleError, Store, parse_local, stamp
from mizan.prayer import defaults, ensure_weekly, fajr_slot, upcoming


@pytest.fixture
def state(tmp_path,monkeypatch):
    clock=[parse_local('2026-10-30 09:00','America/Chicago')]
    store=Store(tmp_path/'test.sqlite',clock=lambda:clock[0])
    store.set('guild_id',123)
    store.set('channel_id',456)
    monkeypatch.delenv('DISCORD_GUILD_ID',raising=False)
    monkeypatch.setenv('BEEMINDER_ENABLED','false')
    yield store,clock
    store.close()


def interaction(uid=1,ta=True):
    return SimpleNamespace(guild_id=123,user=SimpleNamespace(id=uid,display_name=f'Member {uid}',roles=[]),
        permissions=SimpleNamespace(manage_guild=ta),
        response=SimpleNamespace(send_message=AsyncMock(),send_modal=AsyncMock(),defer=AsyncMock(),is_done=lambda:False),
        followup=SimpleNamespace(send=AsyncMock()))


def four(store):
    for uid in range(1,5): store.member(uid,f'Member {uid}')


def test_iqamah_not_adhan_and_fall_clock_change(state):
    s,c=state
    first=defaults(s)['start'].astimezone(ZoneInfo(s.tz))
    assert first.strftime('%Y-%m-%d %H:%M %Z')=='2026-10-31 07:00 CDT'
    c[0]=parse_local('2026-11-02 09:00',s.tz)
    second=defaults(s)['start'].astimezone(ZoneInfo(s.tz))
    assert second.strftime('%Y-%m-%d %H:%M %Z')=='2026-11-07 06:15 CST'
    assert fajr_slot(date(2026,11,21)).astimezone(ZoneInfo(s.tz)).strftime('%H:%M')=='06:30'
    assert first.utcoffset()!=second.utcoffset()
    assert defaults(s)['duration']==60


def test_calendar_expiry_never_reuses_prior_year(state):
    s,c=state
    c[0]=parse_local('2027-01-01 09:00',s.tz)
    with pytest.raises(RuleError,match='No previous-year'):
        upcoming(s)


def test_offset_and_next_saturday_after_class(state):
    s,c=state
    s.set('fajr_offset',30)
    assert defaults(s)['start'].astimezone(ZoneInfo(s.tz)).strftime('%H:%M')=='07:15'
    c[0]=parse_local('2026-10-31 08:00',s.tz)
    assert defaults(s)['start'].astimezone(ZoneInfo(s.tz)).date()==date(2026,11,7)


def test_weekly_invitation_requires_four_and_is_durable_after_cancel(state):
    s,c=state
    assert ensure_weekly(s) is None
    four(s)
    sid=ensure_weekly(s)
    assert ensure_weekly(s)==sid
    s.cancel(sid,1,'Cancelled')
    assert ensure_weekly(s)==sid
    assert len(s.rows('SELECT * FROM sessions'))==1
    s.set('weekly_invites',False)
    assert ensure_weekly(s) is None


def test_manual_weekend_and_moved_session_do_not_duplicate(state):
    s,c=state;four(s)
    sid=s.create_class(parse_local('2026-11-01 07:00',s.tz),60,'IACC','Arabic',1)
    assert ensure_weekly(s)==sid
    assert len(s.rows('SELECT * FROM sessions'))==1


def test_all_slash_commands_have_no_required_arguments(state):
    async def run():
        bot=MizanBot(state[0])
        for command in bot.tree.get_commands():
            assert all(not p.required for p in command.parameters), command.name
        assert len(ui.Panel(bot).children)==9
        assert ui.Panel(bot).is_persistent()
        await bot.close()
    asyncio.run(run())


def test_preview_no_write_until_confirm_and_reject_incomplete_roster(state):
    async def run():
        s,c=state;bot=MizanBot(s)
        i=interaction()
        await ui.schedule_preview(bot,i)
        view=i.response.send_message.call_args.kwargs['view']
        assert not s.rows('SELECT * FROM sessions')
        with pytest.raises(RuleError,match='four class members'):
            await view.children[0].callback(interaction())
        four(s); bot.update_card=AsyncMock()
        await view.children[0].callback(interaction())
        assert len(s.rows('SELECT * FROM sessions'))==1
        assert s.session(1)['start_at']==stamp(defaults(s)['start'])
        # Duplicate click cannot create a second invitation.
        with pytest.raises(RuleError,match='already scheduled'):
            await view.children[0].callback(interaction())
        await bot.close()
    asyncio.run(run())


def test_non_ta_cannot_use_schedule_or_confirmation(state):
    async def run():
        s,c=state;bot=MizanBot(s)
        with pytest.raises(RuleError,match='Only a TA'):
            await ui.schedule_preview(bot,interaction(ta=False))
        apply=AsyncMock()
        i=interaction()
        await ui.confirm(bot,i,'Confirm change?',apply)
        view=i.response.send_message.call_args.kwargs['view']
        with pytest.raises(RuleError,match='Only a TA'):
            await view.children[0].callback(interaction(ta=False))
        apply.assert_not_called()
        await bot.close()
    asyncio.run(run())


def test_stale_controls_cannot_change_revised_class(state):
    async def run():
        s,c=state;four(s);bot=MizanBot(s);sid=ensure_weekly(s)
        i=interaction();apply=AsyncMock()
        await ui.confirm(bot,i,'Held?',apply,sid)
        view=i.response.send_message.call_args.kwargs['view']
        with s.db: s.db.execute('UPDATE sessions SET revision=revision+1 WHERE id=?',(sid,))
        with pytest.raises(RuleError,match='changed'):
            await view.children[0].callback(interaction())
        apply.assert_not_called()
        await bot.close()
    asyncio.run(run())


def test_preferences_preserve_other_explicit_choices(state):
    async def run():
        s,c=state;four(s);bot=MizanBot(s)
        bot.wuphf.consent(1,True,False,True)
        await bot.tree.get_command('nudges').callback(interaction(),calls=True)
        assert s.get('nudges:1')==dict(sms=True,calls=True,early=True)
        i=interaction();await ui.nudge_preferences(bot,i)
        view=i.response.send_message.call_args.kwargs['view']
        await view.children[0].callback(interaction())
        assert s.get('nudges:1')==dict(sms=False,calls=True,early=True)
        await bot.close()
    asyncio.run(run())


def test_panel_and_automatic_announcement_recover_and_do_not_repeat(state):
    async def run():
        s,c=state;four(s);bot=MizanBot(s)
        channel=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=987)))
        bot.channel=AsyncMock(return_value=channel)
        bot.notify=AsyncMock(return_value=SimpleNamespace(id=988))
        async def card(sid,create=False):
            with s.db: s.db.execute('UPDATE sessions SET message_id=999 WHERE id=?',(sid,))
        bot.update_card=AsyncMock(side_effect=card)
        await bot.tick();await bot.tick()
        assert len(s.rows('SELECT * FROM sessions'))==1
        assert bot.update_card.call_count==2
        assert channel.send.call_count==1
        # Recover an unannounced, persisted regular class without making a new class.
        with s.db: s.db.execute('UPDATE sessions SET message_id=NULL')
        await bot.tick()
        assert bot.update_card.call_count==3
        assert len(s.rows('SELECT * FROM sessions'))==1
        assert s.monthly_count('2026-10')==0
        await bot.close()
    asyncio.run(run())
