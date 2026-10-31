import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import discord
import pytest
from mizan.core import Store,RuleError,parse_local,stamp
from mizan.bot import MizanBot
from mizan import consensus
from mizan.availability import save_ballot,AvailabilityMenu,SharedPollView
from mizan.reports import report_data,report_embed,ReportView


@pytest.fixture
def world(tmp_path,monkeypatch):
    monkeypatch.delenv('DISCORD_GUILD_ID',raising=False)
    c=[parse_local('2026-10-01 12:00','America/Chicago')]
    s=Store(tmp_path/'db',clock=lambda:c[0]);s.set('guild_id',123)
    for uid in range(1,5):s.member(uid,f'Member {uid}')
    sid=s.create_class(parse_local('2026-10-03 07:00',s.tz),60,'IACC','Arabic',1)
    slots=[stamp(parse_local(f'2026-10-0{n} 07:00',s.tz)) for n in [4,5]]
    pid=s.open_poll(sid,slots,c[0]+timedelta(hours=24),1)
    yield s,c,sid,pid,slots
    s.close()


def test_atomic_multi_time_ballot_auto_moves_on_fourth_save(world):
    s,c,sid,pid,slots=world
    save_ballot(s,pid,1,slots,{0,1})
    save_ballot(s,pid,2,slots,{1})
    save_ballot(s,pid,3,slots,{1})
    assert consensus.settle(s,pid) is None
    assert not consensus.finish_round(s,pid)
    save_ballot(s,pid,4,slots,{1})
    assert consensus.settle(s,pid)==sid
    assert s.session(sid)['start_at']==slots[1]
    assert len(s.responses(sid))==4
    assert not s.rows('SELECT * FROM ledger')


def test_new_option_preserves_answers_and_rejects_stale_ballot(world):
    s,c,sid,pid,slots=world
    save_ballot(s,pid,1,slots,{0})
    s.suggest(sid,2,parse_local('2026-10-06 07:00',s.tz),0)
    assert s.poll_answer(pid,1,0)=='yes'
    assert s.poll_answer(pid,1,1)=='no'
    assert s.poll_answer(pid,1,2)=='waiting'
    with pytest.raises(RuleError,match='new time'):
        save_ballot(s,pid,1,slots,{1})
    assert s.poll_answer(pid,1,0)=='yes'
    with pytest.raises(RuleError,match='three alternatives'):
        s.suggest(sid,3,parse_local('2026-10-07 07:00',s.tz),0)


def test_none_work_reopens_suggestions_without_deduction(world):
    s,c,sid,pid,slots=world
    for uid in range(1,5):save_ballot(s,pid,uid,slots,set())
    assert consensus.finish_round(s,pid)
    assert s.session(sid)['revision']==0
    assert not s.rows('SELECT * FROM ledger')
    assert len(s.rows("SELECT * FROM replacement_requests WHERE state='pending'"))==4


def test_failed_ballot_rolls_back_all_options(world,monkeypatch):
    s,c,sid,pid,slots=world
    original=s.answer_poll
    def fail(pid,uid,n,yes):
        if n==1:raise RuntimeError('failed second option')
        return original(pid,uid,n,yes)
    monkeypatch.setattr(s,'answer_poll',fail)
    with pytest.raises(RuntimeError):save_ballot(s,pid,1,slots,{0})
    assert s.poll_answer(pid,1,0)=='waiting'


def test_shared_public_board_and_private_ballot_controls(world):
    s,c,sid,pid,slots=world
    async def run():
        bot=MizanBot(s)
        view=SharedPollView(bot,pid)
        assert [b.label for b in view.children]==['Update my availability','Suggest another time']
        assert view.is_persistent()
        ballot=AvailabilityMenu(bot,pid,1)
        select=next(b for b in ballot.children if isinstance(b,discord.ui.Select))
        assert select.min_values==0 and select.max_values==2
        assert {'Save availability','None work for me'}=={b.label for b in ballot.children if isinstance(b,discord.ui.Button)}
        # One shared board, both options, all four names, and no per-option messages.
        channel=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=999)))
        bot.channel=AsyncMock(return_value=channel)
        await bot.update_poll(pid,create=True)
        assert channel.send.call_count==1
        assert len(channel.send.call_args.kwargs['embed'].fields)==2
        assert not s.rows('SELECT * FROM poll_cards')
        await bot.close()
    asyncio.run(run())


def test_reports_reconcile_ledger_history_corrections_and_reset(world):
    s,c,sid,pid,slots=world
    s.ledger('attend-test',sid,1,1,'Present',1,'2026-10')
    s.ledger('penalty-test',sid,1,-2,'Last-minute decline',1,'2026-10')
    data=report_data(s,'2026-10')
    member=next(m for m in data['members'] if m['user_id']==1)
    assert (member['earned'],member['lost'],member['points'])==(1,2,-1)
    detail=report_embed(s,'2026-10',1).to_dict()
    assert any('Last-minute decline' in f['name'] for f in detail['fields'])
    assert len([f for f in detail['fields'] if 'Class #1' in f['value']])==2
    s.ledger('penalty-test',sid,1,-1,'Corrected early decline',1,'2026-10')
    assert next(m for m in report_data(s,'2026-10')['members'] if m['user_id']==1)['points']==0
    assert all(m['points']==0 for m in report_data(s,'2026-11')['members'])
    assert len(next(m for m in report_data(s,'2026-10')['members'] if m['user_id']==1)['entries'])==2
    assert 'Next fresh start' in report_embed(s,'2026-10').footer.text
    assert '<t:' not in report_embed(s,'2026-10').footer.text
    assert int(report_data(s,'2026-10')['end'].timestamp())==int(parse_local('2026-11-01 00:00',s.tz).timestamp())


def test_report_paginates_full_history_and_supports_empty_month(world):
    s,c,sid,pid,slots=world
    for n in range(19):s.ledger(f'e{n}',sid,1,1,f'Attendance {n}',1,'2026-10')
    embed=report_embed(s,'2026-10',1,2)
    assert 'Page 3/3' in embed.fields[-2].value
    assert len(embed.fields)==5
    assert 'No point changes' in report_embed(s,'2026-11',1).fields[0].name
    async def run():
        bot=MizanBot(s);view=ReportView(bot,'2026-10',1)
        assert any(getattr(b,'label',None)=='More changes' for b in view.children)
        await bot.close()
    asyncio.run(run())


def test_private_ballot_save_refreshes_shared_card_and_prompts_none_work(world,monkeypatch):
    from mizan import flow
    s,c,sid,pid,slots=world
    async def run():
        bot=MizanBot(s)
        bot.update_poll=AsyncMock();bot.notify_once=AsyncMock()
        i=SimpleNamespace(guild_id=123,channel_id=456,user=SimpleNamespace(id=1),
            response=SimpleNamespace(defer=AsyncMock()),edit_original_response=AsyncMock())
        picker=AsyncMock();monkeypatch.setattr(flow,'suggest_menu',picker)
        ballot=AvailabilityMenu(bot,pid,1)
        await ballot.submit(i,pid,1,set())
        bot.update_poll.assert_awaited_once_with(pid,create=True)
        picker.assert_awaited_once_with(bot,i,sid,1,0)
        assert s.poll_answer(pid,1,0)=='no' and s.poll_answer(pid,1,1)=='no'
        assert not s.rows('SELECT * FROM ledger')
        await bot.close()
    asyncio.run(run())
