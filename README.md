# Shaykh Standly · ⚖️ Al-Mīzān

A Discord bot for a four-person **in-person** Arabic class. Coordinates Saturday classes after Fajr congregation at **IACC, Plano**, collects commitments, reschedules with polls, keeps monthly scores, and offers group-authorised WUPHF phone nudges.

## Included

- A persistent button panel: schedule, RSVP, class controls, scores, links, roster, WUPHF for any server member and TA settings. Every slash command works with no required arguments; structured menus guide any remaining choices.
- Automatic Wednesday 6 PM (Dallas time) RSVP invitations for Saturday, once the four-person roster is registered.
- Exactly two RSVP buttons: **I can come / I can’t**. Declines require a replacement suggestion, automatically open or extend a group availability poll, and apply one Al-Mizan deduction.
- After-class TA recap prompt; completed lesson, next lesson and preparation carry into the following invitation.
- Class announcement cards with physical location, topic, preparation, RSVP deadline, and Quizlet links.
- Persistent RSVP and shared availability controls; they work after restarts.
- RSVP nudges, conflict notifications, day-before and one-hour reminders.
- Unanimous rescheduling: the earliest time explicitly approved by all four locks automatically. Votes for that time become new RSVPs; other old responses never carry over.
- Trust-based attendance: no routine roll call if everyone accepted. TA confirms class actually happened after its scheduled end; accepted participants count as present unless exceptions were recorded.
- Disruption reports from participants. Reporter is never automatically blamed or deducted.
- Al-Mīzān: attend +1; cannot attend −1, or −2 within 24 hours; causing a move after accepting −1; last-minute move/unexcused absence −2. One strongest disruption penalty per person per class, not stacked penalties. Excuses/waivers and corrections supported.
- Monthly standings start at zero without deleting previous months. Personal attendance streaks and group monthly-success streaks continue across month boundaries.
- 🟢/🟡/🔴/🚨 monthly danger status on class cards, updated in place without duplicate standalone danger alerts. Only fully attended four-person classes count toward 2/month.
- Private #shaykh-debug testing channel. Commands there use an isolated test session automatically.
- Curated Arabic roasts with translation reveals and vocabulary notes.
- Shared minimum of two classes per calendar month, at-risk alerts, unresolved-makeup reminders.
- Private record export, restricted TA commands, audit trail, persistent SQLite storage.
- Beeminder **group** monthly-compliance adapter, disabled until configured. See [BEEMINDER.md](BEEMINDER.md).
- Automatic Discord → SMS → call RSVP escalation and member-triggered `/wuphf @person`; group-authorised defaults, personal stop/resume, quiet hours, cooldown and daily limits.

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
4. **I can’t** records −1 (−2 within 24 hours), immediately opens a replacement-time picker, and posts a curated Arabic roast. Select all workable suggested dates and tap **Suggest selected times**, or enter up to three custom Dallas date/times. The selected batch is saved atomically and posted as one shared poll. The bot automatically posts an availability poll and marks the proposer available. Subsequent suggestions extend the same open poll without erasing existing votes, up to three alternatives. Closing the picker leaves a visible pending suggestion; the bot nudges them after 12 hours. No one can propose on behalf of another member outside Test mode.
5. One shared rescheduling card shows up to three proposed times, each with all four names and ✅ available / ❌ unavailable / ⏳ unanswered. **Update my availability** opens a private multi-select menu: select every workable time and **Save availability**, or choose **None work for me**. Saving records all displayed options atomically. Nothing changes while selecting. New suggestions preserve prior answers and add an unanswered option; a ballot opened before a new suggestion must be reopened before saving. The proposer is automatically available for their suggestion. As soon as all four approve a time, it locks automatically and becomes the class's four RSVPs. If several qualify together, the earliest wins. No TA approval or override exists. Once everyone answers every option without agreement, or the deadline expires, a fresh suggestion round opens. None-work answers immediately offer another time and leave a pending reminder if abandoned. Replacement votes incur no additional deduction. Only the strongest original disruption deduction counts per person/class.
After unanimous rescheduling, the bot posts the full class invitation at the bottom of the channel with **Confirmed**, all four ✅ RSVPs, the new time, preparation, streaks and monthly progress. This replaces the separate plain-text class-moved announcement. The earlier card becomes a snapshot; future changes update the fresh card. Anyone whose plans change taps **I can’t** on the current card and suggests their workable alternatives; all four must agree again. These are confirmed intentions, not recorded attendance or streak credit, which are finalized after class.

6. After class ends, the bot nudges the TA: **Did class happen? What did we cover, and what comes next?** Tap **Class happened · add recap** and fill in completed work, next lesson and optional prep. Accepted RSVPs count as present; use **Attendance exception** for anyone else. An unresolved attendance record saves the recap as a draft and offers controls to finish it.
7. Confirming the recap records the held class and individual attendance points once. A partial-group meeting is recorded but contributes zero toward the group’s two-class target. The next Wednesday’s card includes **Where we left off**, the next lesson, preparation, and practice links. If next week was manually announced early, saving the recap refreshes its lesson details too. Older recaps cannot override a newer lesson handoff.
8. A class with no recap prompts the TA again on following days after 9 AM. A class that did not happen uses **Class didn’t happen** and a replacement poll, without earning held-class credit.

Financially audited months retain their existing protections. Individual scores and the group’s two-held-classes monthly commitment remain separate. Beeminder and WUPHF still require their private configuration. Phone nudges use the group’s agreed permission and honour individual stop controls.

### Rehearse the whole week alone

Open **#shaykh-debug → Start weekly rehearsal**, or `/testmode` inside that private channel. The primary controls are five numbered steps:

1. **Run midweek RSVP** moves the test clock to Wednesday at 6 PM and runs the actual automatic check-in.
2. **Answer as members** lets you play all four people with the same two responses. Test declines deduct test points and require a replacement time; a private test poll opens automatically.
3. **Vote as all four** shows the shared availability board. Choose a simulated member, open **Update my availability**, select every workable time and save. The fourth agreeing ballot moves class automatically. **None work for me** offers another suggestion.
4. **Jump after class** runs the actual post-class prompt. Record any attendance exceptions, then fill in the recap and next lesson.
5. **Next week’s announcement** runs the next Wednesday and shows the carried lesson details, streaks and monthly danger status.

**Streaks & danger tests** opens optional tools for clock jumps, poll-voter simulation, WUPHF rehearsal tools and the mock group-commitment preview. Its streak example resets the private test session and creates two successful sample months; its month-end button runs the real danger checks. **Reset test session** starts over.

Each TA gets separate in-memory records. Test messages are private, marked TEST ONLY, and suppress mentions. Live members, records, SMS/calls and Beeminder writes are untouched. WUPHF delivery is mocked while keeping stop, quiet-hour and frequency checks. Test records disappear on reset or deployment. Test errors stay inside test controls rather than directing the user to live `/status`.

### Streaks, danger and attendance display

Every class card uses ✅ coming/present, ❌ unavailable/absent, ⏳ unanswered, 📅 replacement suggested, 🤍 excused and 🟠 partial. A separate 4/4 agreement field shows whether the time is locked. RSVPs are intention; recorded attendance replaces them on completed class cards.

- Personal streak: consecutive attended classes, with current and best values. An unexcused absence resets it, including a no-show whose class is subsequently moved to a makeup. Excused or partial attendance pauses without incrementing. Correcting attendance recomputes the streak.
- Group monthly streak: consecutive completed months with two classes at which all four were present. A current month that has already met the target can join the streak; an unfinished current month does not prematurely break the previous streak. A failed month breaks it once that month ends. Al-Mizan’s monthly score reset does not erase streaks.
- 🟢 target met: 2 full-group classes completed. 🟡 on track: enough future classes explicitly agreed by all four, still awaiting attendance. 🔴 at risk: insufficient agreed future classes. 🚨 urgent: final seven calendar days and fewer than two full-group classes completed. Overlapping sessions cannot make the target look safe.
- The scheduling card shows monthly danger and refreshes as the group target or confirmed capacity changes. No separate daily or escalation danger message is posted. Normal RSVP and replacement-response reminders remain.

The private **#shaykh-debug** channel must deny View Channel to @everyone and allow the owner and bot. The bot discovers it by name on startup and posts a separate test panel. Test mode refuses to start in the member channel; ordinary commands used in the debug channel route to an isolated test bot/store. The live class panel contains no test button. Existing private ephemeral test messages from earlier versions were visible only to their creator.

### Prayer-relative schedule

The bundled snapshot comes from the [official 2026 IACC timetable](https://planomasjid.org/wp-content/uploads/2025/12/IACC-Prayer-Timings-2026.pdf), verified October 30, 2026, for October–December 2026. It uses the **second time under Fajr (iqamah)**, not adhan. Source URL, verification date and source PDF hash are retained in `mizan/iacc_2026.json`.

Timezone is `America/Chicago`, including the November clock change. New classes default to **Saturday, iqamah + 15 minutes, 60 minutes at IACC Plano**. The 15 minutes is an adjustable class-planning allowance, not a published prayer end time. Settings offer 10/15/20/30 minutes; existing invitations never silently move. Examples: October 31 iqamah 06:45 CDT → class 07:00; November 7 iqamah 06:00 CST → class 06:15; November 21 iqamah 06:15 CST → class 06:30.

A new verified timetable must be loaded before 2027. The bot alerts the TA and refuses to reuse old-year times. A TA can still enter a verified custom time through the scheduling form. Wednesday 6 PM invitations require exactly four active roster members and can be paused in TA settings. It creates one regular invitation for each Saturday's week, with persistent deduplication, and retries an unposted invitation after a restart without creating a second class. Scores and financial credit are never inferred from creating an invitation.

### Commands

Everyone: `/standly`, `/nextclass`, `/rsvp`, `/report`, `/mizan`, `/resources`, `/status`, `/stopnudges`, `/resumenudges`, `/help`.

TA/server manager: `/member`, `/schedule`, `/poll`, `/confirm_slot`, `/cancel`, `/attendance`, `/classheld`, `/reopen`, `/waive`, `/wuphf`, `/beeminder`, `/export`.

Hiatus changes future rosters. Existing classes retain a snapshot; mark those absences excused explicitly. No automatic late-arrival deductions. Partial attendance earns zero until the TA decides. The class's local **start date** determines its calendar month.

## WUPHF setup

Set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_PHONE_NUMBER` privately in Railway. Add one variable per recipient phone in E.164 format; map Discord IDs to variable names in `WUPHF_CONTACTS_JSON`.

Phone nudges use the group’s agreed permission by default for privately configured recipients. No **My nudges** menu or opt-in step is required. `/stopnudges` stops all phone nudges to the requesting member and cancels pending jobs. `/resumenudges` resumes future jobs, retaining 9 PM–8 AM Dallas quiet hours. Existing explicit stop preferences are respected.

**Automatic unanswered RSVP escalation:** once the RSVP deadline is reached, send the normal Discord DM/fallback mention. Queue SMS two hours after that nudge, then one call two hours after the actual SMS, only if no RSVP has been recorded. Any RSVP—including unavailable—stops escalation. A changed revision, cancellation, removed participant, hiatus, or class starting also stops it. Each class/revision/person gets one automatic sequence.

**Member-triggered WUPHF:** anyone in the configured Discord server can use `/wuphf @person` or **WUPHF someone**; no TA role or scheduled class is needed. The target must have a privately configured number. Post one public mention with an **I’m here** button, queue SMS immediately, then one call an hour after the actual SMS unless acknowledged. Only the recipient can acknowledge the job. One active sequence per recipient; manual jobs expire after 24 hours.

All phone sends share a one-hour recipient cooldown, three-attempt local daily limit and quiet hours. Queues wait for permissible hours/capacity. Jobs and timestamps survive deployment. A provider timeout/error is terminal and surfaces for review; a restart during a provider request does not retry or advance to calls. There is no repeat-dial loop.

**Test mode:** Streaks & danger tests → advanced tools → **Test automatic WUPHF** rehearses Discord, text, call and cancellation with a fake clock and mocked phone provider. No real number is contacted.

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

### Al-Mizan report

A single **public Al-Mizan board** in the configured class channel shows the monthly leader, all four balances, earned/lost points, personal streaks, group progress, and cumulative scores from the full ledger. **This month / All-time** changes its ranking for everyone. The board refreshes in place when scores change and survives restarts. `/mizan` and the panel link to this shared report rather than sending a separate report per member. **Point history** opens a private drill-down; old months remain available. Corrections and waivers update the real ledger totals. Debug rehearsals remain isolated and private.

### Private Arabic welcome

With **Server Members Intent** enabled in Discord Developer Portal and `WELCOME_ON_JOIN=true`, each human member receives one private DM explaining the Arabic greeting, Saturday/Fajr class, RSVP and unanimous rescheduling, Al-Mizan, attendance, resources and optional phone nudges. Existing members receive it once on first enabled startup. Delivery markers persist across restarts and rejoining, and bots/other guilds are ignored. If Discord blocks DMs, no public welcome is substituted; the member can use **Welcome / how it works** or `/welcome` for a private copy. Welcome does not register someone in the class roster or opt them into WUPHF.

`CLASS_CHANNEL_ID` can move the live class destination to #general. The bot preserves scores/rosters/audit history and recreates live controls there once. Archive the old channel in Discord without deleting its history; private rehearsals remain in #shaykh-debug.

### Shaykh Standly’s voice

A playful, stern Arabic teacher: practical reminders, dry humour, and roasts about excuses, snoozing and missing RSVPs. The curated 24-line Arabic collection includes English spoiler translations and vocabulary notes. Each member gets a persistent rotation through the collection before a line repeats. The Arabic welcome explains the rules with the same voice; roasts never replace scheduling information or a clear next action.
