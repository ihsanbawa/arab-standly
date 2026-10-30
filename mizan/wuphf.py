"""Opt-in SMS/voice nudges. Contacts stay in private environment variables."""

import json
import os
import re
from datetime import datetime, timedelta
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

import aiohttp

from .core import RuleError, stamp


class Wuphf:
    def __init__(self, store):
        self.store = store

    def consent(self, uid, sms, calls, early):
        if not self.store.one("SELECT 1 FROM members WHERE user_id=? AND active=1", (uid,)):
            raise RuleError("Only active class members can opt in.")
        self.store.set(f"nudges:{uid}", {"sms": sms, "calls": calls, "early": early})

    def reserve(self, uid, mode, reason):
        prefs = self.store.get(f"nudges:{uid}", {})
        if mode not in {"sms", "calls"} or not prefs.get(mode):
            raise RuleError("This person has not opted into that nudge method.")
        if not self.store.one("SELECT 1 FROM members WHERE user_id=? AND active=1", (uid,)):
            raise RuleError("This member is on hiatus.")
        local = self.store.clock().astimezone(ZoneInfo(self.store.tz))
        if (local.hour < 8 or local.hour >= 21) and not prefs.get("early"):
            raise RuleError("Quiet hours: 21:00–08:00. Recipient can explicitly allow Fajr-time nudges.")
        contacts = json.loads(os.getenv("WUPHF_CONTACTS_JSON", "{}"))
        envname = contacts.get(str(uid))
        phone = os.getenv(envname, "") if envname else ""
        if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
            raise RuleError("No valid private E.164 phone number is configured for this member.")
        if not all(os.getenv(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER")):
            raise RuleError("Twilio is not configured.")
        recent = self.store.get(f"nudge_last:{uid}")
        if recent and self.store.clock() - datetime.fromisoformat(recent) < timedelta(minutes=60):
            raise RuleError("One WUPHF per recipient per hour. Please wait.")
        local_date = local.strftime("%Y-%m-%d")
        daykey = f"nudge_count:{uid}:{local_date}"
        count = self.store.get(daykey, 0)
        if count >= 3:
            raise RuleError("Daily limit reached: three nudges per recipient.")
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
        data = {"To": phone, "From": os.environ["TWILIO_PHONE_NUMBER"]}
        if mode == "sms":
            endpoint = "Messages"
            data["Body"] = "Shaykh Standly: " + message[:300] + " Reply STOP to stop SMS."
        else:
            endpoint = "Calls"
            data["Twiml"] = (
                "<Response><Say>"
                + escape("Shaykh Standly Arabic class reminder. " + message[:300])
                + "</Say></Response>"
            )
        try:
            async with client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/{endpoint}.json",
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
