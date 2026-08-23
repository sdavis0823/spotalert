# Deploy SpotAlert on Fly.io (cheapest — beats JetTip)

**Cost:** ~**$3.30/month** for the always-on instance + ~$0.15/mo for a 1 GB
volume ≈ **$3.50/mo** ($42/yr). That undercuts JetTip's $5/mo and edges its
$50/yr. Data feeds are free.

You do this from the unzipped project folder. You're on Windows, so use
PowerShell.

## 1. Install flyctl and sign in
```powershell
iwr https://fly.io/install.ps1 -useb | iex
fly auth signup      # or: fly auth login
```
(Signing up asks for a card — Fly bills usage; a tiny app like this lands near
the numbers above.)

## 2. Launch the app (creates it from fly.toml, no deploy yet)
From inside the project folder:
```powershell
fly launch --no-deploy --copy-config
```
- If it says the name `spotalert` is taken, accept the suggested unique name.
- Keep the region it detects, or set one near you (`lax` is preconfigured).
- Say **No** if it offers to add a database (we use SQLite on a volume).

## 3. Create the persistent volume (same name as in fly.toml)
```powershell
fly volumes create spotalert_data --size 1 --region lax
```

## 4. Add your secrets
```powershell
fly secrets set OPENSKY_CLIENT_ID=your-id OPENSKY_CLIENT_SECRET=your-secret
```
Get these free at https://opensky-network.org → Account → API Clients. (Without
them the app still runs on live airplanes.live data; the historical boards use
OpenSky.)

## 5. Deploy
```powershell
fly deploy
fly open          # opens your live https URL
```
On first boot the app creates the database and seeds airports + curated
aircraft (incl. the special-livery registry). Photos load live in your browser.

## 6. Backfill real Phoenix history
```powershell
fly ssh console -C "python -m tools.backfill KPHX --days 14"
```
Optional: load the identity DB first so alerts show real tails (QUICKSTART_KPHX.md, step 3).

## Notes
- **Always-on:** `fly.toml` sets `auto_stop_machines = false` /
  `min_machines_running = 1` so the scheduler never sleeps. Don't change that or
  alerts will stop when the app idles.
- **Cost control:** one shared-cpu-1x/512MB machine + 1 GB volume is the cheap
  path. Adding machines or memory raises the bill.
- **Backups:** the database lives on the volume at `/var/data/jettip.db`.
  `fly ssh console` in and copy it out to back up.
- Prefer the click-through route instead? `DEPLOY.md` covers Render (~$7/mo,
  easiest, no terminal).
