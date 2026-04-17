# JMG Halt Monitor

Monitors **JM Group Limited (NYSE: JMG)** for trading halt updates and new SEC filings.
Sends email alerts via [Resend](https://resend.com) using GitHub Actions — no external services required.

---

## What it alerts on

| Trigger | Alert |
|---|---|
| New SEC filing (6-K or 8-K) detected on EDGAR | 📄 Filing alert with form type and date |
| Yahoo Finance shows JMG price in an active market state | 🟢 Halt lifted alert with price |

Runs every **30 minutes** automatically. Can also be triggered manually.

---

## Setup

### 1. Create a private GitHub repo and push these files

```
jmg-monitor/
├── .github/
│   └── workflows/
│       └── jmg_monitor.yml
├── monitor.py
├── state.json
└── README.md
```

### 2. Add GitHub Secrets

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value |
|---|---|
| `RESEND_API_KEY` | Your Resend API key (starts with `re_...`) |
| `ALERT_EMAIL_TO` | Email address to receive alerts |
| `ALERT_EMAIL_FROM` | Sender address — must match your verified Resend domain e.g. `JMG Monitor <alerts@yourdomain.com>`. Use `onboarding@resend.dev` for testing only. |

### 3. Get your Resend API key

1. Sign up at [resend.com](https://resend.com)
2. Go to **API Keys → Create API Key**
3. Copy the key into the `RESEND_API_KEY` secret

### 4. (Optional) Verify a sending domain in Resend

- Go to Resend → **Domains → Add Domain**
- Add DNS records as instructed
- Update `ALERT_EMAIL_FROM` to use that domain

Without a verified domain, Resend restricts sending to your own account email only.

### 5. Test it manually

1. Go to your repo → **Actions tab**
2. Click **JMG Halt Monitor**
3. Click **Run workflow → Run workflow**
4. Check the logs — you should see `No changes detected for JMG.`

---

## How state works

After each run, `state.json` is auto-committed back to the repo.
It tracks the last seen SEC filing ID and whether a halt is currently active,
so you won't receive duplicate alerts.

---

## Files

| File | Purpose |
|---|---|
| `.github/workflows/jmg_monitor.yml` | GitHub Actions schedule and steps |
| `monitor.py` | Checks SEC EDGAR + Yahoo Finance, sends alerts |
| `state.json` | Persists last known state between runs |
