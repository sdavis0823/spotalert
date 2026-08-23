# Deploy SpotAlert on Render

You'll have it live in ~10 minutes for **~$7/month** (Starter instance + a 1 GB
disk for the database). Data feeds are free.

## 1. Put the code on GitHub
Make the **`jettip/` folder the repository root** (so `render.yaml`, `app/`, and
`seed.py` sit at the top of the repo), then push to a new GitHub repo.
```bash
cd jettip
git init && git add . && git commit -m "SpotAlert"
git branch -M main
git remote add origin https://github.com/<you>/spotalert.git
git push -u origin main
```

## 2. Create the service on Render
1. Sign in at https://render.com → **New → Blueprint**.
2. Connect the repo. Render reads `render.yaml` and proposes the `spotalert`
   web service with a 1 GB disk mounted at `/var/data`.
3. Click **Apply**. It builds and starts. On first boot the app auto-creates the
   database and seeds airports + the curated aircraft list (no fake history).

## 3. Add your secrets (Environment tab)
- `OPENSKY_CLIENT_ID` and `OPENSKY_CLIENT_SECRET` — from
  https://opensky-network.org (Account → API Clients). Without these the app
  still runs on airplanes.live live data, but the historical arrival/departure
  boards need OpenSky.
- (Optional) `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` for real web push — see below.

After saving secrets, Render redeploys automatically.

## 4. Backfill real history for your airport (KPHX)
The rarity engine needs history to be trustworthy. From the Render **Shell**
(or locally against the same DB) run:
```bash
python -m tools.backfill KPHX --days 14
```
Optional, to show real tails/types instead of hex codes, load the identity DB
first (see QUICKSTART_KPHX.md, step 3).

## 5. (Optional) Real web push
Push runs in dry-run until you enable it:
1. In the Dockerfile, uncomment the `pip install pywebpush` line (or add
   `pywebpush` to requirements.txt and switch the service to the Docker runtime).
2. Generate a VAPID keypair once and paste the values into the Render env vars
   `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY`. (Any web-push VAPID generator, or
   `python -c "from app import push; print(push.public_key())"` prints the public
   key of an ephemeral pair — for production, generate a stable pair and keep the
   private key secret.)

## Make it an app on your phone (PWA)
Once it's live at your URL, open that URL on your phone's browser:
- **iPhone (Safari):** Share → **Add to Home Screen**.
- **Android (Chrome):** menu → **Install app** / **Add to Home screen**.
It gets an icon and opens full-screen like a native app — same as JetTip — and
can send push notifications (enable in Settings). No app store needed.

## Notes
- **Don't use Render's Free plan** for this — it sleeps after 15 min of
  inactivity, which stops the background scheduler and your alerts. Starter keeps
  it always-on.
- **Photos** load client-side in the visitor's browser directly from
  Planespotters / airport-data, so they don't add to your server bandwidth.
- The SQLite database lives on the mounted disk at `/var/data/jettip.db` and
  survives deploys and restarts. Back it up by downloading that file.
- Other hosts: a `Dockerfile` and `Procfile` are included, so the same repo runs
  on Fly.io, Railway, or any VPS with minimal changes.
