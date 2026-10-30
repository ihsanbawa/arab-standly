"""Monthly GROUP compliance, never individual attendance or direct charges.

One Do Less goal per participant, zero allowed failures, no safety buffer.
At month-end report 0 (>=2 held classes) or 1 (missed monthly commitment).
This deliberately avoids a 2/month average that permits banking extra classes.
Financial goal creation/configuration is performed by the account owner.
"""

import hashlib
import json
import os
import re
from datetime import timedelta
from zoneinfo import ZoneInfo

import aiohttp

from .core import RuleError, month_of, stamp


def bindings_from_env():
    raw = json.loads(os.getenv("BEEMINDER_BINDINGS_JSON", "[]"))
    if not isinstance(raw, list):
        raise RuleError("BEEMINDER_BINDINGS_JSON must be a list.")
    seen = set()
    for b in raw:
        if not isinstance(b, dict) or not all(
            b.get(k) for k in ("discord_id", "username", "goal", "token_env")
        ):
            raise RuleError("Each Beeminder binding needs discord_id, username, goal and token_env.")
        if not all(re.fullmatch(r"[A-Za-z0-9_-]+", b[k]) for k in ("username", "goal", "token_env")):
            raise RuleError("Invalid Beeminder binding name.")
        key = b["username"] + "/" + b["goal"]
        if key in seen:
            raise RuleError("Duplicate Beeminder goal binding.")
        seen.add(key)
    return raw


class Beeminder:
    def __init__(self, store):
        self.store = store
        self.bindings = bindings_from_env()
        self.enabled = os.getenv("BEEMINDER_ENABLED", "false").lower() == "true"
        self.start_month = os.getenv("BEEMINDER_START_MONTH", "")

    def configured(self):
        # Financial roster is fixed by bindings, independent of temporary classroom hiatus.
        known = {m["user_id"] for m in self.store.rows("SELECT user_id FROM members")}
        linked = {int(b["discord_id"]) for b in self.bindings}
        return bool(
            self.enabled
            and re.fullmatch(r"\d{4}-\d{2}", self.start_month)
            and len(linked) == 4
            and len(self.bindings) == 4
            and linked <= known
        )

    async def request(self, client, binding, method, path, data=None, user_resource=False):
        token = os.getenv(binding["token_env"])
        if not token:
            raise RuleError("Missing private token for " + binding["username"])
        values = {"auth_token": token, **(data or {})}
        base = f"https://www.beeminder.com/api/v1/users/{binding['username']}"
        url = base + path if user_resource else base + f"/goals/{binding['goal']}{path}"
        kwargs = {"params": values} if method == "GET" else {"data": values}
        async with client.request(method, url, **kwargs) as response:
            if response.status >= 400:
                # Never include response bodies/URLs: these can contain credentials.
                raise RuleError(f"Beeminder returned HTTP {response.status} for {binding['username']}.")
            return await response.json()

    async def validate(self, client, binding):
        goal = await self.request(client, binding, "GET", ".json")
        user = await self.request(client, binding, "GET", ".json", user_resource=True)
        errors = []
        if goal.get("goal_type") != "drinker" or not goal.get("kyoom"):
            errors.append("needs an auto-summing Do Less goal")
        if goal.get("currate") != 0:
            errors.append("goal rate must be zero")
        road = goal.get("fullroad") or []
        current = self.store.clock().timestamp()
        past = [row for row in road if len(row) == 3 and row[0] <= current]
        future = [row for row in road if len(row) == 3 and row[0] > current]
        if not past:
            errors.append("cannot verify current goal boundary")
        else:
            boundary = past[-1][1]
            if goal.get("curval") is None or abs(float(boundary) - float(goal["curval"])) > 0.0001:
                errors.append("goal must have zero unused allowance (no carryover buffer)")
            if any(float(row[2] or 0) != 0 or float(row[1]) != float(boundary) for row in future):
                errors.append("future goal boundary must remain flat")
        if user.get("timezone") != self.store.tz:
            errors.append("goal timezone must match " + self.store.tz)
        if goal.get("deadline") != 0:
            errors.append("goal deadline must be local midnight")
        if goal.get("aggday") != "sum":
            errors.append("daily aggregation must be sum")
        pledge = float(goal.get("pledge") or 0)
        if not 5 <= pledge <= 90:
            errors.append("pledge must be between $5 and $90")
        if binding.get("contract_verified") is not True:
            errors.append(
                "owner must verify $90 pledge cap, zero buffer, flat road, no automatic failure data, and derailment settings"
            )
        if errors:
            raise RuleError(binding["username"] + ": " + "; ".join(errors))
        return goal

    def prepare(self, month):
        if not self.configured():
            raise RuleError("Beeminder is not activated for the full group.")
        if month < self.start_month:
            raise RuleError("This month predates the agreed commitment.")
        local = self.store.clock().astimezone(ZoneInfo(self.store.tz))
        # Only submit the CURRENT month's audit in the last 10 minutes before midnight.
        # After downtime, alert a TA instead of silently backdating a financial failure.
        if (
            month_of(local, self.store.tz) != month
            or (local + timedelta(days=1)).month == local.month
            or (local.hour, local.minute) < (23, 50)
        ):
            raise RuleError("Monthly audit runs during the final 10 minutes of the calendar month.")
        if self.store.unsettled(month):
            raise RuleError("A class still needs confirmation. Financial audit is blocked; TA action needed.")
        value = 0 if self.store.monthly_count(month) >= 2 else 1
        with self.store.transaction():
            for binding in self.bindings:
                key = binding["username"] + "/" + binding["goal"]
                request_id = (
                    "mizan-month-"
                    + hashlib.sha256(
                        (str(self.store.get("guild_id")) + ":" + month + ":" + key).encode()
                    ).hexdigest()[:32]
                )
                self.store.db.execute(
                    "INSERT OR IGNORE INTO deliveries(month,binding,value,request_id,updated_at) VALUES(?,?,?,?,?)",
                    (month, key, value, request_id, stamp(self.store.clock())),
                )

    async def sync(self, client, month):
        self.prepare(month)
        results = []
        for binding in self.bindings:
            key = binding["username"] + "/" + binding["goal"]
            d = self.store.one("SELECT * FROM deliveries WHERE month=? AND binding=?", (month, key))
            if d["state"] == "sent":
                continue
            try:
                # Reconcile an ambiguous previous response before validating a possibly
                # already-derailed goal. This is a read, not another financial action.
                existing = await self.request(client, binding, "GET", "/datapoints.json", {"count": 1000})
                found = next((x for x in existing if x.get("requestid") == d["request_id"]), None)
                if found:
                    if float(found["value"]) != d["value"]:
                        raise RuleError("Existing audit datapoint differs; owner reconciliation required.")
                    with self.store.db:
                        self.store.db.execute(
                            "UPDATE deliveries SET state='sent',datapoint_id=?,error=NULL,updated_at=? WHERE month=? AND binding=?",
                            (found["id"], stamp(self.store.clock()), month, key),
                        )
                    results.append(key + ": synced (confirmed previous delivery)")
                    continue
                await self.validate(client, binding)
                if month_of(self.store.clock(), self.store.tz) != month:
                    raise RuleError(
                        "Month-end window closed during provider checks; TA reconciliation required."
                    )
                # Stable requestid makes retries an upsert, not another financial failure.
                data = await self.request(
                    client,
                    binding,
                    "POST",
                    "/datapoints.json",
                    {
                        "value": d["value"],
                        "daystamp": self.store.clock().astimezone(ZoneInfo(self.store.tz)).strftime("%Y%m%d"),
                        "requestid": d["request_id"],
                        "comment": f"Al-Mizan GROUP monthly audit {month}: {self.store.monthly_count(month)}/2 classes held; 0=met, 1=missed",
                    },
                )
                with self.store.db:
                    self.store.db.execute(
                        "UPDATE deliveries SET state='sent',datapoint_id=?,attempts=attempts+1,error=NULL,updated_at=? WHERE month=? AND binding=?",
                        (data["id"], stamp(self.store.clock()), month, key),
                    )
                results.append(key + ": synced")
            except (RuleError, aiohttp.ClientError, TimeoutError, KeyError, ValueError, TypeError) as exc:
                error = str(exc) if isinstance(exc, RuleError) else type(exc).__name__
                with self.store.db:
                    self.store.db.execute(
                        "UPDATE deliveries SET state='failed',attempts=attempts+1,error=?,updated_at=? WHERE month=? AND binding=?",
                        (error, stamp(self.store.clock()), month, key),
                    )
                results.append(key + ": " + error)
        return results
