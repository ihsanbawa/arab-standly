"""Group-authorised SMS nudges (calls are retired). Contacts stay in private environment variables."""

import json
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiohttp

from .content import ascii_sms, strip_harakat
from .core import RuleError, stamp

SMS_PREFIX = "Shaykh Standly: "
SMS_STOP = " Reply STOP to stop SMS."
SMS_PARTS = 3
UCS2_PART = 66  # 67 is the real concatenated limit; one unit of margin because the sender type is unconfirmed
GSM_PART = 153
GSM7 = frozenset(
    "\n\r 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    "@£$¥èéùìòÇØøÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ!\"#¤%&'()*+,-./:;<=>?¡ÄÖÑÜ§¿äöñüà"
)
GSM7_EXT = frozenset("^{}\\[~]|€")


def sms_units(body):
    """Return (encoding, units): GSM-7 septets (extension characters cost 2), else UTF-16 code units."""
    if all(ch in GSM7 or ch in GSM7_EXT for ch in body):
        return "GSM-7", sum(2 if ch in GSM7_EXT else 1 for ch in body)
    return "UCS-2", len(body.encode("utf-16-le")) // 2


def sms_fits(body):
    """True when the body fits SMS_PARTS parts: 459 septets (GSM-7) or 198 UTF-16 units (UCS-2)."""
    encoding, units = sms_units(body)
    return units <= SMS_PARTS * (GSM_PART if encoding == "GSM-7" else UCS2_PART)


def fit_sms(opener, action, arabic="", english=""):
    """Join the text lines, richest layout that fits first: drop the English, then the Arabic, then both.

    The opener and the action (the reply instruction) are never cut and nothing is ever sliced;
    if even those two exceed the budget they are sent whole.
    """
    opener, action, english = ascii_sms(opener), ascii_sms(action), ascii_sms(english)
    arabic = strip_harakat(arabic)
    layouts = [
        [opener, arabic, english, action],
        [opener, arabic, action],
        [opener, english, action],
        [opener, action],
    ]
    candidates = ["\n".join(part for part in layout if part) for layout in layouts]
    for text in candidates:
        if sms_fits(SMS_PREFIX + text + SMS_STOP):
            return text
    return candidates[-1]


class Wuphf:
    def __init__(self, store):
        self.store = store

    def consent(self, uid, sms, calls, early):
        if not self.store.one("SELECT 1 FROM members WHERE user_id=? AND active=1", (uid,)):
            raise RuleError("Only active class members can opt in.")
        self.store.set(f"nudges:{uid}", {"sms": sms, "calls": calls, "early": early})

    def member_for(self, phone):
        """Reverse lookup for inbound texts: which roster member owns this number?"""
        for uid, envname in json.loads(os.getenv("WUPHF_CONTACTS_JSON", "{}")).items():
            if phone and os.getenv(envname, "") == phone:
                return int(uid)
        return None

    def phone(self, uid):
        contacts = json.loads(os.getenv("WUPHF_CONTACTS_JSON", "{}"))
        envname = contacts.get(str(uid))
        phone = os.getenv(envname, "") if envname else ""
        if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
            raise RuleError("No valid private E.164 phone number is configured for this member.")
        if not all(os.getenv(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER")):
            raise RuleError("Twilio is not configured.")
        return phone

    def preferences(self,uid):
        return self.store.get(f"nudges:{uid}", {"sms":True,"calls":True,"early":False})

    def check(self, uid, mode):
        prefs = self.preferences(uid)
        if mode != "sms" or not prefs.get("sms"):
            raise RuleError("This person has stopped that nudge method.")
        member=self.store.one("SELECT active FROM members WHERE user_id=?",(uid,))
        if member and not member["active"]: raise RuleError("This member is on hiatus.")
        local = self.store.clock().astimezone(ZoneInfo(self.store.tz))
        if (local.hour < 8 or local.hour >= 21) and not prefs.get("early"):
            raise RuleError("Quiet hours: 21:00–08:00. Queued phone nudges wait until morning.")
        phone = self.phone(uid)
        recent = self.store.get(f"nudge_last:{uid}")
        if recent and self.store.clock() - datetime.fromisoformat(recent) < timedelta(minutes=60):
            raise RuleError("One WUPHF per recipient per hour. Please wait.")
        local_date = local.strftime("%Y-%m-%d")
        daykey = f"nudge_count:{uid}:{local_date}"
        count = self.store.get(daykey, 0)
        if count >= 3:
            raise RuleError("Daily limit reached: three nudges per recipient.")
        return phone

    def reserve(self,uid,mode,reason):
        phone=self.check(uid,mode)
        local_date=self.store.clock().astimezone(ZoneInfo(self.store.tz)).strftime("%Y-%m-%d")
        daykey=f"nudge_count:{uid}:{local_date}"
        count=self.store.get(daykey,0)
        # Reserve before the HTTP request. Never retry an ambiguous timeout automatically.
        with self.store.transaction():
            self.store.db.execute(
                "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f"nudge_last:{uid}", json.dumps(stamp(self.store.clock()))),
            )
            self.store.db.execute(
                "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (daykey, json.dumps(count + 1)),
            )
            self.store.log(uid, "wuphf_reserved", {"mode": mode, "reason": reason})
        return phone

    async def send(self, client, uid, mode, message):
        phone = self.reserve(uid, mode, message)
        sid = os.environ["TWILIO_ACCOUNT_SID"]
        data = {
            "To": phone,
            "From": os.environ["TWILIO_PHONE_NUMBER"],
            "Body": SMS_PREFIX + message + SMS_STOP,
        }
        try:
            async with client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                data=data,
                auth=aiohttp.BasicAuth(sid, os.environ["TWILIO_AUTH_TOKEN"]),
            ) as r:
                if r.status >= 400:
                    raise RuleError(
                        f"Twilio returned HTTP {r.status}. Check the Twilio console; no automatic retry."
                    )
                return "Nudge accepted by Twilio (delivery is not yet confirmed)."
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise RuleError("Nudge delivery is uncertain. Check Twilio before retrying.") from exc
