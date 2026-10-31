# Shaykh Standly · ⚖️ Al-Mīzān

A Discord bot for a four-person **in-person** Arabic class. Coordinates Saturday classes after Fajr congregation at **IACC, Plano**, collects commitments, reschedules with polls, keeps monthly scores, and offers opt-in WUPHF phone nudges.

## Included

- A persistent button panel: schedule, RSVP, class controls, scores, links, roster, private nudge preferences and TA settings. Every slash command works with no required arguments; structured menus guide any remaining choices.
- Automatic Wednesday 6 PM (Dallas time) RSVP invitations for Saturday, once the four-person roster is registered.
- Exactly two RSVP buttons: **I can come / I can’t**. Declines require a replacement suggestion, automatically open or extend a group availability poll, and apply one Al-Mizan deduction.
- After-class TA recap prompt; completed lesson, next lesson and preparation carry into the following invitation.
- Class announcement cards with physical location, topic, preparation, RSVP deadline, and Quizlet links.
- Persistent two-choice RSVP and per-time rescheduling buttons; they work after restarts.
- RSVP nudges, conflict notifications, day-before and one-hour reminders.
- Unanimous rescheduling: the earliest time explicitly approved by all four locks automatically. Votes for that time become new RSVPs; other old responses never carry over.
- Trust-based attendance: no routine roll call if everyone accepted. TA confirms class actually happened after its scheduled end; accepted participants count as present unless exceptions were recorded.
- Disruption reports from participants. Reporter is never automatically blamed or deducted.
- Al-Mīzān: attend +1; cannot attend −1, or −2 within 24 hours; causing a move after accepting −1; last-minute move/unexcused absence −2. One strongest disruption penalty per person per class, not stacked penalties. Excuses/waivers and corrections supported.
- Monthly standings start at zero without deleting previous months. Personal attendance streaks and group monthly-success streaks continue across month boundaries.
- 🟢/🟡/🔴/🚨 monthly danger status on class cards, with one daily action summary and same-day escalation. Only fully attended four-person classes count toward 2/month.
- Private #shaykh-debug testing channel. Commands there use an isolated test session automatically.
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

1. Use the permanent **Shaykh Standly** panel or `/standly`. Invite the four people, then select them with **Class roster**.
2. Every **Wednesday at 6 PM America/Chicago**, the bot announces Saturday’s class at IACC after Fajr. If setup happens later in the week it catches up. It uses verified IACC iqamah + an adjustable 15 minutes, for a 60-minute class.
3. The class card has exactly **I can come / I can’t**. No separate late/change/report controls on that invitation. TA actions remain on the main panel and post-class prompt.
4. **I can’t** records −1 (−2 within 24 hours), immediately opens a replacement-time picker, and posts a curated Arabic roast. Pick a suggested date or enter a custom Dallas date/time. The bot automatically posts an availability poll and marks the proposer available. Subsequent suggestions extend the same open poll without erasing existing votes, up to five alternatives. Closing the picker leaves a visible pending suggestion; the bot nudges them after 12 hours. No one can propose on behalf of another member outside Test mode.
5. Every proposed time gets its own RSVP card with **I can come / I can’t** and all four names: ✅ coming, ❌ can’t, or ⏳ awaiting response. Each answer refreshes the public cards. Repeated yes clicks keep a yes; a no replaces it and immediately opens the alternative-time picker. Availability votes do not add deductions beyond the original class decline. Everyone answers each proposed time. As soon as an option has all four votes, the bot chooses the earliest unanimously available time, updates the class and reminders, and records those four votes as RSVPs. No TA approval or override exists. An unanswered member is never counted as agreement. If all four answer every proposed time without a common time, or the poll expires, the bot closes the round and asks all four for new suggestions; previous votes are not carried to a new round. The original class remains until agreement. Decisions and pending announcements survive a restart. Only the strongest deduction counts per person/class; repeated clicks cannot stack deductions. Marking an emergency excused removes the decline/absence penalty; a separate confirmed-move penalty requires a waiver.
6. After class ends, the bot nudges the TA: **Did class happen? What did we cover, and what comes next?** Tap **Class happened · add recap** and fill in completed work, next lesson and optional prep. Accepted RSVPs count as present; use **Attendance exception** for anyone else. An unresolved attendance record saves the recap as a draft and offers controls to finish it.
7. Confirming the recap records the held class and individual attendance points once. A partial-group meeting is recorded but contributes zero toward the group’s two-class target. The next Wednesday’s card includes **Where we left off**, the next lesson, preparation, and practice links. If next week was manually announced early, saving the recap refreshes its lesson details too. Older recaps cannot override a newer lesson handoff.
8. A class with no recap prompts the TA again on following days after 9 AM. A class that did not happen uses **Class didn’t happen** and a replacement poll, without earning held-class credit.

Financially audited months retain their existing protections. Individual scores and the group’s two-held-classes monthly commitment remain separate. Beeminder and WUPHF still require their private configuration and participant opt-ins.

### Rehearse the whole week alone

Open **#shaykh-debug → Start weekly rehearsal**, or `/testmode` inside that private channel. The primary controls are five numbered steps:

1. **Run midweek RSVP** moves the test clock to Wednesday at 6 PM and runs the actual automatic check-in.
2. **Answer as members** lets you play all four people with the same two responses. Test declines deduct test points and require a replacement time; a private test poll opens automatically.
3. **Vote as all four** shows the same reschedule RSVP card and visible four-person response list. Switch the simulated member or proposed time using the dropdowns, then tap **I can come / I can’t**. The fourth yes automatically schedules the class; a no immediately offers another time.
4. **Jump after class** runs the actual post-class prompt. Record any attendance exceptions, then fill in the recap and next lesson.
5. **Next week’s announcement** runs the next Wednesday and shows the carried lesson details, streaks and monthly danger status.

**Streaks & danger tests** opens optional tools for clock jumps, poll-voter simulation, private nudge settings and the mock group-commitment preview. Its streak example resets the private test session and creates two successful sample months; its month-end button runs the real danger checks. **Reset test session** starts over.

Each TA gets separate in-memory records. Test messages are private, marked TEST ONLY, and suppress mentions. Live members, records, SMS/calls and Beeminder writes are untouched. WUPHF delivery is mocked while keeping the consent, quiet-hour and frequency checks. Test records disappear on reset or deployment. Test errors stay inside test controls rather than directing the user to live `/status`.

### Streaks, danger and attendance display

Every class card uses ✅ coming/present, ❌ unavailable/absent, ⏳ unanswered, 📅 replacement suggested, 🤍 excused and 🟠 partial. A separate 4/4 agreement field shows whether the time is locked. RSVPs are intention; recorded attendance replaces them on completed class cards.

- Personal streak: consecutive attended classes, with current and best values. An unexcused absence resets it, including a no-show whose class is subsequently moved to a makeup. Excused or partial attendance pauses without incrementing. Correcting attendance recomputes the streak.
- Group monthly streak: consecutive completed months with two classes at which all four were present. A current month that has already met the target can join the streak; an unfinished current month does not prematurely break the previous streak. A failed month breaks it once that month ends. Al-Mizan’s monthly score reset does not erase streaks.
- 🟢 target met: 2 full-group classes completed. 🟡 on track: enough future classes explicitly agreed by all four, still awaiting attendance. 🔴 at risk: insufficient agreed future classes. 🚨 urgent: final seven calendar days and fewer than two full-group classes completed. Overlapping sessions cannot make the target look safe.
- One daily summary lists the missing RSVPs, replacement agreement or additional class needed. A worsening severity can alert again that day. The bot does not claim Beeminder charges are active when the integration is off.

The private **#shaykh-debug** channel must deny View Channel to @everyone and allow the owner and bot. The bot discovers it by name on startup and posts a separate test panel. Test mode refuses to start in the member channel; ordinary commands used in the debug channel route to an isolated test bot/store. The live class panel contains no test button. Existing private ephemeral test messages from earlier versions were visible only to their creator.

### Prayer-relative schedule

The bundled snapshot comes from the [official 2026 IACC timetable](https://planomasjid.org/wp-content/uploads/2025/12/IACC-Prayer-Timings-2026.pdf), verified October 30, 2026, for October–December 2026. It uses the **second time under Fajr (iqamah)**, not adhan. Source URL, verification date and source PDF hash are retained in `mizan/iacc_2026.json`.

Timezone is `America/Chicago`, including the November clock change. New classes default to **Saturday, iqamah + 15 minutes, 60 minutes at IACC Plano**. The 15 minutes is an adjustable class-planning allowance, not a published prayer end time. Settings offer 10/15/20/30 minutes; existing invitations never silently move. Examples: October 31 iqamah 06:45 CDT → class 07:00; November 7 iqamah 06:00 CST → class 06:15; November 21 iqamah 06:15 CST → class 06:30.

A new verified timetable must be loaded before 2027. The bot alerts the TA and refuses to reuse old-year times. A TA can still enter a verified custom time through the scheduling form. Wednesday 6 PM invitations require exactly four active roster members and can be paused in TA settings. It creates one regular invitation for each Saturday's week, with persistent deduplication, and retries an unposted invitation after a restart without creating a second class. Scores and financial credit are never inferred from creating an invitation.

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
