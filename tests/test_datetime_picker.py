import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from mizan.core import RuleError, Store, parse_local
from mizan.bot import MizanBot
from mizan.datetime_picker import parse_suggestion, pick_datetime
from mizan.flow import ReplacementTimes
from mizan.prayer import ensure_weekly


@pytest.fixture
def store(tmp_path,monkeypatch):
    monkeypatch.delenv('DISCORD_GUILD_ID',raising=False)
    monkeypatch.setenv('BEEMINDER_ENABLED','false')
    s=Store(tmp_path/'picker.sqlite',clock=lambda:parse_local('2026-11-19 12:00','America/Chicago'))
    s.set('guild_id',123)
    for uid in range(1,5): s.member(uid,f'Member {uid}')
    yield s
    s.close()


def interaction():
    return SimpleNamespace(guild_id=123,user=SimpleNamespace(id=1),
        response=SimpleNamespace(send_message=AsyncMock(),edit_message=AsyncMock(),send_modal=AsyncMock(),
                                 defer=AsyncMock(),is_done=lambda:False),followup=SimpleNamespace(send=AsyncMock()))


@pytest.mark.parametrize('text,expected',[
    ('Friday at 4pm','2026-11-20 16:00'),('fri 3:30 PM','2026-11-20 15:30'),
    ('tomorrow 4pm','2026-11-20 16:00'),('today 5pm','2026-11-19 17:00'),
    ('Thursday 11am','2026-11-26 11:00'),('Nov 27 4pm','2026-11-27 16:00'),
    ('November 27, 2026 at 4pm','2026-11-27 16:00'),('2026-11-27 16:00','2026-11-27 16:00'),
    ('Nov 27 12am','2026-11-27 00:00'),('Nov 27 12pm','2026-11-27 12:00'),
    ('Jan 1 4pm','2027-01-01 16:00'),
])
def test_typed_times(store,text,expected):
    assert parse_suggestion(text,store)==parse_local(expected,store.tz)


@pytest.mark.parametrize('text',['Friday 4','tomorrow 13pm','tomorrow 4:90pm','Feb 30 4pm','something vague'])
def test_invalid_text_has_no_guesses(store,text):
    with pytest.raises(RuleError): parse_suggestion(text,store)


def test_dst_safety_is_preserved(store):
    with pytest.raises(RuleError,match='ambiguous'):
        parse_suggestion('2027-11-07 01:30',store)
    assert parse_suggestion('2027-11-07T01:30:00-06:00',store)


def test_calendar_navigation_time_selection_and_draft_publication(store):
    async def run():
        bot=MizanBot(store)
        sid=ensure_weekly(store)
        store.answer(sid,1,False,0)
        original=store.session(sid)['start_at']
        menu=ReplacementTimes(bot,sid,1,0,[])
        i=interaction()
        await next(b for b in menu.children if b.label=='Pick a date & time').callback(i)
        view=i.response.send_message.call_args.kwargs['view']
        # Every rendered view obeys Discord's five-row / 25-option constraints.
        def valid(v):
            v.to_components()
            assert all(len(c.options)<=25 for c in v.children if isinstance(c,discord.ui.Select))
        valid(view)
        j=interaction()
        await next(b for b in view.children if getattr(b,'label',None)=='Next month →').callback(j)
        assert 'December 2026' in j.response.send_message.call_args.args[0]
        view=j.response.send_message.call_args.kwargs['view'];valid(view)
        day_select=next(c for c in view.children if isinstance(c,discord.ui.Select))
        day_select._values=['2026-12-04']
        k=interaction();await day_select.callback(k)
        view=k.response.send_message.call_args.kwargs['view'];valid(view)
        quick=interaction();await next(c for c in view.children if getattr(c,'label',None)=='5 PM').callback(quick)
        view=quick.response.send_message.call_args.kwargs['view'];valid(view)
        chosen=interaction();await next(c for c in view.children if getattr(c,'label',None)=='Use this time').callback(chosen)
        draft=chosen.response.send_message.call_args.kwargs['view'];valid(draft)
        assert draft.selected==[parse_local('2026-12-04 17:00',store.tz)]
        assert not store.rows('SELECT * FROM polls')
        bot.update_card=AsyncMock();bot.update_poll=AsyncMock();bot.notify=AsyncMock()
        save=interaction();await next(c for c in draft.children if getattr(c,'label',None)=='Suggest selected times').callback(save)
        assert len(store.rows('SELECT * FROM polls'))==1
        assert store.session(sid)['start_at']==original
        assert store.poll_answer(1,1,0)=='yes'
        await bot.close()
    asyncio.run(run())


def test_same_week_old_saturday_invitation_is_preserved(store):
    old=store.create_class(parse_local('2026-11-21 06:30',store.tz),60,'IACC','Arabic',1)
    store.set('weekly:2026-11-21',old)
    assert ensure_weekly(store)==old
    assert len(store.rows('SELECT * FROM sessions'))==1
    assert store.session(old)['start_at']==parse_local('2026-11-21 06:30',store.tz).isoformat()


def test_moved_old_weekly_key_still_prevents_duplicate(store):
    old=store.create_class(parse_local('2026-11-28 06:30',store.tz),60,'IACC','Arabic',1)
    store.set('weekly:2026-11-21',old)
    assert ensure_weekly(store)==old
    assert len(store.rows('SELECT * FROM sessions'))==1


def test_typed_draft_can_be_cleared_and_stale_submission_rejected(store):
    async def run():
        bot=MizanBot(store);sid=ensure_weekly(store);store.answer(sid,1,False,0)
        view=ReplacementTimes(bot,sid,1,0,[])
        typed=interaction()
        await next(b for b in view.children if b.label=='Type dates / times').callback(typed)
        modal=typed.response.send_modal.call_args.args[0]
        submitted=interaction()
        await modal.submit(submitted,['tomorrow 3pm','Nov 27 5pm',''])
        draft=submitted.response.send_message.call_args.kwargs['view']
        assert len(draft.selected)==2
        assert not store.rows('SELECT * FROM polls')
        clear=interaction()
        await next(c for c in draft.children if getattr(c,'label',None)=='Clear draft').callback(clear)
        assert clear.response.send_message.call_args.kwargs['view'].selected==[]
        with store.db: store.db.execute('UPDATE sessions SET revision=revision+1 WHERE id=?',(sid,))
        with pytest.raises(RuleError,match='changed'):
            await next(c for c in draft.children if getattr(c,'label',None)=='Suggest selected times').callback(interaction())
        assert not store.rows('SELECT * FROM polls')
        await bot.close()
    asyncio.run(run())
