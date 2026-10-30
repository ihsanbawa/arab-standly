import asyncio
from datetime import datetime,timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mizan.bot import MizanBot
from mizan.core import RuleError,Store,parse_local,stamp
from mizan.prayer import ensure_weekly
from mizan.testing import DemoBot,open_test


def interaction(uid=100,ta=True):
    msg=SimpleNamespace(id=88,edit=AsyncMock())
    return SimpleNamespace(guild_id=123,user=SimpleNamespace(id=uid,display_name='TA',roles=[]),
        permissions=SimpleNamespace(manage_guild=ta),
        response=SimpleNamespace(send_message=AsyncMock(),send_modal=AsyncMock(),defer=AsyncMock(),is_done=lambda:True),
        followup=SimpleNamespace(send=AsyncMock(return_value=msg)))


@pytest.fixture
def root(tmp_path,monkeypatch):
    monkeypatch.delenv('DISCORD_GUILD_ID',raising=False)
    monkeypatch.setenv('BEEMINDER_ENABLED','false')
    clock=lambda:parse_local('2026-11-02 12:00','America/Chicago')
    store=Store(tmp_path/'real.sqlite',clock=clock)
    store.set('guild_id',123)
    yield store
    store.close()


def test_test_mode_uses_separate_scores_and_never_sends_real_announcements(root):
    async def run():
        bot=MizanBot(root)
        bot.notify=AsyncMock(side_effect=AssertionError('Must never publish live'))
        i=interaction()
        await open_test(bot,i)
        demo=bot.test_sessions[100]
        assert len(demo.store.roster())==4
        assert not root.roster()
        demo.store.set('weekly_invites',True)
        sid=ensure_weekly(demo.store)
        demo.store.set('weekly_invites',False)
        await demo.update_card(sid,create=True)
        sent=i.followup.send.call_args
        assert sent.kwargs['ephemeral'] is True
        assert sent.kwargs['allowed_mentions'].users is False
        assert 'TEST ONLY' in sent.args[0]
        for p in demo.store.roster(): demo.store.rsvp(sid,p['user_id'],'coming')
        demo.test_clock=datetime.fromisoformat(demo.store.session(sid)['confirmation_due'])+timedelta(minutes=1)
        await demo.tree.get_command('classheld').callback(i,class_id=sid)
        assert demo.store.monthly_count('2026-11')==1
        assert all(p['points']==1 for p in demo.store.standings('2026-11'))
        assert not root.rows('SELECT * FROM sessions')
        assert not root.rows('SELECT * FROM ledger')
        assert not root.rows('SELECT * FROM deliveries')
        assert not demo.bee.configured()
        bot.notify.assert_not_called()
        await bot.close()
    asyncio.run(run())


def test_demo_scheduler_mocks_private_dms_and_cannot_use_network(root):
    async def run():
        bot=MizanBot(root);i=interaction();await open_test(bot,i)
        demo=bot.test_sessions[100]
        demo.store.set('weekly_invites',True);sid=ensure_weekly(demo.store);demo.store.set('weekly_invites',False)
        await demo.update_card(sid,create=True)
        s=demo.store.session(sid)
        demo.test_clock=datetime.fromisoformat(s['start_at'])-timedelta(hours=1)
        await demo.tick()
        texts=[str(c.args[0]) for c in i.followup.send.call_args_list if c.args]
        assert len([x for x in texts if 'Simulated private nudge' in x])==4
        assert all('TEST ONLY' in x for x in texts)
        assert not root.rows('SELECT * FROM notices')
        await bot.close()
    asyncio.run(run())


def test_test_wuphf_reuses_consent_quiet_hours_and_cooldown_without_http(root):
    async def run():
        bot=MizanBot(root);i=interaction();await open_test(bot,i);demo=bot.test_sessions[100]
        client=SimpleNamespace(post=AsyncMock(side_effect=AssertionError('No external request')))
        with pytest.raises(RuleError,match='opted'):
            await demo.wuphf.send(client,100,'sms','Test class')
        demo.wuphf.consent(100,True,False,False)
        result=await demo.wuphf.send(client,100,'sms','Test class')
        assert 'No text or call was sent' in result
        with pytest.raises(RuleError,match='hour'):
            await demo.wuphf.send(client,100,'sms','Test class')
        demo.test_clock=parse_local('2026-11-03 06:00',demo.store.tz)
        with pytest.raises(RuleError,match='Quiet hours'):
            await demo.wuphf.send(client,100,'sms','Test class')
        demo.wuphf.consent(100,True,True,True)
        await demo.wuphf.send(client,100,'calls','Test class')
        client.post.assert_not_called()
        assert not root.rows('SELECT * FROM audit')
        await bot.close()
    asyncio.run(run())


def test_test_mode_is_private_to_the_ta_and_reset_invalidates_old_controls(root):
    async def run():
        bot=MizanBot(root)
        with pytest.raises(RuleError,match='Only a TA'):
            await open_test(bot,interaction(ta=False))
        i=interaction();await open_test(bot,i);demo=bot.test_sessions[100]
        with pytest.raises(RuleError,match='another TA'):
            demo.check_guild(interaction(uid=101))
        view=i.response.send_message.call_args.kwargs['view']
        reset=next(b for b in view.children if b.label=='Reset test session')
        await reset.callback(i)
        assert demo.demo_closed
        assert bot.test_sessions[100] is not demo
        with pytest.raises(RuleError,match='reset'):
            demo.check_guild(i)
        assert not bot.test_sessions[100].store.rows('SELECT * FROM sessions')
        await bot.close()
    asyncio.run(run())
