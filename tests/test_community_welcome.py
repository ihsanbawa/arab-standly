import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import discord
import pytest
from mizan.core import Store,parse_local
from mizan.bot import MizanBot
from mizan.welcome import deliver,welcome_embed,open_private
from mizan.reports import public_board,standings_embed

@pytest.fixture
def world(tmp_path,monkeypatch):
    monkeypatch.delenv('DISCORD_GUILD_ID',raising=False)
    s=Store(tmp_path/'db',clock=lambda:parse_local('2026-11-04 12:00','America/Chicago'))
    s.set('guild_id',123);s.set('channel_id',456)
    for u in range(1,5):s.member(u,f'Member {u}')
    yield s
    s.close()


def test_private_welcome_once_even_with_concurrent_join_events(world):
    async def run():
        bot=MizanBot(world)
        member=SimpleNamespace(id=1,bot=False,guild=SimpleNamespace(id=123),send=AsyncMock())
        await asyncio.gather(deliver(bot,member),deliver(bot,member))
        assert member.send.await_count==1
        assert world.get('welcome:sent:1')
        embed=member.send.call_args.kwargs['embed']
        assert 'السَّلَامُ' in embed.title
        assert any('all four agree' in f.value for f in embed.fields)
        assert not world.get('nudges:1')
        await bot.close()
    asyncio.run(run())


def test_closed_dms_never_publish_welcome_in_channel(world):
    async def run():
        bot=MizanBot(world);bot.notify=AsyncMock()
        response=SimpleNamespace(status=403,reason='Forbidden')
        member=SimpleNamespace(id=1,bot=False,guild=SimpleNamespace(id=123),send=AsyncMock(side_effect=discord.Forbidden(response,'Closed DMs')))
        assert not await deliver(bot,member)
        bot.notify.assert_not_called()
        assert not world.get('welcome:sent:1')
        assert world.get('welcome:blocked:1')
        i=SimpleNamespace(guild_id=123,user=SimpleNamespace(id=1),response=SimpleNamespace(send_message=AsyncMock()))
        await open_private(bot,i)
        assert i.response.send_message.call_args.kwargs['ephemeral']
        await bot.close()
    asyncio.run(run())


def test_welcome_ignores_bots_and_other_servers(world):
    async def run():
        bot=MizanBot(world)
        for robot,gid in [(True,123),(False,999)]:
            m=SimpleNamespace(id=10,bot=robot,guild=SimpleNamespace(id=gid),send=AsyncMock())
            assert not await deliver(bot,m)
            m.send.assert_not_called()
        await bot.close()
    asyncio.run(run())


def test_public_board_reuses_one_message_and_keeps_lifetime_scores(world):
    world.ledger('last-month',None,1,3,'Attendance',1,'2026-10')
    world.ledger('this-month',None,1,-1,'Early decline',1,'2026-11')
    async def run():
        bot=MizanBot(world)
        message=SimpleNamespace(id=99,edit=AsyncMock())
        channel=SimpleNamespace(id=456,send=AsyncMock(return_value=message),fetch_message=AsyncMock(return_value=message))
        bot.channel=AsyncMock(return_value=channel)
        await public_board(bot);await public_board(bot)
        assert channel.send.await_count==1
        await public_board(bot,period='all',force=True)
        assert channel.send.await_count==1
        assert message.edit.await_count==1
        embed=message.edit.call_args.kwargs['embed']
        assert any('All-time **+2**' in f.value for f in embed.fields)
        assert any('This month -1' in f.value for f in embed.fields)
        world.ledger('new-point',None,2,1,'Attendance',1,'2026-11')
        await public_board(bot)
        assert message.edit.await_count==2
        assert world.get('public_mizan:2026-11')['period']=='all'
        await bot.close()
    asyncio.run(run())


def test_channel_migration_preserves_ledger_and_runs_once(world,monkeypatch):
    from mizan.community import configure_channel
    monkeypatch.setenv('CLASS_CHANNEL_ID','789')
    world.ledger('keep',None,1,2,'Attendance',1,'2026-10')
    world.set('panel_message_id',10)
    async def run():
        bot=MizanBot(world)
        channel=SimpleNamespace(id=789,guild=SimpleNamespace(id=123))
        monkeypatch.setattr('mizan.community.discord.TextChannel',type(channel))
        bot.get_channel=lambda cid:channel
        await configure_channel(bot);await configure_channel(bot)
        assert world.get('channel_id')==789
        assert world.get('panel_message_id') is None
        assert world.standings('2026-10')[0]['points']==2
        assert len(world.rows("SELECT * FROM audit WHERE action='class_channel_moved'"))==1
        await bot.close()
    asyncio.run(run())
