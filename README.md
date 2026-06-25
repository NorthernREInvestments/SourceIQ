# SourceIQ

SourceIQ is a two-stage market intelligence platform for e-commerce sellers and dropshippers. It scans free and paid data sources to score niche opportunities (Stage 1) and estimate Budget / Mid / Premium margins by matching wholesale listings to retail prices (Stage 2).

Use it via the **CLI** for local research or the **web app** for accounts, billing, search history, and team workflows.

---

## What SourceIQ does

### Stage 1 — Category scan (free sources, no ScrapingBee credits)

Scrapes eBay, Bing Shopping, Gumroad, AppSumo, and Google Trends to produce:

- An **opportunity score** (0–100)
- Source breakdown and listing counts
- Google Trends direction
- Top products and suggested drill-down subcategories

### Stage 2 — Margin drill-down (~175 ScrapingBee credits per run)

Scrapes Amazon, Walmart, AliExpress, DHgate, Alibaba, and Made-in-China to produce:

- **Budget / Mid / Premium** tier margin analysis
- Matched sourcing ↔ selling pairs with landed cost
- HIGH / MEDIUM / LOW margin labels and BEST LANDED highlights
- CSV export (Pro tier)

### Web app features

- User accounts with trial, Starter, and Pro tiers
- Stripe subscription checkout
- SendGrid email (password reset, trial expiry, receipts)
- Search history, Pro watchlist, and margin price-history charts
- Admin dashboard at `/admin`

---

## Installation

### 1. Clone and create a virtual environment (recommended)

```bash
cd "Market Spy"
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 2. Install dependencies

**CLI (scraping, analysis, reports):**

```bash
pip install -r requirements.txt
playwright install chromium
```

**Web app (adds FastAPI, Stripe, SendGrid, etc.):**

```bash
pip install -r requirements_web.txt
```

---

## Environment setup (`.env`)

Copy the template and fill in your keys:

```bash
copy .env.example .env    # Windows
cp .env.example .env      # macOS / Linux
```

### Required for full production

| Variable | Purpose |
|----------|---------|
| `SECRET_KEY` | Session cookies and password-reset tokens |
| `SCRAPINGBEE_API_KEY` | Stage 2 scraping (Amazon, Walmart, wholesale sites) |
| `STRIPE_SECRET_KEY` | Subscription checkout |
| `SENDGRID_API_KEY` | Transactional email |

### Recommended

| Variable | Purpose |
|----------|---------|
| `STRIPE_WEBHOOK_SECRET` | Stripe subscription renewals / cancellations |
| `STARTER_PRICE_ID` / `PRO_PRICE_ID` | Stripe Price IDs for each plan |
| `FROM_EMAIL` | SendGrid sender address |
| `APP_BASE_URL` | Public URL for email links and Stripe redirects |
| `ADMIN_PASSWORD` | Protects `/admin` dashboard (HTTP Basic Auth) |

### Optional

| Variable | Purpose |
|----------|---------|
| `USER_SCRAPINGBEE_API_KEY` | CLI: use your own ScrapingBee key |
| `SOURCEIQ_TIER` | CLI tier override (`trial`, `starter`, `pro`) |
| Reddit / eBay / Etsy keys | Enhanced CLI scraping when configured |

> **Never commit `.env` to git.** It is listed in `.railwayignore` for deployments.

On startup, the web app prints a **warning** (not a crash) if core keys are missing so local development can proceed with reduced functionality.

---

## Running locally

### CLI

```bash
# Stage 1 — single niche
python run.py "dog collar"

# Stage 2 — margin drill-down
python run.py "pet supplies" --drill-down "dog collar"

# Quick Start — 12 preset niches, ranked by score
python run.py --quick-start

# Advanced output + CSV export (Pro)
python run.py "dog collar" --drill-down "nylon dog collar" --advanced --export-csv
```

Preset Quick Start niches: home decor, pet supplies, fitness gear, kitchen gadgets, beauty tools, phone accessories, outdoor gear, baby products, car accessories, gaming accessories, jewelry, yoga equipment.

### Web app

```bash
uvicorn market_spy.web.app:app --reload --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000)

- Register → dashboard → search
- Health check: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)
- Admin: [http://127.0.0.1:8000/admin](http://127.0.0.1:8000/admin) (browser will prompt for password; use `ADMIN_PASSWORD`)

---

## Deploying to Railway

1. Push the repo to GitHub and create a new **Railway** project from the repository.
2. Set environment variables in the Railway dashboard (same as `.env` above).
3. Railway reads `Procfile` and `railway.toml`:
   - Start command: `uvicorn market_spy.web.app:app --host 0.0.0.0 --port $PORT`
   - Health check: `GET /health`
4. Add a **Stripe webhook** pointing to `https://your-app.up.railway.app/webhook`.
5. Set `APP_BASE_URL` to your public Railway URL.

`.railwayignore` excludes `output/`, `__pycache__/`, `.env`, and `*.pyc` from the build artifact.

---

## Tier limits

### Web app (SQLite `users.db`)

| Tier | Stage 1 / month | Stage 2 / month | Notes |
|------|-----------------|-----------------|-------|
| Trial | 10 | 3 | 7-day trial, then `none` (no searches) |
| Starter | 30 | 5 | Paid via Stripe |
| Pro | 100 | 25 | CSV export, watchlist, price history |
| Pro + own ScrapingBee key | 100 | 50 | User adds key in Account settings |
| None (expired) | 0 | 0 | After trial ends without upgrade |

### CLI (`user_session.json`)

| Tier | Stage 1 / month | Stage 2 / month |
|------|-----------------|-----------------|
| Trial | 10 | 3 |
| Starter | 30 | 5 |
| Pro | 100 | 25 |
| Pro + own ScrapingBee key | 100 | 50 |

Set CLI tier: `SOURCEIQ_TIER=starter` in `.env`.

---

## How ScrapingBee credits work

- **Stage 1** uses direct HTTP scrapers and Google Trends — **0 ScrapingBee credits**.
- **Stage 2** uses ScrapingBee for JS-rendered retail and wholesale pages — **~175 credits per drill-down** (configurable via `STAGE2_CREDITS_PER_DRILLDOWN` in `config.py`).
- Each ScrapingBee request logs a line to `output/credit_log.txt`:

  ```
  timestamp    source    url    credits    session_total
  ```

- Pro users can add their own `SCRAPINGBEE_API_KEY` in Account settings to unlock 50 Stage 2 runs/month instead of 25.
- The admin dashboard shows **credits used today** by summing today's rows in `credit_log.txt`.

---

## Project layout

```
Market Spy/
├── run.py                  # CLI entry point
├── market_spy/
│   ├── cli.py              # Stage 1 / 2 orchestration
│   ├── analysis.py         # Scoring and margin matching
│   ├── config.py           # Tiers, paths, API keys
│   └── web/
│       ├── app.py          # FastAPI application
│       ├── database.py     # SQLite users, history, watchlist
│       └── templates/      # HTML UI
├── output/
│   ├── credit_log.txt      # ScrapingBee usage
│   ├── error_log.txt       # Web app errors
│   └── exports/            # Pro CSV exports
├── requirements.txt        # CLI dependencies
├── requirements_web.txt    # Web dependencies
├── Procfile                # Railway process
└── railway.toml            # Railway deploy config
```

---

## Logs and admin

| File | Contents |
|------|----------|
| `output/credit_log.txt` | ScrapingBee credits per request |
| `output/error_log.txt` | Web errors with stack traces |
| `output/request_log.txt` | HTTP method, path, status code |

`GET /admin` (HTTP Basic Auth, password = `ADMIN_PASSWORD`) shows user counts, today's searches, credits used, recent errors, and recent signups.

---

## Contact

Questions, support, or partnership inquiries: **support@sourceiq.app** (placeholder — update before launch).

---

## License

Proprietary. All rights reserved.
