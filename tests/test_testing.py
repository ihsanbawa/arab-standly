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
    return SimpleNamespace(guild_id=123,channel_id=777,user=SimpleNamespace(id=uid,display_name='TA',roles=[]),
        permissions=SimpleNamespace(manage_guild=ta),
        response=SimpleNamespace(send_message=AsyncMock(),send_modal=AsyncMock(),defer=AsyncMock(),is_done=lambda:False),
        followup=SimpleNamespace(send=AsyncMock(return_value=msg)))


@pytest.fixture
def root(tmp_path,monkeypatch):
    monkeypatch.delenv('DISCORD_GUILD_ID',raising=False)
    monkeypatch.setenv('BEEMINDER_ENABLED','false')
    clock=lambda:parse_local('2026-11-04 19:00','America/Chicago')
    store=Store(tmp_path/'real.sqlite',clock=clock)
    store.set('guild_id',123)
    store.set('debug_channel_id',777)
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
        await demo.actions['classheld'].callback(i,class_id=sid)
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
        demo.wuphf.consent(100,False,False,False)
        with pytest.raises(RuleError,match='stopped'):
            await demo.wuphf.send(client,100,'sms','Test class')
        demo.wuphf.consent(100,True,False,False)
        result=await demo.wuphf.send(client,100,'sms','Test class')
        assert 'No text or call was sent' in result
        with pytest.raises(RuleError,match='hour'):
            await demo.wuphf.send(client,100,'sms','Test class')
        demo.test_clock=parse_local('2026-11-05 06:00',demo.store.tz)
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


def test_compact_rsvp_updates_in_place_and_offers_rescheduling(root):
    from mizan.testing import simulated_rsvp
    async def run():
        bot=MizanBot(root);i=interaction();await open_test(bot,i);demo=bot.test_sessions[100]
        demo.store.set('weekly_invites',True);sid=ensure_weekly(demo.store);demo.store.set('weekly_invites',False)
        await demo.update_card(sid,create=True)
        compact=interaction()
        compact.message=SimpleNamespace(flags=SimpleNamespace(ephemeral=True),embeds=[])
        compact.response.edit_message=AsyncMock()
        await simulated_rsvp(demo,compact,sid,2)
        compact.response.send_message.assert_not_called()
        view=compact.response.edit_message.call_args.kwargs['view']
        assert len([x for x in view.children if isinstance(x, __import__('discord').ui.Select)])==1
        answer=next(b for b in view.children if getattr(b,'label',None)=="I can't")
        result=interaction()
        result.message=compact.message
        result.response.is_done=lambda:True
        result.edit_original_response=AsyncMock()
        await answer.callback(result)
        content=result.edit_original_response.call_args.kwargs['content']
        assert 'When can you come instead?' in content
        assert '**-1**' in content
        controls=result.edit_original_response.call_args.kwargs['view']
        assert any(getattr(b,'placeholder',None)=='When CAN you come?' for b in controls.children)
        assert demo.store.one("SELECT state FROM replacement_requests WHERE user_id=2")['state']=='pending'
        assert all('TEST ONLY' in call.args[0] for call in result.followup.send.call_args_list)
        assert demo.store.session(sid)['status']=='scheduled'
        assert demo.store.rows('SELECT * FROM ledger')[0]['points']==-1
        assert not root.rows('SELECT * FROM sessions')
        await bot.close()
    asyncio.run(run())


@pytest.mark.parametrize('clock_label',['3 days before','1 hour before','After class ends'])
def test_clock_buttons_handle_discords_no_empty_view_contract(root, clock_label):
    from mizan.testing import clock_menu
    async def run():
        bot=MizanBot(root);i=interaction();await open_test(bot,i);demo=bot.test_sessions[100]
        demo.store.set('weekly_invites',True);sid=ensure_weekly(demo.store);demo.store.set('weekly_invites',False)
        await demo.update_card(sid,create=True)
        await clock_menu(demo,i)
        picker=i.response.send_message.call_args.kwargs['view'].children[0]
        picker._values=[str(sid)]
        j=interaction();await picker.callback(j)
        controls=j.response.send_message.call_args.kwargs['view']
        button=next(b for b in controls.children if b.label==clock_label)
        k=interaction()
        k.message=SimpleNamespace(flags=SimpleNamespace(ephemeral=True),embeds=[])
        k.edit_original_response=AsyncMock()
        def deferred(*args,**kwargs): k.response.is_done=lambda:True
        k.response.defer.side_effect=deferred
        async def strict_send(*args,**kwargs):
            # Discord Webhook.send rejects an explicitly supplied None view.
            if 'view' in kwargs and kwargs['view'] is None:
                raise TypeError('expected view parameter to be of type View, not NoneType')
            return SimpleNamespace(id=89,edit=AsyncMock())
        k.followup.send.side_effect=strict_send
        await button.callback(k)
        assert 'Test clock:' in k.edit_original_response.call_args.kwargs['content']
        assert not root.rows('SELECT * FROM sessions')
        assert not root.rows('SELECT * FROM notices')
        assert demo.store.monthly_count('2026-11')==0
        await bot.close()
    asyncio.run(run())


def test_test_errors_offer_test_controls_without_live_status(root):
    async def run():
        bot=MizanBot(root);i=interaction();await open_test(bot,i)
        j=interaction()
        await bot.test_sessions[100].respond_error(j,TypeError('bad view'))
        text=j.response.send_message.call_args.args[0]
        assert '/status' not in text
        assert 'Test mode' in text
        view=j.response.send_message.call_args.kwargs['view']
        assert any(b.label=='Reset test session' for b in view.children)
        await bot.close()
    asyncio.run(run())


def test_automatic_wuphf_rehearsal_runs_both_stages_without_external_delivery(root):
    from mizan.testing import rehearse_wuphf
    async def run():
        bot=MizanBot(root);i=interaction();await open_test(bot,i);demo=bot.test_sessions[100]
        demo.store.set('weekly_invites',True);sid=ensure_weekly(demo.store);demo.store.set('weekly_invites',False)
        await demo.update_card(sid,create=True)
        menu=interaction();await rehearse_wuphf(demo,menu)
        view=menu.response.send_message.call_args.kwargs['view']
        await next(b for b in view.children if b.label=='Start missing-RSVP test').callback(interaction())
        assert demo.store.rows('SELECT * FROM wuphf_jobs')
        advance=next(b for b in view.children if b.label=='Advance 2 hours')
        await advance.callback(interaction())
        sms=demo.store.rows("SELECT * FROM audit WHERE action='test_nudge'")
        assert len(sms)==4
        await advance.callback(interaction())
        assert len(demo.store.rows("SELECT * FROM audit WHERE action='test_nudge'"))==8
        assert all(j['state']=='done' for j in demo.store.rows('SELECT * FROM wuphf_jobs'))
        assert not root.rows('SELECT * FROM wuphf_jobs')
        await bot.close()
    asyncio.run(run())


def test_welcome_preview_is_replayable_without_live_or_demo_delivery_markers(root):
    async def run():
        from mizan.testing import TestControls
        from mizan.welcome import welcome_embed
        bot=MizanBot(root);i=interaction();await open_test(bot,i)
        demo=bot.test_sessions[100]
        view=TestControls(bot,demo)
        labels={b.label for b in view.children}
        assert {'Welcome preview','Browse test controls','More test tools'} <= labels
        preview=next(b for b in view.children if b.label=='Welcome preview')
        for _ in range(2):
            j=interaction()
            await preview.callback(j)
            sent=j.response.send_message.call_args
            assert sent.kwargs['ephemeral'] is True
            assert sent.kwargs['allowed_mentions'].users is False
            assert sent.kwargs['embed'].to_dict()==welcome_embed().to_dict()
            assert not root.get('welcome:sent:100')
            assert not root.get('welcome:seen:100')
            assert not demo.store.get('welcome:sent:100')
            assert not demo.store.get('welcome:seen:100')
        with pytest.raises(RuleError):
            await preview.callback(interaction(ta=False))
        assert not root.rows('SELECT * FROM audit')
        await bot.close()
    asyncio.run(run())
