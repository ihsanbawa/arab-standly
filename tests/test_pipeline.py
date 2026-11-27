"""Regressions for the Nov 25 duplicate invite and notification burst."""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from mizan.bot import MizanBot
from mizan.core import Store, RuleError, parse_local, stamp
from mizan.escalation import enqueue_auto, process
from mizan.inbound import open_class
from mizan.prayer import ensure_weekly


@pytest.fixture
def world(tmp_path,monkeypatch):
    monkeypatch.delenv('DISCORD_GUILD_ID',raising=False)
    monkeypatch.setenv('BEEMINDER_ENABLED','false')
    clock=[parse_local('2026-11-24 22:31','America/Chicago')]
    s=Store(tmp_path/'pipeline.sqlite',clock=lambda:clock[0])
    s.set('guild_id',123);s.set('channel_id',456);s.set('panel_message_id',1)
    for uid in range(1,5): s.member(uid,f'Member {uid}')
    yield s,clock
    s.close()


def create(s,day,actor=1):
    return s.create_class(parse_local(day,s.tz),60,'IACC','Arabic',actor)


def fake_bot(s):
    bot=MizanBot(s)
    bot.channel=AsyncMock(return_value=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=100))))
    bot.update_card=AsyncMock();bot.update_poll=AsyncMock()
    bot.notify=AsyncMock(return_value=SimpleNamespace(id=100))
    bot.wuphf=SimpleNamespace(phone=lambda uid:'+15555550101',preferences=lambda uid:{'sms':True},check=lambda *a:None,send=AsyncMock())
    bot.get_user=lambda uid:SimpleNamespace(send=AsyncMock())
    return bot


def test_manual_december_plan_blocks_november_auto_invite_across_restart(world):
    s,c=world
    manual=create(s,'2026-12-04 16:00')
    c[0]=parse_local('2026-11-25 18:00',s.tz)
    assert ensure_weekly(s)==manual
    path=s.db.execute('PRAGMA database_list').fetchone()[2]
    reopened=Store(path,clock=lambda:c[0])
    assert ensure_weekly(reopened)==manual
    assert len(reopened.rows('SELECT * FROM sessions'))==1
    reopened.close()


def test_legacy_wrong_auto_invite_is_ignored_for_reminders_sms_and_replies(world):
    s,c=world
    manual=create(s,'2026-12-04 16:00')
    c[0]=parse_local('2026-11-25 18:00',s.tz)
    wrong=create(s,'2026-11-27 16:00',actor=0)
    async def run():
        bot=fake_bot(s)
        for uid in range(1,5): enqueue_auto(bot,s.session(wrong),uid)
        # Emulate the database before this version introduced a focus setting.
        with s.db: s.db.execute("DELETE FROM settings WHERE key='active_class_id'")
        c[0]=parse_local('2026-11-26 20:00',s.tz)
        await bot.tick();await bot.tick()
        assert s.active_class()['id']==manual
        assert open_class(s,1)['id']==manual
        assert len(s.rows('SELECT * FROM sessions'))==2
        bot.wuphf.send.assert_not_called()
        assert all(j['state']=='stopped' for j in s.rows('SELECT * FROM wuphf_jobs'))
        assert 'Not current' in bot.card(wrong).title
        assert not [x for x in bot.notify.call_args_list if '#'+str(wrong) in x.args[0]]
        await bot.close()
    asyncio.run(run())


def test_late_invite_has_a_day_to_answer_and_legacy_jobs_obey_grace(world):
    s,c=world;c[0]=parse_local('2026-11-25 18:00',s.tz)
    sid=create(s,'2026-11-27 16:00')
    assert s.session(sid)['rsvp_due']==stamp(c[0]+timedelta(hours=24))
    async def run():
        bot=fake_bot(s)
        # Simulate the old one-minute deadline and a text already queued for 20:01.
        with s.db: s.db.execute('UPDATE sessions SET rsvp_due=? WHERE id=?',(stamp(c[0]+timedelta(minutes=1)),sid))
        enqueue_auto(bot,s.session(sid),1)
        c[0]+=timedelta(hours=3)
        await process(bot)
        bot.wuphf.send.assert_not_called()
        c[0]=parse_local('2026-11-26 18:00',s.tz)
        await process(bot);await process(bot)
        assert bot.wuphf.send.await_count==1
        assert s.one('SELECT state FROM wuphf_jobs')['state']=='done'
        c[0]+=timedelta(hours=2)
        await process(bot)
        assert bot.wuphf.send.await_count==1
        bot.notify.assert_not_called()
        await bot.close()
    asyncio.run(run())


def test_conflict_stops_original_rsvp_texts_and_start_messages(world):
    s,c=world;sid=create(s,'2026-11-27 16:00')
    async def run():
        bot=fake_bot(s)
        for uid in range(1,5): enqueue_auto(bot,s.session(sid),uid)
        s.answer(sid,1,False,0)
        c[0]=parse_local('2026-11-27 15:00',s.tz)
        await bot.tick();await bot.tick()
        bot.wuphf.send.assert_not_called()
        assert 'Needs a new time' in bot.card(sid).title
        assert not [x for x in bot.notify.call_args_list if 'Class starts' in x.args[0] or 'conflict(s)' in x.args[0]]
        await bot.close()
    asyncio.run(run())


def test_only_all_four_confirmed_get_one_start_reminder(world):
    s,c=world;sid=create(s,'2026-11-27 16:00')
    for uid in range(1,4): s.answer(sid,uid,True,0)
    async def run():
        bot=fake_bot(s);c[0]=parse_local('2026-11-27 15:00',s.tz)
        await bot.tick()
        assert not [x for x in bot.notify.call_args_list if 'Class starts' in x.args[0]]
        s.answer(sid,4,True,0)
        await bot.tick();await bot.tick()
        assert len([x for x in bot.notify.call_args_list if 'Class starts' in x.args[0]])==1
        await bot.close()
    asyncio.run(run())


def test_unfinished_class_blocks_next_week_and_recap_does_not_repeat_daily(world):
    s,c=world;sid=create(s,'2026-11-27 16:00')
    async def run():
        bot=fake_bot(s)
        c[0]=parse_local('2026-12-02 18:00',s.tz)
        await bot.tick();await bot.tick()
        c[0]+=timedelta(days=1);await bot.tick()
        assert len(s.rows('SELECT * FROM sessions'))==1
        assert len([x for x in bot.notify.call_args_list if 'did class' in x.args[0]])==1
        s.cancel(sid,1,'Not held')
        assert ensure_weekly(s)==sid
        await bot.close()
    asyncio.run(run())


def test_skip_stops_jobs_preserves_scores_and_does_not_resurrect_old_plans(world):
    s,c=world
    old=create(s,'2026-12-04 16:00')
    sid=create(s,'2026-11-27 16:00')
    s.answer(sid,1,False,0)
    before=s.rows('SELECT * FROM ledger')
    async def run():
        bot=fake_bot(s);enqueue_auto(bot,s.session(sid),2)
        s.skip_class(sid,1)
        c[0]=parse_local('2026-11-25 18:00',s.tz)
        assert ensure_weekly(s) is None
        assert s.active_class() is None
        assert s.session(sid)['status']=='skipped'
        assert s.rows('SELECT * FROM ledger')==before
        assert s.one('SELECT state FROM replacement_requests')['state']=='resolved'
        assert s.one('SELECT state FROM wuphf_jobs')['state']=='stopped'
        with pytest.raises(RuleError): s.require_active(old)
        await bot.close()
    asyncio.run(run())


def test_skip_inactive_legacy_duplicate_keeps_manual_plan(world):
    s,c=world;manual=create(s,'2026-12-04 16:00');wrong=create(s,'2026-11-27 16:00',actor=0)
    with s.db: s.db.execute("DELETE FROM settings WHERE key='active_class_id'")
    s.skip_class(wrong,1)
    assert s.active_class()['id']==manual


def test_blocked_dms_produce_one_group_fallback_not_four_public_posts(world):
    s,c=world;sid=create(s,'2026-11-27 16:00')
    async def run():
        bot=fake_bot(s)
        error=discord.HTTPException(SimpleNamespace(status=403,reason='Forbidden'),'DM blocked')
        bot.get_user=lambda uid:SimpleNamespace(send=AsyncMock(side_effect=error))
        c[0]=parse_local('2026-11-26 16:00',s.tz)
        await bot.tick();await bot.tick()
        fallbacks=[x.args[0] for x in bot.notify.call_args_list if 'RSVP still needed' in x.args[0]]
        assert len(fallbacks)==1 and all(f'<@{u}>' in fallbacks[0] for u in range(1,5))
        assert len(s.rows('SELECT * FROM wuphf_jobs'))==4
        await bot.close()
    asyncio.run(run())


def test_pause_stops_automatic_jobs_and_invites(world):
    s,c=world;sid=create(s,'2026-11-27 16:00')
    async def run():
        bot=fake_bot(s);enqueue_auto(bot,s.session(sid),1)
        s.set('automation_paused',True)
        c[0]=parse_local('2026-11-26 18:00',s.tz)
        await bot.tick()
        bot.wuphf.send.assert_not_called();bot.notify.assert_not_called()
        assert s.one('SELECT state FROM wuphf_jobs')['state']=='answered'
        assert ensure_weekly(s) is None
        await bot.close()
    asyncio.run(run())


def test_old_sms_yes_does_not_accept_a_different_plan(world):
    from mizan.inbound import rsvp
    s,c=world;old=create(s,'2026-11-27 16:00')
    async def run():
        bot=fake_bot(s)
        jid=enqueue_auto(bot,s.session(old),1)
        with s.db: s.db.execute("UPDATE wuphf_jobs SET sms_sent=?,state='done' WHERE id=?",(stamp(c[0]),jid))
        current=create(s,'2026-12-04 16:00')
        response=await rsvp(bot,1,True)
        assert 'no RSVP was changed' in response
        assert not s.responses(current)
        await bot.close()
    asyncio.run(run())


def test_held_class_waits_for_recap_before_pipeline_advances(world):
    s,c=world;sid=create(s,'2026-11-27 16:00')
    for uid in range(1,5): s.answer(sid,uid,True,0)
    c[0]=parse_local('2026-12-02 18:00',s.tz)
    s.held(sid,1)
    assert ensure_weekly(s)==sid
    assert len(s.rows('SELECT * FROM sessions'))==1
    s.save_lesson(sid,1,'Covered chapter 3','Chapter 4','Bring books')
    assert ensure_weekly(s)!=sid
    assert len(s.rows('SELECT * FROM sessions'))==2


def test_short_notice_class_never_sends_overdue_messages_after_one_minute(world):
    s,c=world;start=c[0]+timedelta(hours=3)
    sid=s.create_class(start,60,'IACC','Arabic',1)
    async def run():
        bot=fake_bot(s)
        c[0]+=timedelta(hours=2)
        await bot.tick()
        bot.wuphf.send.assert_not_called()
        assert not s.rows('SELECT * FROM wuphf_jobs')
        assert not [x for x in bot.notify.call_args_list if 'RSVP still needed' in x.args[0] or 'Class starts' in x.args[0]]
        await bot.close()
    asyncio.run(run())


def test_skip_control_is_confirmed_and_ta_only(world):
    from mizan.ui import class_action
    s,c=world;sid=create(s,'2026-11-27 16:00')
    def interaction(ta=True):
        return SimpleNamespace(guild_id=123,user=SimpleNamespace(id=1,roles=[]),permissions=SimpleNamespace(manage_guild=ta),
            response=SimpleNamespace(send_message=AsyncMock(),defer=AsyncMock(),is_done=lambda:False),followup=SimpleNamespace(send=AsyncMock()))
    async def run():
        bot=fake_bot(s);i=interaction()
        await class_action(bot,i,'skip',sid)
        view=i.response.send_message.call_args.kwargs['view']
        assert s.session(sid)['status']=='scheduled'
        with pytest.raises(RuleError,match='Only a TA'):
            await view.children[0].callback(interaction(False))
        await view.children[0].callback(interaction())
        assert s.session(sid)['status']=='skipped'
        assert s.active_class() is None
        await bot.close()
    asyncio.run(run())
