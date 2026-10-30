import asyncio
from datetime import datetime,timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from mizan.bot import MizanBot,ClassView
from mizan.core import Store,RuleError,parse_local,stamp
from mizan.prayer import ensure_weekly,defaults
from mizan.testing import open_test
from mizan.flow import Recap


def interaction(uid=100):
    msg=SimpleNamespace(id=88,edit=AsyncMock())
    i=SimpleNamespace(guild_id=123,user=SimpleNamespace(id=uid,display_name='TA',roles=[]),permissions=SimpleNamespace(manage_guild=True),
        response=SimpleNamespace(send_message=AsyncMock(),send_modal=AsyncMock(),defer=AsyncMock(),is_done=lambda:False),followup=SimpleNamespace(send=AsyncMock(return_value=msg)))
    def defer(*args,**kwargs): i.response.is_done=lambda:True
    i.response.defer.side_effect=defer
    return i


@pytest.fixture
def state(tmp_path,monkeypatch):
    monkeypatch.delenv('DISCORD_GUILD_ID',raising=False)
    monkeypatch.setenv('BEEMINDER_ENABLED','false')
    clock=[parse_local('2026-11-02 12:00','America/Chicago')]
    s=Store(tmp_path/'real.sqlite',clock=lambda:clock[0]);s.set('guild_id',123)
    for uid in [100,2,3,4]: s.member(uid,f'Member {uid}')
    yield s,clock
    s.close()


def test_wednesday_check_in_is_deduplicated(state):
    s,c=state
    assert ensure_weekly(s) is None
    c[0]=parse_local('2026-11-04 17:59',s.tz)
    assert ensure_weekly(s) is None
    c[0]+=timedelta(minutes=1)
    sid=ensure_weekly(s)
    assert ensure_weekly(s)==sid
    assert len(s.rows('SELECT * FROM sessions'))==1
    assert s.monthly_count('2026-11')==0


def test_two_button_decline_deduction_pending_proposal_and_strongest_penalty(state):
    s,c=state;c[0]=parse_local('2026-11-04 18:00',s.tz);sid=ensure_weekly(s)
    async def controls():
        bot=MizanBot(s)
        assert [b.label for b in ClassView(bot,sid,0).children]==['I can come',"I can't"]
        await bot.close()
    asyncio.run(controls())
    assert s.answer(sid,2,False,0)['points']==-1
    assert s.answer(sid,2,False,0)['points']==-1
    assert s.one('SELECT state FROM replacement_requests WHERE user_id=2')['state']=='pending'
    c[0]=datetime.fromisoformat(s.session(sid)['start_at'])-timedelta(hours=1)
    assert s.answer(sid,2,False,0)['points']==-2
    s.set_attendance(sid,2,'absent',100)
    assert s.one('SELECT points FROM penalties WHERE user_id=2')['points']==-2
    assert len(s.rows('SELECT * FROM ledger WHERE user_id=2'))==1


def test_proposals_automatically_create_and_extend_poll_without_losing_votes(state):
    s,c=state;c[0]=parse_local('2026-11-04 18:00',s.tz);sid=ensure_weekly(s)
    s.answer(sid,2,False,0)
    original=s.session(sid)['start_at']
    option=parse_local('2026-11-08 06:15',s.tz)
    pid=s.suggest(sid,2,option,0)
    assert len(s.poll(pid)['options'])==1
    s.vote(pid,100,0)
    s.answer(sid,3,False,0)
    assert s.suggest(sid,3,option+timedelta(hours=1),0)==pid
    assert len(s.poll(pid)['options'])==2
    assert s.one('SELECT 1 FROM votes WHERE poll_id=? AND user_id=100 AND slot=0',(pid,))
    assert s.session(sid)['start_at']==original
    assert s.one('SELECT state FROM replacement_requests WHERE user_id=2')['state']=='suggested'
    with pytest.raises(RuleError): s.suggest(sid,4,option,0)
    with pytest.raises(RuleError): s.suggest(sid,2,option,1)


def test_whole_week_rehearsal_midweek_rsvp_recap_and_following_invitation(state):
    async def run():
        live,c=state;bot=MizanBot(live);i=interaction();await open_test(bot,i)
        demo=bot.test_sessions[100]
        home=i.response.send_message.call_args.kwargs['view']
        midweek=next(b for b in home.children if b.label=='1 · Run midweek RSVP')
        w=interaction();await midweek.callback(w)
        sid=demo.store.rows('SELECT * FROM sessions')[0]['id']
        assert demo.test_clock.astimezone(ZoneInfo(demo.store.tz)).strftime('%a %H:%M')=='Wed 18:00'
        assert demo.store.monthly_count('2026-11')==0
        for p in demo.store.roster(): demo.store.answer(sid,p['user_id'],True,0)
        after=next(b for b in home.children if b.label=='3 · Jump after class')
        j=interaction();await after.callback(j)
        recap_messages=[call for call in j.followup.send.call_args_list if isinstance(call.kwargs.get('view'),Recap)]
        assert len(recap_messages)==1
        recap_view=recap_messages[0].kwargs['view']
        k=interaction();await recap_view.children[0].callback(k)
        modal=k.response.send_modal.call_args.args[0]
        submit=interaction()
        await modal.submit(submit,['Chapter 4 pages 20–22: pronouns','Chapter 4 pages 23–25: verbs','Review chapter 4 Quizlet'])
        assert demo.store.monthly_count('2026-11')==1
        assert all(p['points']==1 for p in demo.store.standings('2026-11'))
        next_week=next(b for b in home.children if b.label=='4 · Next week’s announcement')
        l=interaction();await next_week.callback(l)
        following=demo.store.rows('SELECT * FROM sessions ORDER BY start_at')[-1]
        assert following['id']!=sid
        assert following['topic']=='Chapter 4 pages 23–25: verbs'
        assert following['prep']=='Review chapter 4 Quizlet'
        fields=demo.card(following['id']).to_dict()['fields']
        assert next(f['value'] for f in fields if f['name']=='Where we left off')=='Chapter 4 pages 20–22: pronouns'
        assert not live.rows('SELECT * FROM sessions')
        assert not live.rows('SELECT * FROM ledger')
        assert not live.rows('SELECT * FROM lesson_notes')
        await bot.close()
    asyncio.run(run())


def test_excuse_waives_decline_but_does_not_erase_separate_move_penalty(state):
    s,c=state;c[0]=parse_local('2026-11-04 18:00',s.tz);sid=ensure_weekly(s)
    s.answer(sid,2,False,0)
    s.set_attendance(sid,2,'excused',100,'Emergency')
    assert not s.one('SELECT * FROM penalties WHERE user_id=2')
    with s.transaction(): s.penalty(sid,2,-1,'Moved class after accepting',100)
    s.set_attendance(sid,2,'excused',100,'Emergency')
    assert s.one('SELECT points FROM penalties WHERE user_id=2')['points']==-1
