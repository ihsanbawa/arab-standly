# Shaykh Standly · ⚖️ Al-Mīzān

A Discord bot for a four-person **in-person** Arabic class. Coordinates Saturday classes after Fajr congregation at **IACC, Plano**, collects commitments, reschedules with polls, keeps monthly scores, and offers opt-in WUPHF phone nudges.

## Included

- A persistent button panel: schedule, RSVP, class controls, scores, links, roster, private nudge preferences and TA settings. Every slash command works with no required arguments; structured menus guide any remaining choices.
- Automatic weekly Saturday invitations once the four-person roster is registered.
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

1. In the [Discord Developer Portal](https://discord.com/developers/applications), create **Shaykh Standly** and open its Bot settings. The current application is [Shaykh Standly](https://discord.com/developers/applications/1565804169057796126/information).
2. Set its token privately as `DISCORD_BOT_TOKEN`. Never put it in chat, GitHub, screenshots, or bot commands.
3. Invite it to your class server using OAuth2 scopes `bot` and `applications.commands`.
4. Give it View Channels, Send Messages, Embed Links, Read Message History, and Attach Files **only in the class channel**. Administrator and privileged member/message-content intents are unnecessary.
5. Enable Discord Developer Mode, copy the server ID into `DISCORD_GUILD_ID`, and your user ID into `BOT_OWNER_ID`.
6. Run `/setup` in the announcements channel (no arguments needed; an optional TA role is still supported). The first setup requires Manage Server or configured owner access.
7. Invite all four participants (teachers included), then use **Class roster** to select them together. Weekly invitations start automatically once all four are on the roster.

## Everyday flow

1. Use the permanent **Shaykh Standly** panel in `#class-announcements`, or open `/standly` or `/help`.
2. **Class roster**: select the four people in one picker; use **Add me** for yourself. A hiatus affects future classes only.
3. The bot automatically announces the upcoming Saturday once the roster is complete, then opens the next week's invitation after Saturday's start. **Schedule class** also previews a Saturday with location, duration, lesson and preparation already filled. Tap **Announce & ask for RSVPs**, select a different Saturday, or edit the filled form. Existing weeks are not duplicated after a move, cancellation or restart.
4. Everyone taps **Coming / Running late / Can't attend / Request another time**. Invitations mention each roster member. RSVP deadline defaults to 48 hours before class, or soon if scheduled late. Discord reminders and unanswered-RSVP nudges follow automatically.
5. **TA class controls** on the invitation (or **Class controls** on the panel) lets you record attendance exceptions and confirm **Class happened**. Accepted RSVPs count as present only after that confirmation. No routine roll call.
6. **Reschedule / poll** proposes two Saturdays and an optional Sunday using the official IACC iqamah times. Tap to post or edit three separately labeled fields. Poll deadline defaults to 24 hours, ending at least one hour before its earliest option. Class stays unchanged until a TA confirms a replacement.
7. **Confirm poll time**: pick the poll, new time and responsibility (defaults to an explicit **No individual penalty** choice). Review known conflicts and any score consequence, then confirm. Everyone RSVPs again.
8. **Class didn't happen**, attendance penalties, reopening and sending WUPHF all show a review before applying. Reports and penalty waivers ask only for a short reason. Reporting a conflict does not assign blame.
9. **Al-Mizan** has a month picker. **My nudges** has independent SMS, calls and Fajr-hours toggles. **TA settings** controls the after-prayer allowance, default lesson/prep, weekly invitations, status, Beeminder checks and private exports.

Financially audited months remain sealed; no menu bypasses the existing audit guards. Excused/partial attendance earns zero. A confirmed accepted-commitment move is −1 (−2 last minute); a no-show is −2, with only the strongest disruption penalty per person/class.

### Test mode (one-person rehearsal)

Tap **Test mode** on the live panel or open `/testmode`. TA access is checked on every test interaction. Each TA gets a separate in-memory class with themselves and three simulated members. All test outputs are private, visibly marked TEST ONLY, and suppress mentions.

- **Simulate weekly invitation** or use **Open test class controls → Schedule class**. The normal scheduling/attendance/rescheduling/score menus operate on the test database.
- **Play the other members** simulates each person's RSVP; **Play poll votes** toggles each person's availability without needing their account.
- **Move test clock** jumps to three days before class, one hour before, or after its end, allowing reminder, late-change and class-held flows to be rehearsed.
- **Preview group commitment** shows the same shared monthly success/failure idea; unresolved classes block the mock audit.
- WUPHF uses mock delivery, while exercising the real opt-in, quiet-hour and frequency checks. No phone number or Twilio credentials are needed for testing. Beeminder is disabled in test mode, regardless of live configuration; no goals, financial datapoints or charges are created.
- **Reset test session** discards everything and invalidates older test controls. Test records also disappear on deployment/restart. The live scheduler never processes test sessions.

Use the normal server panel for real actions. Test mode's controls cannot reach the live class database or real announcement channel.

### Prayer-relative schedule

The bundled snapshot comes from the [official 2026 IACC timetable](https://planomasjid.org/wp-content/uploads/2025/12/IACC-Prayer-Timings-2026.pdf), verified October 30, 2026, for October–December 2026. It uses the **second time under Fajr (iqamah)**, not adhan. Source URL, verification date and source PDF hash are retained in `mizan/iacc_2026.json`.

Timezone is `America/Chicago`, including the November clock change. New classes default to **Saturday, iqamah + 15 minutes, 60 minutes at IACC Plano**. The 15 minutes is an adjustable class-planning allowance, not a published prayer end time. Settings offer 10/15/20/30 minutes; existing invitations never silently move. Examples: October 31 iqamah 06:45 CDT → class 07:00; November 7 iqamah 06:00 CST → class 06:15; November 21 iqamah 06:15 CST → class 06:30.

A new verified timetable must be loaded before 2027. The bot alerts the TA and refuses to reuse old-year times. A TA can still enter a verified custom time through the scheduling form. Weekly invitations require exactly four active roster members and can be paused in TA settings. It creates one regular invitation for each Saturday's week, with persistent deduplication, and retries an unposted invitation after a restart without creating a second class. Scores and financial credit are never inferred from creating an invitation.

### Commands

Everyone: `/standly`, `/nextclass`, `/rsvp`, `/report`, `/mizan`, `/resources`, `/status`, `/nudges`, `/help`.

TA/server manager: `/member`, `/schedule`, `/poll`, `/confirm_slot`, `/cancel`, `/attendance`, `/classheld`, `/reopen`, `/waive`, `/wuphf`, `/beeminder`, `/export`.

Hiatus changes future rosters. Existing classes retain a snapshot; mark those absences excused explicitly. No automatic late-arrival deductions. Partial attendance earns zero until the TA decides. The class's local **start date** determines its calendar month.

## WUPHF setup

Set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_PHONE_NUMBER` privately in Railway. Add one variable per recipient phone in E.164 format; map Discord IDs to variable names in `WUPHF_CONTACTS_JSON`.

Recipients tap **My nudges** (or open `/nudges` without arguments) and toggle SMS, calls and Fajr-hours independently. **Turn all off** disables every method. Advanced command flags change only the explicitly supplied preference; opening the menu never changes consent.

TA uses **TA settings → Send WUPHF**, picks a person and method, then confirms. Advanced `/wuphf` flags are still supported. Uses the next class's actual time and location. WUPHF is manual in v1; normal Discord reminders are automatic. Limits: one attempt per recipient per hour, three per local day. Quiet hours 21:00–08:00 unless the recipient explicitly opts into Fajr-time nudges. Timeout outcomes are not automatically retried, avoiding duplicate calls.

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
