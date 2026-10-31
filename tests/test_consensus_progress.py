import asyncio
import sqlite3
from datetime import datetime,timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mizan import consensus,progress
from mizan.core import Store,RuleError,parse_local,stamp
from mizan.bot import MizanBot
from mizan.testing import open_test


@pytest.fixture
def world(tmp_path,monkeypatch):
    monkeypatch.delenv('DISCORD_GUILD_ID',raising=False)
    monkeypatch.setenv('BEEMINDER_ENABLED','false')
    c=[parse_local('2026-10-01 12:00','America/Chicago')]
    s=Store(tmp_path/'persist.sqlite',clock=lambda:c[0]);s.set('guild_id',123);s.set('debug_channel_id',777)
    for uid in range(1,5):s.member(uid,f'Member {uid}')
    yield s,c
    s.close()


def session(s,c,start='2026-10-03 07:00'):
    return s.create_class(parse_local(start,s.tz),60,'IACC','Arabic',1)


def poll(s,c,sid):
    return s.open_poll(sid,[stamp(parse_local('2026-10-04 07:00',s.tz)),stamp(parse_local('2026-10-05 07:00',s.tz))],c[0]+timedelta(hours=24),1)


def test_no_override_three_votes_then_automatic_move_on_fourth(world):
    s,c=world;sid=session(s,c);pid=poll(s,c,sid);original=s.session(sid)['start_at']
    for uid in [1,2,3]:s.vote(pid,uid,0)
    assert consensus.settle(s,pid) is None
    with pytest.raises(RuleError,match='all four'):s.confirm_slot(pid,0,1)
    assert s.session(sid)['start_at']==original
    s.vote(pid,4,0)
    assert consensus.settle(s,pid)==sid
    assert s.session(sid)['revision']==1
    assert progress.locked(s,sid)
    assert len(s.responses(sid))==4
    assert consensus.settle(s,pid) is None
    assert len(s.rows('SELECT * FROM auto_moves'))==1
    with pytest.raises(RuleError):s.rsvp(sid,1,'coming',revision=0)


def test_earliest_unanimous_option_wins_regardless_of_option_order(world):
    s,c=world;sid=session(s,c)
    slots=[stamp(parse_local('2026-10-05 07:00',s.tz)),stamp(parse_local('2026-10-04 07:00',s.tz))]
    pid=s.open_poll(sid,slots,c[0]+timedelta(hours=24),1)
    for uid in range(1,5):
        s.vote(pid,uid,0);s.vote(pid,uid,1)
    assert consensus.winner(s,pid)==1
    consensus.settle(s,pid)
    assert s.session(sid)['start_at']==slots[1]


def test_no_common_time_opens_new_suggestion_round_without_moving(world):
    s,c=world;sid=session(s,c);pid=poll(s,c,sid)
    for uid in range(1,5):
        for n in range(2): s.answer_poll(pid,uid,n,n==uid%2)
    assert consensus.finish_round(s,pid)
    assert s.poll(pid)['status']=='no_agreement'
    assert len(s.rows("SELECT * FROM replacement_requests WHERE state='pending'"))==4
    assert s.session(sid)['revision']==0
    assert not s.rows('SELECT * FROM ledger')
    new=s.suggest(sid,4,parse_local('2026-10-06 07:00',s.tz),0)
    assert new!=pid
    assert s.poll(new)['status']=='open'
    assert s.rows('SELECT user_id FROM votes WHERE poll_id=?',(new,))==[{'user_id':4}]
    assert not consensus.finish_round(s,new)  # Unanswered people are not agreement or rejection.


def test_expired_round_never_locks_using_old_votes(world):
    s,c=world;sid=session(s,c);pid=poll(s,c,sid)
    for uid in range(1,5):s.vote(pid,uid,0)
    c[0]+=timedelta(days=1)
    assert consensus.settle(s,pid) is None
    assert consensus.finish_round(s,pid)
    assert s.session(sid)['revision']==0


def test_move_and_announcement_outbox_are_one_transaction(world):
    s,c=world;sid=session(s,c);pid=poll(s,c,sid)
    for uid in range(1,5):s.vote(pid,uid,0)
    s.db.execute("CREATE TRIGGER fail_outbox BEFORE INSERT ON auto_moves BEGIN SELECT RAISE(ABORT,'test crash'); END")
    with pytest.raises(sqlite3.IntegrityError):consensus.settle(s,pid)
    assert s.session(sid)['revision']==0
    assert s.poll(pid)['status']=='open'
    assert not s.rows('SELECT * FROM auto_moves')
    s.db.execute('DROP TRIGGER fail_outbox')
    consensus.settle(s,pid)
    assert s.session(sid)['revision']==1


def test_pending_move_survives_store_reopen(world):
    s,c=world;sid=session(s,c);pid=poll(s,c,sid)
    for uid in range(1,5):s.vote(pid,uid,0)
    consensus.settle(s,pid)
    filename=s.db.execute('PRAGMA database_list').fetchone()[2]
    reopened=Store(filename,clock=lambda:c[0])
    assert reopened.rows('SELECT * FROM auto_moves')[0]['announced']==0
    assert consensus.settle(reopened,pid) is None
    assert reopened.session(sid)['revision']==1
    reopened.close()


def test_emoji_rsvp_and_actual_attendance_and_partial_group_credit(world):
    s,c=world;sid=session(s,c);m=s.participants(sid)[0]
    assert progress.member_line(s,sid,m).startswith('⏳')
    s.answer(sid,m['user_id'],False,0)
    assert progress.member_line(s,sid,m).startswith('❌')
    s.suggest(sid,m['user_id'],parse_local('2026-10-04 07:00',s.tz),0)
    assert progress.member_line(s,sid,m).startswith('📅')
    for uid in range(1,5):s.answer(sid,uid,True,0)
    assert progress.member_line(s,sid,m).startswith('✅')
    c[0]=parse_local('2026-10-03 08:01',s.tz)
    s.set_attendance(sid,4,'absent',1);s.held(sid,1)
    assert s.monthly_count('2026-10')==0
    assert progress.member_line(s,sid,{'user_id':4}).startswith('❌')
    s.set_attendance(sid,4,'present',1)
    assert s.monthly_count('2026-10')==1


def test_streaks_cross_months_and_correct_after_absence_or_excuse(world):
    s,c=world
    ids=[]
    for date in ['2026-10-03','2026-10-10','2026-11-07','2026-11-14']:
        dt=parse_local(date+' 07:00',s.tz);c[0]=dt-timedelta(days=3)
        sid=s.create_class(dt,60,'IACC','Streak',1);ids.append(sid)
        for uid in range(1,5):s.answer(sid,uid,True,0)
        c[0]=dt+timedelta(minutes=61);s.held(sid,1)
    assert progress.personal(s,4)=={'current':4,'best':4}
    assert progress.group_streak(s)['current']==2
    s.set_attendance(ids[-1],4,'absent',1)
    assert progress.personal(s,4)['current']==0
    assert progress.group_streak(s)['current']==1  # Current month is still in progress.
    s.set_attendance(ids[-1],4,'excused',1)
    assert progress.personal(s,4)['current']==3
    c[0]=parse_local('2026-12-01 12:00',s.tz)
    assert progress.group_streak(s)['current']==0


def test_danger_uses_all_four_rsvps_and_escalates_final_week(world):
    s,c=world
    first=session(s,c);second=session(s,c,'2026-10-10 07:00')
    assert progress.danger(s)['level']==2
    for sid in [first,second]:
        for uid in range(1,5):s.answer(sid,uid,True,0)
    assert progress.danger(s)['confirmed']==2
    assert progress.danger(s)['level']==1
    s.answer(second,4,False,0)
    assert progress.danger(s)['confirmed']==1
    assert progress.danger(s)['level']==2
    c[0]=parse_local('2026-10-30 12:00',s.tz)
    assert progress.danger(s)['level']==3


def test_overlapping_classes_do_not_make_target_look_safe(world):
    s,c=world;ids=[session(s,c),session(s,c,'2026-10-03 07:30')]
    for sid in ids:
        for uid in range(1,5):s.answer(sid,uid,True,0)
    assert progress.danger(s)['confirmed']==1


def test_debug_rejects_live_channel_and_public_panel_has_no_test_button(world):
    s,c=world
    async def run():
        bot=MizanBot(s)
        from mizan.ui import Panel
        assert not any(b.label=='Test mode' for b in Panel(bot).children)
        i=SimpleNamespace(guild_id=123,channel_id=456,user=SimpleNamespace(id=1,roles=[]),permissions=SimpleNamespace(manage_guild=True))
        with pytest.raises(RuleError,match='lives in'):await open_test(bot,i)
        assert not bot.test_sessions
        await bot.close()
    asyncio.run(run())


def test_danger_refreshes_cards_without_separate_alert_messages(world):
    s,c=world;s.set('channel_id',456);s.set('panel_message_id',1);s.set('weekly_invites',False)
    ids=[session(s,c,'2026-10-10 07:00'),session(s,c,'2026-10-17 07:00')]
    for sid in ids:
        for uid in range(1,5):s.answer(sid,uid,True,0)
        with s.db: s.db.execute('UPDATE sessions SET message_id=10 WHERE id=?',(sid,))
    async def run():
        bot=MizanBot(s);bot.notify=AsyncMock(return_value=SimpleNamespace(id=9));bot.update_card=AsyncMock()
        await bot.tick();await bot.tick()
        reports=[x.args[0] for x in bot.notify.call_args_list if 'completed**' in x.args[0]]
        assert not reports
        assert bot.update_card.await_count==2
        assert any('On track' in f.value for f in bot.card(ids[0]).fields)
        bot.update_card.reset_mock()
        s.answer(ids[-1],4,False,0)
        await bot.tick();await bot.tick()
        reports=[x.args[0] for x in bot.notify.call_args_list if 'completed**' in x.args[0]]
        assert not reports
        assert bot.update_card.await_count==2
        assert any('At risk' in f.value for f in bot.card(ids[0]).fields)
        await bot.close()
    asyncio.run(run())


def test_no_show_break_survives_rescheduling_to_a_makeup(world):
    s,c=world;first=session(s,c)
    for uid in range(1,5):s.answer(first,uid,True,0)
    c[0]=parse_local('2026-10-03 08:01',s.tz);s.held(first,1)
    second=session(s,c,'2026-10-10 07:00')
    for uid in range(1,5):s.answer(second,uid,True,0)
    c[0]=parse_local('2026-10-10 08:01',s.tz)
    s.set_attendance(second,4,'absent',1)
    s.cancel(second,1,'Missing member')
    assert progress.personal(s,4)['current']==0
    pid=s.open_poll(second,[stamp(parse_local('2026-10-11 07:00',s.tz))],c[0]+timedelta(hours=1),1)
    for uid in range(1,5):s.vote(pid,uid,0)
    consensus.settle(s,pid)
    c[0]=parse_local('2026-10-11 08:01',s.tz);s.held(second,1)
    assert progress.personal(s,4)['current']==1
    assert progress.personal(s,3)['current']==2


def test_normal_schedule_command_in_debug_cannot_change_live_records(world):
    s,c=world
    async def run():
        bot=MizanBot(s)
        msg=SimpleNamespace(id=88,edit=AsyncMock())
        i=SimpleNamespace(guild_id=123,channel_id=777,user=SimpleNamespace(id=1,display_name='TA',roles=[]),permissions=SimpleNamespace(manage_guild=True),response=SimpleNamespace(defer=AsyncMock(),send_message=AsyncMock(),is_done=lambda:True),followup=SimpleNamespace(send=AsyncMock(return_value=msg)))
        await bot.actions['schedule'].callback(i,start='2026-10-03 07:00',topic='Debug command rehearsal')
        assert not s.rows('SELECT * FROM sessions')
        assert len(bot.test_sessions[1].store.rows('SELECT * FROM sessions'))==1
        assert i.followup.send.call_args_list[0].kwargs['ephemeral'] is True
        await bot.close()
    asyncio.run(run())


def test_stale_live_panels_cannot_mutate_live_store_from_debug(world):
    s,c=world
    async def run():
        bot=MizanBot(s);sid=session(s,c)
        from mizan.flow import respond,Recap
        i=SimpleNamespace(guild_id=123,channel_id=777,user=SimpleNamespace(id=1,roles=[]),permissions=SimpleNamespace(manage_guild=True))
        with pytest.raises(RuleError,match='older panel'):await respond(bot,i,sid,1,False,0)
        with pytest.raises(RuleError,match='older panel'):await Recap(bot,sid,0).interaction_check(i)
        assert not s.responses(sid)
        assert not s.rows('SELECT * FROM ledger')
        await bot.close()
    asyncio.run(run())


def test_proposal_responses_are_explicit_visible_and_idempotent(world):
    s,c=world;sid=session(s,c);pid=poll(s,c,sid)
    s.answer_poll(pid,1,0,True);s.answer_poll(pid,1,0,True)
    s.answer_poll(pid,2,0,False)
    assert [s.poll_answer(pid,u,0) for u in range(1,5)]==['yes','no','waiting','waiting']
    bot=MizanBot(s)
    text=bot.poll_embed(pid,0).fields[0].value
    for name in ['Member 1','Member 2','Member 3','Member 4']: assert name in text
    for emoji in ['✅','❌','⏳']: assert emoji in text
    s.answer_poll(pid,2,0,True)
    for u in [3,4]: s.answer_poll(pid,u,0,True)
    assert consensus.settle(s,pid)==sid
    assert progress.locked(s,sid)


def test_answering_one_time_does_not_close_other_options(world):
    s,c=world;sid=session(s,c);pid=poll(s,c,sid)
    for uid in range(1,5):s.answer_poll(pid,uid,0,False)
    assert not consensus.finish_round(s,pid)
    assert s.poll(pid)['status']=='open'
    for uid in range(1,5):s.answer_poll(pid,uid,1,True)
    assert consensus.settle(s,pid)==sid


def test_per_time_no_persists_and_other_option_is_unanswered(world):
    s,c=world;sid=session(s,c);pid=poll(s,c,sid)
    s.answer_poll(pid,1,0,False)
    path=s.db.execute('PRAGMA database_list').fetchone()[2]
    other=Store(path,clock=lambda:c[0])
    assert other.poll_answer(pid,1,0)=='no'
    assert other.poll_answer(pid,1,1)=='waiting'
    other.close()


def test_reschedule_card_has_only_two_explicit_responses(world):
    from mizan.bot import PollView
    s,c=world;sid=session(s,c);pid=poll(s,c,sid)
    async def check():
        view=PollView(MizanBot(s),pid,0)
        assert [b.label for b in view.children]==['I can come',"I can't"]
        assert view.is_persistent()
    asyncio.run(check())
