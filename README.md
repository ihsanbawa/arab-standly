# Arab Standly · ⚖️ Al-Mīzān

A Discord bot for a four-person **in-person** Arabic class. Coordinates Saturday classes after Fajr congregation at **IACC, Plano**, collects commitments, reschedules with polls, keeps monthly scores, and offers opt-in WUPHF phone nudges.

## Included

- Class announcement cards with physical location, topic, preparation, RSVP deadline, and Quizlet links.
- Persistent RSVP and multi-select replacement-poll buttons; they work after restarts.
- RSVP nudges, conflict notifications, day-before and one-hour reminders.
- TA-confirmed rescheduling: polls never silently cancel the original class. New time requires new RSVPs.
- Trust-based attendance: no routine roll call if everyone accepted. TA confirms class actually happened after its scheduled end; accepted participants count as present unless exceptions were recorded.
- Disruption reports from participants. Reporter is never automatically blamed or deducted.
- Al-Mīzān: attend +1; causing a move after accepting −1; last-minute move/unexcused absence −2. One strongest disruption penalty per person per class, not stacked penalties. Excuses/waivers and corrections supported.
- Monthly standings start at zero without deleting previous months.
- Curated Arabic roasts with translation reveals and vocabulary notes.
- Shared minimum of two classes per calendar month, at-risk alerts, unresolved-makeup reminders.
- Private record export, restricted TA commands, audit trail, persistent SQLite storage.
- Beeminder **group** monthly-compliance adapter, disabled until configured. See [BEEMINDER.md](BEEMINDER.md).
- WUPHF SMS/voice nudges, participant opt-in, Fajr quiet-hours choice, cooldown and daily limit.

The book-based learning assistant, automated grading, and Quizlet completion tracking are **not included** in this first version. The two supplied Quizlet links are included as resources.

## Local setup

Requires Python 3.13. Create a virtual environment, install `requirements-dev.txt`, copy `.env.example` to `.env`, and fill the Discord variables privately.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env
.venv/bin/python -m pytest -q
.venv/bin/python -m mizan.bot
```

### Create the Discord application

1. In the [Discord Developer Portal](https://discord.com/developers/applications), create **Al-Mizan** and open its Bot settings.
2. Set its token privately as `DISCORD_BOT_TOKEN`. Never put it in chat, GitHub, screenshots, or bot commands.
3. Invite it to your class server using OAuth2 scopes `bot` and `applications.commands`.
4. Give it View Channels, Send Messages, Embed Links, Read Message History, and Attach Files **only in the class channel**. Administrator and privileged member/message-content intents are unnecessary.
5. Enable Discord Developer Mode, copy the server ID into `DISCORD_GUILD_ID`, and your user ID into `BOT_OWNER_ID`.
6. Run `/setup` with the announcements channel and optional TA role. The first setup requires Manage Server or configured owner access.
7. Add all four participants (teachers included) using `/member`.

## Everyday flow

1. TA: `/schedule start:2026-11-07 06:15 topic:Chapter review` (example only; verify the mosque's congregation time and allow prayer to finish).
2. Everyone taps Coming / Running late / Can't attend / Request another time. Default RSVP deadline: 48 hours before class.
3. Bot sends reminders. Responses indicate intention, not attendance or financial credit.
4. At/after class, report exceptions with `/attendance`, then `/classheld`. If everyone accepted, no per-person roll call is needed.
5. If someone flakes: `/report`; TA decides whether others still meet. If class did not happen, `/cancel`, then `/poll`.
6. `/poll class_id:1 options:2026-11-08 07:00;2026-11-14 06:15 deadline:2026-11-06 18:00` (example dates).
7. People select all workable options or None work. TA uses `/confirm_slot`. Known conflicts require explicit `accept_conflicts:true`.
8. Supply `responsible` only after checking who broke an accepted commitment. Requesting a change before accepting is not penalized. Use `excused:true` for emergencies.

`/reopen` removes a mistaken class's attendance credits; correct and reconfirm or cancel. `/waive` removes a disruption penalty. Financially audited months are sealed to prevent silent disagreement with Beeminder; reconcile those with account owners/support.

### Prayer-relative schedule

Timezone is `America/Chicago` (Dallas/Plano, DST-aware). Duration defaults to 60 minutes. The bot deliberately does not invent Fajr or iqamah times. The TA verifies the [official IACC schedule](https://planomasjid.org/iacc-prayer-timings-2026/) and supplies the actual post-prayer start time. If no class is scheduled, Monday's scheduler asks the TA to schedule Saturday. Automatic prayer-table updates can be added after agreeing the offset after iqamah.

### Commands

Everyone: `/nextclass`, `/rsvp`, `/report`, `/mizan`, `/resources`, `/status`, `/nudges`, `/help`.

TA/server manager: `/member`, `/schedule`, `/poll`, `/confirm_slot`, `/cancel`, `/attendance`, `/classheld`, `/reopen`, `/waive`, `/wuphf`, `/beeminder`, `/export`.

Hiatus changes future rosters. Existing classes retain a snapshot; mark those absences excused explicitly. No automatic late-arrival deductions. Partial attendance earns zero until the TA decides. The class's local **start date** determines its calendar month.

## WUPHF setup

Set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_PHONE_NUMBER` privately in Railway. Add one variable per recipient phone in E.164 format; map Discord IDs to variable names in `WUPHF_CONTACTS_JSON`.

Recipients use `/nudges sms:true calls:false allow_fajr:true` to opt into SMS, including early-morning reminders. Calls require a separate `calls:true` opt-in. The command explains preferences privately. `/nudges` with defaults turns both off.

TA uses `/wuphf person:@Name method:sms` (or `calls`). Uses the next class's actual time and location. WUPHF is manual in v1; normal Discord reminders are automatic. Limits: one attempt per recipient per hour, three per local day. Quiet hours 21:00–08:00 unless the recipient explicitly opts into Fajr-time nudges. Timeout outcomes are not automatically retried, avoiding duplicate calls.

Twilio may require sender registration/verification and verified destinations on trial accounts. SMS STOP handling is provided by Twilio; recipients can also disable nudges in Discord. A successful request means Twilio accepted it, not confirmed delivery. No private numbers are displayed in the channel or stored in class exports.

## Deploy on Railway, automatically from main

See [RAILWAY.md](RAILWAY.md) for the exact setup. Do not reuse or alter the Todoist Timer service: this is a separate bot and volume.

The repository includes a multi-stage Dockerfile that runs the tests before producing a deployable runtime image, plus a GitHub Actions test workflow. Health checks, restart settings, and storage are configured on the Railway service. **The service is connected to `ihsanbawa/arab-standly` on `main`: new pushes automatically deploy.** Secrets still need account-owner setup. The optional Wait for CI setting is not required for the Docker build-time test gate. New Railway services no longer accept the deprecated `railway.json` format; this service uses its live Railway settings instead.

One process/replica only. SQLite lives on a Railway volume. Bot startup refuses Railway deployment without a volume. No public website/domain is necessary; Discord connects outbound. Health checks return 503 until Discord and the scheduler are ready.

## Operating notes

- Schedules, buttons, scores, vote state and reminder markers persist across deployments.
- Discord send operations and database writes cannot share one transaction. A crash immediately after sending can repeat a notification; scores/financial records are independently idempotent.
- Health checks, monthly finance alerts, and persistent state reduce risk; use Railway volume backups and monitor failures, especially at month-end.
- Tokens live in environment variables. Never import Standly's database or credentials into this repo.
- Export records privately and review the audit trail before correcting financial months.

Sources: [Discord interactions](https://discordpy.readthedocs.io/en/stable/interactions/api.html), [Railway autodeploys](https://docs.railway.com/deployments/github-autodeploys), [Railway volumes](https://docs.railway.com/volumes), [Beeminder API](https://api.beeminder.com/), [Twilio Messaging](https://www.twilio.com/docs/messaging/api/message-resource).
