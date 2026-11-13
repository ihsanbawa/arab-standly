"""Prove every WUPHF text keeps its reply line and STOP within the 3-part budget, for every roast."""
from datetime import timedelta
from types import SimpleNamespace
import unicodedata
import pytest
from mizan.content import ROASTS, BURNS, ascii_sms, strip_harakat, plain_roast, burn, roast
from mizan.core import Store, parse_local
from mizan.escalation import sms_text
from mizan.wuphf import SMS_PREFIX, SMS_STOP, sms_units, sms_fits, fit_sms

GUILD = 1234567890123456789
CHANNEL = 1234567890123456790
SESSION_MSG = 1234567890123456791
JOB_MSG = 1234567890123456792


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.delenv('DISCORD_GUILD_ID', raising=False)
    c = [parse_local('2026-11-05 09:00', 'America/Chicago')]
    s = Store(tmp_path / 'db', clock=lambda: c[0])
    s.set('guild_id', GUILD)
    s.set('channel_id', CHANNEL)
    for u in range(1, 5):
        s.member(u, f'Member {u}')
    sid = s.create_class(c[0] + timedelta(days=2), 60, 'IACC', 'Arabic', 1)
    with s.db:
        s.db.execute('UPDATE sessions SET message_id=? WHERE id=?', (SESSION_MSG, sid))
    yield s, sid
    s.close()


def expected(source, followup, replies):
    if source == 'auto':
        opener = 'Second notice, and the whole class channel has been told.' if followup else 'Your Arabic class RSVP is overdue.'
        action = (
            "Reply YES if you're coming or NO if you can't."
            if replies
            else f"RSVP in Discord: https://discord.com/channels/{GUILD}/{CHANNEL}/{SESSION_MSG}"
        )
    else:
        opener = 'Still hiding? The class channel is watching.' if followup else 'Member 3 is looking for you in Arabic class.'
        action = (
            'Reply HERE to check in.'
            if replies
            else f"Tap I'm here on your WUPHF card: https://discord.com/channels/{GUILD}/{CHANNEL}/{JOB_MSG}"
        )
    return opener, action


@pytest.mark.parametrize('source', ['auto', 'manual'])
@pytest.mark.parametrize('followup', [False, True])
@pytest.mark.parametrize('replies', [True, False])
def test_every_roast_fits_every_text_configuration(world, monkeypatch, source, followup, replies):
    s, sid = world
    if replies:
        monkeypatch.setenv('TWILIO_WEBHOOK_URL', 'https://example.test/twilio/sms')
    else:
        monkeypatch.delenv('TWILIO_WEBHOOK_URL', raising=False)
    bot = SimpleNamespace(store=s)
    if source == 'auto':
        job = {'user_id': 1, 'source': 'auto', 'session_id': sid, 'actor': 0, 'message_id': None}
    else:
        job = {'user_id': 1, 'source': 'manual', 'session_id': None, 'actor': 3, 'message_id': JOB_MSG}
    opener, action = expected(source, followup, replies)
    for i in range(len(ROASTS)):
        s.set('roast:sms:1', i)
        msg = sms_text(bot, job, followup)
        body = SMS_PREFIX + msg + SMS_STOP
        ar = strip_harakat(ROASTS[i][0])
        en = ascii_sms(ROASTS[i][1])
        lines = msg.split('\n')
        assert sms_fits(body)
        assert lines[0] == opener and lines[-1] == action
        assert body.endswith('\n' + action + SMS_STOP)
        assert lines[1:-1] in ([ar, en], [ar], [en], [])
        full = SMS_PREFIX + '\n'.join([opener, ar, en, action]) + SMS_STOP
        arabic_only = SMS_PREFIX + '\n'.join([opener, ar, action]) + SMS_STOP
        english_only = SMS_PREFIX + '\n'.join([opener, en, action]) + SMS_STOP
        if sms_fits(full):
            assert lines[1:-1] == [ar, en]
        elif sms_fits(arabic_only):
            assert lines[1:-1] == [ar]
        elif sms_fits(english_only):
            assert lines[1:-1] == [en]
        assert not any('ً' <= ch <= 'ْ' or ch == 'ٰ' for ch in body)
        assert '=' not in msg
        if replies:
            assert ar in lines


def test_follow_up_draws_a_different_roast(world, monkeypatch):
    s, sid = world
    monkeypatch.setenv('TWILIO_WEBHOOK_URL', 'https://example.test/twilio/sms')
    bot = SimpleNamespace(store=s)
    job = {'user_id': 1, 'source': 'auto', 'session_id': sid, 'actor': 0, 'message_id': None}
    pool = {strip_harakat(r[0]) for r in ROASTS}
    first = sms_text(bot, job, False).split('\n')[1]
    second = sms_text(bot, job, True).split('\n')[1]
    assert first in pool and second in pool and first != second


def test_long_english_is_dropped_whole_not_sliced():
    opener = 'Your Arabic class RSVP is overdue.'
    action = "Reply YES if you're coming or NO if you can't."
    assert fit_sms(opener, action, english='word ' * 120) == opener + '\n' + action
    ar = strip_harakat(ROASTS[0][0])
    assert fit_sms(opener, action, arabic=ar, english='word ' * 120) == '\n'.join([opener, ar, action])
    assert sms_units(SMS_PREFIX + opener + '\n' + action + SMS_STOP)[0] == 'GSM-7'


def test_required_lines_are_never_cut_even_over_budget():
    opener = '\U0001F986' * 40 + ' is looking for you in Arabic class.'
    action = f"Tap I'm here on your WUPHF card: https://discord.com/channels/{GUILD}/{CHANNEL}/{JOB_MSG}"
    assert fit_sms(opener, action, arabic=strip_harakat(ROASTS[0][0]), english='x') == opener + '\n' + action


def test_sms_units_counts_gsm_septets_and_utf16_units():
    assert sms_units('abc') == ('GSM-7', 3)
    assert sms_units('a{b') == ('GSM-7', 4)
    assert sms_units('€') == ('GSM-7', 2)
    assert sms_units('a\nb') == ('GSM-7', 3)
    assert sms_units('\U0001F986') == ('UCS-2', 2)
    assert sms_units('با') == ('UCS-2', 2)
    assert sms_fits('a' * 459) and not sms_fits('a' * 460)
    assert sms_fits('ب' * 198) and not sms_fits('ب' * 199)


def test_strip_harakat_keeps_every_letter():
    def letters(text):
        return [ch for ch in text if unicodedata.category(ch).startswith('L')]

    for arabic, _en, _note in ROASTS:
        stripped = strip_harakat(arabic)
        assert not any('ً' <= ch <= 'ْ' or ch == 'ٰ' for ch in stripped)
        assert letters(stripped) == letters(unicodedata.normalize('NFC', arabic))
    assert strip_harakat('أَ') == 'أ'
    assert strip_harakat('أ') == 'أ'
    assert strip_harakat('Where are you?') == 'Where are you?'


def test_discord_and_legacy_helpers_still_work(world):
    s, _sid = world
    line = roast(0)
    assert '||' in line and '=' in line
    assert plain_roast(s, 1) in {ascii_sms(r[1]) for r in ROASTS}
    assert burn(s, 1) in BURNS
