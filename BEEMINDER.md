# Group commitment: at least two classes per calendar month

**Agreement:** the group must hold two classes in each calendar month, Dallas/Plano time. All four linked account owners share the same result, even if a particular person was unavailable. Al-Mīzān scores are separate. Accepted RSVPs alone never produce financial credit; a TA must confirm class held.

Agreed stakes: **$5 starting pledge, $90 maximum pledge**. No goals have been activated by this repository. Each participant must configure and accept their own financial commitment.

## Why not just a "2 per month" Do More goal?

A constant rate is an average and can allow early deadlines or banking extra classes across months. That is not an exact calendar-month minimum. This adapter instead records **monthly commitment failures**:

- 2 or more confirmed held classes this month → **0 failures**.
- Fewer than 2 → **1 failure**.

Every participant gets the same monthly result in a dedicated **Do Less** goal with **zero allowed failures**. Extra classes cannot offset a later month's failure. The goal's count accumulates failures; the bot's monthly class count starts fresh each calendar month.

## Account-owner setup (do not skip)

For each participant:

1. Create a dedicated auto-summing **Do Less** goal for monthly Arabic commitment failures. Rate **0**, daily aggregation **sum**, no unused allowance/safety buffer, flat future goal road. This must be an independent goal, not an existing class-count goal.
2. Set account timezone `America/Chicago`, goal deadline local midnight, initial pledge $5, cap $90. Disable automatic pessimistic failure datapoints (or have Beeminder confirm they cannot create an off-cycle failure for this goal). Check post-derailment respite and allowance so a failure in a later month also breaches the goal. Do not assume defaults implement this contract.
3. Agree on the first full month. Default example is November 2026; partial October is not silently included.
4. Run a test with Beeminder/support before real activation to confirm one `1` causes exactly the intended derailment and a `0` does not. No direct-charge endpoint is used.
5. Store personal API token privately in a Railway variable. Add a binding with Discord ID, username, goal slug, token variable name, and `contract_verified:true` **only after verifying these settings**.

Example variable value (dummy IDs/accounts):

```json
[
  {"discord_id":111,"username":"alice","goal":"arabic-monthly","token_env":"BEE_ALICE","contract_verified":true},
  {"discord_id":222,"username":"bob","goal":"arabic-monthly","token_env":"BEE_BOB","contract_verified":true},
  {"discord_id":333,"username":"carol","goal":"arabic-monthly","token_env":"BEE_CAROL","contract_verified":true},
  {"discord_id":444,"username":"dave","goal":"arabic-monthly","token_env":"BEE_DAVE","contract_verified":true}
]
```

Set this as `BEEMINDER_BINDINGS_JSON`; individual secrets are `BEE_ALICE`, etc. Run `/beeminder` for read-only validation. Only then set `BEEMINDER_START_MONTH=2026-11` and `BEEMINDER_ENABLED=true`.

API checks include type, auto-summing, aggregation, timezone, deadline, rate/road/buffer, and pledge range. Some account settings (especially cap and post-derailment behavior) still require owner verification. The adapter is deliberately fail-closed if it cannot verify the contract.

## Month-end delivery

During the final ten minutes of the local calendar month, scheduler computes the result and queues one delivery per linked goal. Classes on the last day must finish before 23:00 to leave confirmation time. Unconfirmed scheduled sessions block the audit and alert the TA.

Persistent delivery records plus Beeminder `requestid` prevent retries creating multiple failures. Successfully audited months are sealed against silent attendance changes. Failures are surfaced in Discord. A next-day check alerts if any delivery is missing.

If the process is down through month-end, or a class remains unconfirmed, it **does not fabricate/backdate a failure after the deadline**. TA/account owners must reconcile with Beeminder. This is not guaranteed enforcement during an outage; Railway uptime and month-end alerts matter.

No direct card charging, pledge changes, contract creation, or financial forgiveness is performed by the bot. Beeminder handles legitimate derailments and its notification/charge process. Corrections after an audit must be reconciled with the affected goals and account owners/support.

Official references: [API and datapoint request IDs](https://api.beeminder.com/), [pledge levels and caps](https://help.beeminder.com/article/20-how-much-do-i-pledge-on-my-goals), [derailment and charging](https://help.beeminder.com/article/17-what-happens-when-i-derail).
