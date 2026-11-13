# Railway deployment and main → production

## One-time account configuration

1. Create a separate Railway project/service named **al-mizan** using **Deploy from GitHub repo**.
2. Choose `ihsanbawa/arab-standly` and set source branch to **main**; keep automatic deploys enabled.
3. **Wait for CI** is an optional extra dashboard setting. The Dockerfile already runs the test suite before producing the runtime image, so failing tests block a Railway build even without that setting.
4. Attach a volume at **/data**. Set `DATABASE_PATH=/data/mizan.sqlite3`.
5. Add `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`, `BOT_OWNER_ID` in service Variables. Keep financial and Twilio integrations disabled until configured.
6. Railway automatically detects the Dockerfile. Set start `python -m mizan.bot`, health path `/health`, timeout 120 seconds, restart on failure (5 retries), port 8080, and one replica. These settings are already configured on the created service. Do not add a deprecated `railway.json` file: new services cannot use that format.
7. Keep deployment overlap at zero for this single-volume worker. Do not enable sleeping/serverless for the continuously connected Discord bot.
8. Deploy and check logs for the Discord connection and command sync. `/status` should work in the class server.
9. Enable Railway volume backups. The persistent database is outside the deployed image.

## Verify autodeployment

After initial deployment, push a small reviewed change to `main`. Verify:

- GitHub's Tests workflow passed.
- Railway started a deployment for **that same commit SHA**.
- Deployment is healthy, `/status` responds, and a previously saved class/RSVP still exists.

This verifies the pipeline; a checked-in config alone does not prove that `main` is connected.

## Provisioning status

A dedicated Railway project/service and a 500 MB volume at `/data` have been prepared. `ihsanbawa/arab-standly` is connected on branch `main`, with no pinned commit: future pushes automatically deploy. The Todoist Timer service is separate. The first image built successfully; startup requires Discord credentials. No financial goals are activated by deploying this code.

## Live integrations

Add Twilio and recipient variables from README. To accept RSVP replies by text, generate a public domain for the service, set `TWILIO_WEBHOOK_URL` to `https://<domain>/twilio/sms`, and point the Twilio number’s incoming-message webhook at the same URL.

For Beeminder, follow BEEMINDER.md and validate every account using `/beeminder` before enabling. Agreed stakes: **$5 initial pledge, $90 cap**. The bot never creates a goal, changes a pledge, or directly charges a card.

## Updates and recovery

Normal update: commit → push `main` → CI → Railway deploy. No repeated manual uploads.

Rollback code through Railway's deployment history. Keep the volume; do not delete it to roll back an application. Record schemas are created on startup; future schema migrations must remain compatible with stored records. A production token must not also be used by a locally running copy.

If a deploy fails, inspect health/logs and variables. Do not set a fake health response or remove the volume guard to make it green. Missing token/server ID/volume is a configuration problem.
