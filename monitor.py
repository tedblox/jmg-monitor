import os
import json
import resend
import requests
from datetime import datetime, timezone

TICKER = "JMG"
STATE_FILE = "state.json"

SEC_EDGAR_URL = (
    "https://efts.sec.gov/LATEST/search-index"
    "?q=%22JMG%22+%22JM+Group%22"
    "&forms=6-K,8-K"
    "&dateRange=custom&startdt=2026-01-01"
)
YAHOO_QUOTE_URL = (
    f"https://query1.finance.yahoo.com/v8/finance/chart/{TICKER}"
    "?interval=1d&range=1d"
)


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "last_sec_filing_id": None,
        "halt_detected": True,
        "last_check": None,
    }


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Email via Resend
# ---------------------------------------------------------------------------

def send_email(subject: str, html_body: str):
    resend.api_key = os.environ["RESEND_API_KEY"]
    from_addr = os.environ.get("ALERT_EMAIL_FROM", "JMG Monitor <onboarding@resend.dev>")
    to_addr = os.environ["ALERT_EMAIL_TO"]

    params = {
        "from": from_addr,
        "to": [to_addr],
        "subject": subject,
        "html": html_body,
    }
    response = resend.Emails.send(params)
    print(f"[Resend] Email sent — id: {response.get('id', 'n/a')}")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_sec_filings(state: dict):
    """Return (new_filing_found, filing_data_or_None)."""
    headers = {"User-Agent": "JMG-Monitor/1.0 contact@example.com"}
    try:
        resp = requests.get(SEC_EDGAR_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])
        if not hits:
            return False, None

        latest = hits[0]["_source"]
        filing_id = latest.get("file_date", "") + "_" + latest.get("form_type", "")

        if filing_id != state.get("last_sec_filing_id"):
            state["last_sec_filing_id"] = filing_id
            return True, latest

    except Exception as e:
        print(f"[SEC] Check error: {e}")

    return False, None


def check_trading_status(state: dict):
    """Return (halt_lifted, market_meta_or_None)."""
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(YAHOO_QUOTE_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        result = resp.json().get("chart", {}).get("result")

        if result:
            meta = result[0].get("meta", {})
            market_state = meta.get("marketState", "")
            regular_price = meta.get("regularMarketPrice")

            # Active price + normal market state = halt likely lifted
            if regular_price and market_state in ("REGULAR", "PRE", "POST"):
                if state.get("halt_detected", True):
                    state["halt_detected"] = False
                    return True, meta

    except Exception as e:
        print(f"[Yahoo] Check error: {e}")

    return False, None


# ---------------------------------------------------------------------------
# Email bodies
# ---------------------------------------------------------------------------

def filing_email(filing_data: dict, now: str) -> tuple[str, str]:
    form = filing_data.get("form_type", "Unknown")
    date = filing_data.get("file_date", "N/A")
    company = filing_data.get("display_names", "JM Group Limited")

    subject = f"🚨 JMG New SEC Filing: {form} ({date})"
    body = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;">
      <h2 style="color:#d97706;">📄 New SEC Filing — JMG</h2>
      <table style="border-collapse:collapse;width:100%;">
        <tr><td style="padding:8px;font-weight:bold;width:140px;">Form</td><td style="padding:8px;">{form}</td></tr>
        <tr style="background:#f9fafb;"><td style="padding:8px;font-weight:bold;">Filed</td><td style="padding:8px;">{date}</td></tr>
        <tr><td style="padding:8px;font-weight:bold;">Company</td><td style="padding:8px;">{company}</td></tr>
        <tr style="background:#f9fafb;"><td style="padding:8px;font-weight:bold;">Detected at</td><td style="padding:8px;">{now}</td></tr>
      </table>
      <p style="margin-top:24px;">
        <a href="https://efts.sec.gov/LATEST/search-index?q=%22JMG%22&forms=6-K,8-K"
           style="background:#2563eb;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;margin-right:12px;">
          View on SEC EDGAR
        </a>
        <a href="https://finance.yahoo.com/quote/JMG"
           style="background:#16a34a;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;">
          Yahoo Finance
        </a>
      </p>
    </div>
    """
    return subject, body


def halt_lifted_email(meta: dict, now: str) -> tuple[str, str]:
    price = meta.get("regularMarketPrice", "N/A")
    mkt_state = meta.get("marketState", "N/A")

    subject = f"🟢 JMG TRADING HALT LIFTED — Market State: {mkt_state}"
    body = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;">
      <h2 style="color:#16a34a;">🟢 JMG Trading Halt Appears Lifted!</h2>
      <table style="border-collapse:collapse;width:100%;">
        <tr><td style="padding:8px;font-weight:bold;width:160px;">Ticker</td><td style="padding:8px;">JMG (NYSE American)</td></tr>
        <tr style="background:#f9fafb;"><td style="padding:8px;font-weight:bold;">Current Price</td><td style="padding:8px;">${price}</td></tr>
        <tr><td style="padding:8px;font-weight:bold;">Market State</td><td style="padding:8px;">{mkt_state}</td></tr>
        <tr style="background:#f9fafb;"><td style="padding:8px;font-weight:bold;">Detected at</td><td style="padding:8px;">{now}</td></tr>
      </table>
      <p style="margin-top:16px;padding:12px;background:#fef9c3;border-radius:6px;font-size:14px;">
        ⚠️ <b>Please verify independently before taking any action.</b>
        This alert is automated and may occasionally produce false positives.
      </p>
      <p style="margin-top:16px;">
        <a href="https://finance.yahoo.com/quote/JMG"
           style="background:#16a34a;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;margin-right:12px;">
          Yahoo Finance
        </a>
        <a href="https://www.nyse.com/quote/XASE:JMG"
           style="background:#2563eb;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;">
          NYSE Quote
        </a>
      </p>
    </div>
    """
    return subject, body


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    state = load_state()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    state["last_check"] = now

    alerts_sent = 0

    # 1. Check for new SEC filings
    new_filing, filing_data = check_sec_filings(state)
    if new_filing and filing_data:
        subject, body = filing_email(filing_data, now)
        send_email(subject, body)
        alerts_sent += 1

    # 2. Check if halt is lifted
    halt_lifted, market_meta = check_trading_status(state)
    if halt_lifted and market_meta:
        subject, body = halt_lifted_email(market_meta, now)
        send_email(subject, body)
        alerts_sent += 1

    if alerts_sent == 0:
        print(f"[{now}] No changes detected for {TICKER}.")
    else:
        print(f"[{now}] {alerts_sent} alert(s) sent.")

    save_state(state)


if __name__ == "__main__":
    main()
