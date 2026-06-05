# Cloudflare Workers Free Deployment

## 1. Prepare Supabase

Run `supabase/worker-free-schema.sql` in Supabase SQL editor.

Required existing tables from the Flask app:

- `trips`
- `expenses`
- `showtimes`
- `showtime_events`
- `schedules`
- `daily_summaries`
- Storage bucket `trip-exports`

## 2. Set Cloudflare secrets

After `wrangler login`, this project can import available values from `.env`:

```powershell
.\scripts\set_worker_secrets.ps1
```

Manual equivalent:

```bash
npx wrangler secret put LINE_CHANNEL_ACCESS_TOKEN
npx wrangler secret put LINE_CHANNEL_SECRET
npx wrangler secret put SUPABASE_URL
npx wrangler secret put SUPABASE_SERVICE_ROLE_KEY
npx wrangler secret put SUPABASE_ANON_KEY
npx wrangler secret put OCR_SPACE_API_KEY
npx wrangler secret put CRON_SECRET
```

Optional for Excel export through GitHub Actions:

```bash
npx wrangler secret put GITHUB_TOKEN
npx wrangler secret put GITHUB_REPO
```

`GITHUB_REPO` format: `owner/repo`.

## 3. Set GitHub secrets

For `.github/workflows/export-trip.yml`:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_KEY` or `SUPABASE_ANON_KEY`
- `LINE_CHANNEL_ACCESS_TOKEN`

For manual cron fallback workflows:

- `WORKER_BASE_URL`
- `CRON_SECRET`

## 4. Deploy

```bash
npm install
npm run typecheck
npx wrangler deploy
```

Then set LINE webhook URL to:

```text
https://<worker-name>.<account>.workers.dev/callback
```

## 5. Cutover checklist

- Test LINE webhook verify in LINE Developers console.
- Send `help`, `ทริป`, add expense, `ยอด`, image slip, `showtime`.
- Run `/api/config-status`.
- Confirm Cloudflare cron logs for `/api/check-showtime` and `/api/daily-summary`.
- Keep Railway on until LINE tests pass, then shut Railway down.

Current deployed Worker URL:

```text
https://line-trip-bot.icecrowice.workers.dev/callback
```
