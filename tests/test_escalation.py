import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from mizan.core import Store,RuleError,parse_local,stamp
from mizan.bot import MizanBot
from mizan.wuphf import Wuphf
from mizan.escalation import enqueue_auto,process,create_manual,Acknowledge,recover_interrupted
from mizan.content import ROASTS,roast

@pytest.fixture
def world(tmp_path,monkeypatch):
    monkeypatch.delenv('DISCORD_GUILD_ID',raising=False)
    c=[parse_local('2026-11-05 09:00','America/Chicago')]
    s=Store(tmp_path/'db',clock=lambda:c[0]);s.set('guild_id',123);s.set('channel_id',456)
    for u in range(1,5):s.member(u,f'Member {u}')
    sid=s.create_class(c[0]+timedelta(days=2),60,'IACC','Arabic',1)
    yield s,c,sid
    s.close()


def bot_for(s):
    bot=MizanBot(s)
    phone=SimpleNamespace(phone=lambda uid:'+15555550101',preferences=lambda uid:s.get(f'nudges:{uid}',{'sms':True,'calls':True,'early':False}),check=lambda uid,mode:None,send=AsyncMock())
    bot.wuphf=phone;bot.notify_once=AsyncMock()
    return bot


def test_auto_text_two_hours_then_call_two_hours_after_actual_text(world):
    s,c,sid=world
    async def run():
        bot=bot_for(s);session=s.session(sid)
        jid=enqueue_auto(bot,session,1)
        assert enqueue_auto(bot,session,1)==jid
        await process(bot);bot.wuphf.send.assert_not_called()
        c[0]+=timedelta(hours=2)
        await process(bot);await process(bot)
        assert [x.args[2] for x in bot.wuphf.send.call_args_list]==['sms']
        c[0]+=timedelta(hours=2)
        await process(bot);await process(bot)
        assert [x.args[2] for x in bot.wuphf.send.call_args_list]==['sms','calls']
        assert s.one('SELECT state FROM wuphf_jobs WHERE id=?',(jid,))['state']=='done'
        await bot.close()
    asyncio.run(run())


@pytest.mark.parametrize('reply',['coming','unavailable'])
def test_any_rsvp_stops_remaining_escalation(world,reply):
    s,c,sid=world
    async def run():
        bot=bot_for(s);enqueue_auto(bot,s.session(sid),1)
        c[0]+=timedelta(hours=2);await process(bot)
        s.answer(sid,1,reply=='coming',0)
        c[0]+=timedelta(hours=2);await process(bot)
        assert bot.wuphf.send.await_count==1
        assert s.one('SELECT state FROM wuphf_jobs')['state']=='answered'
        await bot.close()
    asyncio.run(run())


def test_actual_sms_time_controls_call_even_after_quiet_hour_delay(world):
    s,c,sid=world
    async def run():
        bot=bot_for(s);enqueue_auto(bot,s.session(sid),1)
        c[0]+=timedelta(hours=2)
        def blocked(uid,mode):raise RuleError('Quiet hours')
        bot.wuphf.check=blocked
        await process(bot);bot.wuphf.send.assert_not_called()
        c[0]+=timedelta(hours=5);bot.wuphf.check=lambda uid,mode:None
        await process(bot)
        c[0]+=timedelta(hours=1);await process(bot)
        assert bot.wuphf.send.await_count==1
        c[0]+=timedelta(hours=1);await process(bot)
        assert bot.wuphf.send.await_count==2
        await bot.close()
    asyncio.run(run())


def test_ambiguous_timeout_never_calls_or_retries_after_restart(world):
    s,c,sid=world
    async def run():
        bot=bot_for(s);enqueue_auto(bot,s.session(sid),1)
        bot.wuphf.send.side_effect=TimeoutError()
        c[0]+=timedelta(hours=2);await process(bot)
        c[0]+=timedelta(hours=3);await process(bot)
        assert bot.wuphf.send.await_count==1
        assert s.one('SELECT state FROM wuphf_jobs')['state']=='uncertain'
        with s.db:s.db.execute("UPDATE wuphf_jobs SET state='sending_sms'")
        recover_interrupted(s)
        assert s.one('SELECT state FROM wuphf_jobs')['state']=='uncertain'
        await process(bot);assert bot.wuphf.send.await_count==1
        await bot.close()
    asyncio.run(run())


def test_regular_member_can_create_manual_wuphf_without_any_class(world):
    s,c,sid=world
    with s.db:
        s.db.execute('DELETE FROM session_members WHERE session_id=?',(sid,))
        s.db.execute('DELETE FROM sessions WHERE id=?',(sid,))
    async def run():
        bot=bot_for(s)
        channel=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=99)))
        bot.channel=AsyncMock(return_value=channel)
        i=SimpleNamespace(guild_id=123,channel_id=456,user=SimpleNamespace(id=3),permissions=SimpleNamespace(manage_guild=False),response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        await bot.tree.get_command('wuphf').callback(i,person=SimpleNamespace(id=4,bot=False))
        jid=s.one('SELECT id FROM wuphf_jobs')['id']
        assert s.one('SELECT source FROM wuphf_jobs WHERE id=?',(jid,))['source']=='manual'
        assert bot.wuphf.send.await_count==1
        c[0]+=timedelta(minutes=59);await process(bot)
        assert bot.wuphf.send.await_count==1
        c[0]+=timedelta(minutes=1);await process(bot)
        assert bot.wuphf.send.call_args.args[2]=='calls'
        await bot.close()
    asyncio.run(run())


def test_only_recipient_can_acknowledge_and_ack_during_sms_prevents_call(world):
    s,c,sid=world
    async def run():
        bot=bot_for(s);bot.channel=AsyncMock(return_value=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=99))))
        creator=SimpleNamespace(guild_id=123,channel_id=456,user=SimpleNamespace(id=2),response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        async def during_send(*args):
            jid=s.one('SELECT id FROM wuphf_jobs')['id'];view=Acknowledge(bot,jid)
            recipient=SimpleNamespace(guild_id=123,channel_id=456,user=SimpleNamespace(id=4),response=SimpleNamespace(edit_message=AsyncMock()))
            with pytest.raises(RuleError,match='Only they'):
                recipient.user.id=3;await view.children[0].callback(recipient)
            recipient.user.id=4;await view.children[0].callback(recipient)
        bot.wuphf.send.side_effect=during_send
        await create_manual(bot,creator,4)
        assert s.one('SELECT state FROM wuphf_jobs')['state']=='acknowledged'
        c[0]+=timedelta(hours=2);await process(bot)
        assert bot.wuphf.send.await_count==1
        await bot.close()
    asyncio.run(run())


def test_group_defaults_authorised_and_stop_is_honoured(world,monkeypatch):
    s,c,sid=world
    for k,v in {'TWILIO_ACCOUNT_SID':'ACtest','TWILIO_AUTH_TOKEN':'test','TWILIO_PHONE_NUMBER':'+15555550100','WUPHF_CONTACTS_JSON':'{"1":"PHONE_A"}','PHONE_A':'+15555550101'}.items():monkeypatch.setenv(k,v)
    w=Wuphf(s)
    assert w.check(1,'sms')=='+15555550101'
    assert w.check(1,'calls')=='+15555550101'
    s.set('nudges:1',{'sms':False,'calls':False,'early':False})
    with pytest.raises(RuleError,match='stopped'):w.check(1,'sms')


def test_roasts_rotate_without_repetition_for_a_full_cycle(world):
    s,c,sid=world
    lines=[roast(4,s,1) for _ in range(len(ROASTS))]
    assert len(ROASTS)>=24 and len(set(lines))==len(ROASTS)
    assert all('||' in line and '=' in line for line in lines)
    assert roast(4,s,1)==lines[0]


def test_pending_call_survives_restart_without_repeating_sms(world):
    s,c,sid=world
    async def run():
        bot=bot_for(s);session=s.session(sid);jid=enqueue_auto(bot,session,1)
        c[0]+=timedelta(hours=2);await process(bot)
        filename=s.db.execute('PRAGMA database_list').fetchone()[2]
        reopened=Store(filename,clock=lambda:c[0]);other=bot_for(reopened)
        assert enqueue_auto(other,reopened.session(sid),1)==jid
        recover_interrupted(reopened)
        c[0]+=timedelta(hours=2);await process(other)
        assert [x.args[2] for x in other.wuphf.send.call_args_list]==['calls']
        assert reopened.one('SELECT state FROM wuphf_jobs')['state']=='done'
        await bot.close();await other.close();reopened.close()
    asyncio.run(run())


def test_cancelled_class_and_personal_stop_cancel_pending_jobs(world):
    s,c,sid=world
    async def run():
        bot=bot_for(s);enqueue_auto(bot,s.session(sid),1);enqueue_auto(bot,s.session(sid),2)
        s.set('nudges:1',{'sms':False,'calls':False,'early':False})
        await process(bot)
        assert s.one('SELECT state FROM wuphf_jobs WHERE user_id=1')['state']=='stopped'
        s.cancel(sid,1,'Class cancelled')
        c[0]+=timedelta(hours=3);await process(bot)
        assert s.one('SELECT state FROM wuphf_jobs WHERE user_id=2')['state']=='answered'
        bot.wuphf.send.assert_not_called()
        await bot.close()
    asyncio.run(run())
