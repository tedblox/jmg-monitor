import os
import json
import resend
import requests
from datetime import datetime, timezone

TICKER = "JMG"
CIK = "0002049290"
STATE_FILE = "state.json"

EDGAR_CIK_URL = f"https://data.sec.gov/submissions/CIK{CIK}.json"
NYSE_HALTS_URL = (
    "https://www.nyse.com/api/quotes/tradingHalts"
    "?pageNumber=1&sortColumn=haltDate&sortOrder=DESC"
)
YAHOO_QUOTE_URL = f"https://query1.finance.yahoo.com/v8/finance/chart/{TICKER}?interval=1d&range=1d"

HEADERS = {"User-Agent": "JMG-Monitor/1.0 contact@example.com"}

# Set TEST_MODE=true in your workflow env to force a summary email every run
TEST_MODE = os.environ.get("TEST_MODE", "false").lower() == "true"


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "last_accession": None,
        "halt_active": True,
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
    from_addr = os.environ.get("ALERT_EMAIL_FROM", "onboarding@resend.dev")
    to_addr = os.environ["ALERT_EMAIL_TO"]

    print(f"[Resend] Sending to: {to_addr}")
    print(f"[Resend] From: {from_addr}")
    print(f"[Resend] Subject: {subject}")

    params = {
        "from": from_addr,
        "to": [to_addr],
        "subject": subject,
        "html": html_body,
    }
    response = resend.Emails.send(params)
    print(f"[Resend] Response: {response}")


# ---------------------------------------------------------------------------
# Check 1: SEC EDGAR direct CIK feed
# ---------------------------------------------------------------------------

def check_sec_filings(state: dict):
    print(f"\n[EDGAR] Fetching: {EDGAR_CIK_URL}")
    try:
        resp = requests.get(EDGAR_CIK_URL, headers=HEADERS, timeout=15)
        print(f"[EDGAR] HTTP status: {resp.status_code}")
        resp.raise_for_status()
        data = resp.json()

        filings = data.get("filings", {}).get("recent", {})
        accession_numbers = filings.get("accessionNumber", [])
        form_types = filings.get("form", [])
        filing_dates = filings.get("filingDate", [])

        print(f"[EDGAR] Total filings found: {len(accession_numbers)}")

        if not accession_numbers:
            print("[EDGAR] No filings returned.")
            return False, None

        latest_accession = accession_numbers[0]
        latest_form = form_types[0] if form_types else "Unknown"
        latest_date = filing_dates[0] if filing_dates else "Unknown"

        print(f"[EDGAR] Latest filing: {latest_accession} | Form: {latest_form} | Date: {latest_date}")
        print(f"[EDGAR] Last seen accession in state: {state.get('last_accession')}")

        latest = {
            "accession": latest_accession,
            "form": latest_form,
            "date": latest_date,
            "company": data.get("name", "JM Group Limited"),
        }

        if latest_accession != state.get("last_accession"):
            print("[EDGAR] ✅ New filing detected!")
            state["last_accession"] = latest_accession
            return True, latest   # is_new=True
        else:
            print("[EDGAR] No new filings since last check.")
            return False, latest  # is_new=False, still return data for test email

    except Exception as e:
        print(f"[EDGAR] ❌ Error: {e}")

    return False, None


# ---------------------------------------------------------------------------
# Check 2: NYSE trading halts feed
# ---------------------------------------------------------------------------

def check_nyse_halts(state: dict):
    print(f"\n[NYSE] Fetching: {NYSE_HALTS_URL}")
    try:
        resp = requests.get(NYSE_HALTS_URL, headers=HEADERS, timeout=15)
        print(f"[NYSE] HTTP status: {resp.status_code}")
        resp.raise_for_status()

        halts = resp.json()
        print(f"[NYSE] Total halted stocks returned: {len(halts)}")

        # Print first 3 entries so we can see the data shape
        for i, h in enumerate(halts[:3]):
            print(f"[NYSE] Sample halt[{i}]: {h}")

        jmg_halt = None
        for halt in halts:
            symbol = halt.get("symbolTicker", "").upper()
            if symbol == TICKER:
                jmg_halt = halt
                print(f"[NYSE] ✅ JMG found in halts list: {halt}")
                break

        currently_halted = jmg_halt is not None
        was_halted = state.get("halt_active", True)

        print(f"[NYSE] JMG currently halted: {currently_halted} | Was halted: {was_halted}")

        if was_halted and not currently_halted:
            state["halt_active"] = False
            return True, False, None
        if not was_halted and currently_halted:
            state["halt_active"] = True
            return True, True, jmg_halt

    except Exception as e:
        print(f"[NYSE] ❌ Error: {e}")

    return False, state.get("halt_active", True), None


# ---------------------------------------------------------------------------
# Yahoo Finance price
# ---------------------------------------------------------------------------

def get_yahoo_price() -> dict:
    try:
        resp = requests.get(YAHOO_QUOTE_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        result = resp.json().get("chart", {}).get("result")
        if result:
            return result[0].get("meta", {})
    except Exception as e:
        print(f"[Yahoo] Error: {e}")
    return {}


# ---------------------------------------------------------------------------
# Email bodies
# ---------------------------------------------------------------------------

def filing_email(filing: dict, now: str) -> tuple:
    edgar_url = (
        f"https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcompany&CIK={CIK}&type={filing['form']}&dateb=&owner=include&count=5"
    )
    subject = f"🚨 JMG New SEC Filing: {filing['form']} ({filing['date']})"
    body = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;">
      <h2 style="color:#d97706;">📄 New SEC Filing Detected — JMG</h2>
      <table style="border-collapse:collapse;width:100%;font-size:15px;">
        <tr><td style="padding:10px;font-weight:bold;width:150px;background:#f9fafb;">Company</td>
            <td style="padding:10px;">{filing['company']}</td></tr>
        <tr><td style="padding:10px;font-weight:bold;background:#f0fdf4;">Form Type</td>
            <td style="padding:10px;background:#f0fdf4;">{filing['form']}</td></tr>
        <tr><td style="padding:10px;font-weight:bold;background:#f9fafb;">Filed Date</td>
            <td style="padding:10px;background:#f9fafb;">{filing['date']}</td></tr>
        <tr><td style="padding:10px;font-weight:bold;background:#f0fdf4;">Accession #</td>
            <td style="padding:10px;background:#f0fdf4;font-size:13px;">{filing['accession']}</td></tr>
        <tr><td style="padding:10px;font-weight:bold;background:#f9fafb;">Detected At</td>
            <td style="padding:10px;background:#f9fafb;">{now}</td></tr>
      </table>
      <p style="margin-top:24px;">
        <a href="{edgar_url}" style="background:#2563eb;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;margin-right:12px;font-weight:bold;">View on SEC EDGAR →</a>
        <a href="https://finance.yahoo.com/quote/{TICKER}" style="background:#16a34a;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;font-weight:bold;">Yahoo Finance →</a>
      </p>
    </div>
    """
    return subject, body


def halt_lifted_email(yahoo_meta: dict, now: str) -> tuple:
    price = yahoo_meta.get("regularMarketPrice", "N/A")
    subject = f"🟢 JMG TRADING HALT LIFTED — {now}"
    body = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;">
      <div style="background:#dcfce7;border-left:4px solid #16a34a;padding:16px;border-radius:6px;margin-bottom:24px;">
        <h2 style="color:#16a34a;margin:0;">🟢 JMG Trading Halt Lifted!</h2>
        <p style="margin:8px 0 0;color:#166534;">JMG no longer appears on the NYSE trading halts list.</p>
      </div>
      <table style="border-collapse:collapse;width:100%;font-size:15px;">
        <tr><td style="padding:10px;font-weight:bold;width:160px;background:#f9fafb;">Ticker</td>
            <td style="padding:10px;background:#f9fafb;">JMG (NYSE American)</td></tr>
        <tr><td style="padding:10px;font-weight:bold;background:#f0fdf4;">Last Known Price</td>
            <td style="padding:10px;background:#f0fdf4;">${price}</td></tr>
        <tr><td style="padding:10px;font-weight:bold;background:#f9fafb;">Detected At</td>
            <td style="padding:10px;background:#f9fafb;">{now}</td></tr>
        <tr><td style="padding:10px;font-weight:bold;background:#f0fdf4;">Source</td>
            <td style="padding:10px;background:#f0fdf4;">NYSE Trading Halts API</td></tr>
      </table>
      <div style="background:#fef9c3;border-left:4px solid #ca8a04;padding:14px;border-radius:6px;margin-top:20px;font-size:14px;">
        ⚠️ <b>Please verify independently before taking any action.</b>
      </div>
      <p style="margin-top:24px;">
        <a href="https://www.nyse.com/trade-halt-resumptions" style="background:#16a34a;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;margin-right:12px;font-weight:bold;">NYSE Halt List →</a>
        <a href="https://finance.yahoo.com/quote/{TICKER}" style="background:#2563eb;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;font-weight:bold;">Yahoo Finance →</a>
      </p>
    </div>
    """
    return subject, body


def test_summary_email(edgar_state: dict, nyse_halted: bool, yahoo_meta: dict, now: str) -> tuple:
    """Diagnostic email sent on every manual run when TEST_MODE=true."""
    price = yahoo_meta.get("regularMarketPrice", "N/A")
    market_state = yahoo_meta.get("marketState", "N/A")
    subject = f"🔍 JMG Monitor — Test Run {now}"
    body = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;">
      <h2 style="color:#2563eb;">🔍 JMG Monitor Test Summary</h2>
      <p style="color:#6b7280;">This email is sent on every manual run when TEST_MODE=true. Disable it by removing TEST_MODE from the workflow.</p>

      <h3 style="margin-top:24px;">📄 SEC EDGAR</h3>
      <table style="border-collapse:collapse;width:100%;font-size:14px;">
        <tr><td style="padding:8px;font-weight:bold;background:#f9fafb;width:180px;">Latest Accession</td>
            <td style="padding:8px;background:#f9fafb;">{edgar_state.get('accession', 'N/A')}</td></tr>
        <tr><td style="padding:8px;font-weight:bold;background:#f0fdf4;">Form</td>
            <td style="padding:8px;background:#f0fdf4;">{edgar_state.get('form', 'N/A')}</td></tr>
        <tr><td style="padding:8px;font-weight:bold;background:#f9fafb;">Date</td>
            <td style="padding:8px;background:#f9fafb;">{edgar_state.get('date', 'N/A')}</td></tr>
      </table>

      <h3 style="margin-top:24px;">🏛️ NYSE Halts</h3>
      <table style="border-collapse:collapse;width:100%;font-size:14px;">
        <tr><td style="padding:8px;font-weight:bold;background:#f9fafb;width:180px;">JMG In Halts List</td>
            <td style="padding:8px;background:#f9fafb;">{"🔴 YES — still halted" if nyse_halted else "🟢 NO — halt lifted!"}</td></tr>
      </table>

      <h3 style="margin-top:24px;">📈 Yahoo Finance</h3>
      <table style="border-collapse:collapse;width:100%;font-size:14px;">
        <tr><td style="padding:8px;font-weight:bold;background:#f9fafb;width:180px;">Last Price</td>
            <td style="padding:8px;background:#f9fafb;">${price}</td></tr>
        <tr><td style="padding:8px;font-weight:bold;background:#f0fdf4;">Market State</td>
            <td style="padding:8px;background:#f0fdf4;">{market_state}</td></tr>
      </table>

      <p style="margin-top:24px;font-size:13px;color:#6b7280;">Checked at: {now}</p>
    </div>
    """
    return subject, body


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"\n{'='*50}")
    print(f"JMG Monitor starting — TEST_MODE: {TEST_MODE}")
    print(f"{'='*50}\n")

    state = load_state()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    state["last_check"] = now
    alerts_sent = 0

    # --- Check 1: New SEC filing ---
    new_filing, filing_data = check_sec_filings(state)
    if new_filing and filing_data:
        subject, body = filing_email(filing_data, now)
        send_email(subject, body)
        alerts_sent += 1

    # --- Check 2: NYSE halts ---
    halt_changed, now_halted, halt_detail = check_nyse_halts(state)
    if halt_changed and not now_halted:
        yahoo_meta = get_yahoo_price()
        subject, body = halt_lifted_email(yahoo_meta, now)
        send_email(subject, body)
        alerts_sent += 1

    # --- Test mode: always send a summary email ---
    if TEST_MODE:
        yahoo_meta = get_yahoo_price()
        # filing_data is always returned now (new or existing), so show real values
        edgar_info = filing_data if filing_data else {
            "accession": "Not found",
            "form": "N/A",
            "date": "N/A",
        }
        subject, body = test_summary_email(edgar_info, now_halted, yahoo_meta, now)
        send_email(subject, body)
        alerts_sent += 1

    print(f"\n[Done] {alerts_sent} email(s) sent.")
    save_state(state)


if __name__ == "__main__":
    main()
