import os
import json
import resend
import requests
from datetime import datetime, timezone

TICKER = "JMG"
CIK = "0002049290"          # JM Group Limited's SEC CIK number
STATE_FILE = "state.json"

# Primary: JMG's direct EDGAR submission feed (updates within minutes)
EDGAR_CIK_URL = f"https://data.sec.gov/submissions/CIK{CIK}.json"

# Primary: NYSE trading halts feed (live list of currently halted stocks)
NYSE_HALTS_URL = (
    "https://www.nyse.com/api/quotes/tradingHalts"
    "?pageNumber=1&sortColumn=haltDate&sortOrder=DESC"
)

# Fallback: Yahoo Finance for price confirmation after halt lift
YAHOO_QUOTE_URL = f"https://query1.finance.yahoo.com/v8/finance/chart/{TICKER}?interval=1d&range=1d"

HEADERS = {"User-Agent": "JMG-Monitor/1.0 contact@example.com"}


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "last_accession": None,   # Last seen EDGAR accession number
        "halt_active": True,      # Whether we believe halt is currently active
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

    params = {
        "from": from_addr,
        "to": [to_addr],
        "subject": subject,
        "html": html_body,
    }
    response = resend.Emails.send(params)
    print(f"[Resend] Email sent — id: {response.get('id', 'n/a')}")


# ---------------------------------------------------------------------------
# Check 1: SEC EDGAR direct CIK feed
# ---------------------------------------------------------------------------

def check_sec_filings(state: dict):
    """
    Hits JMG's direct EDGAR submissions JSON — updates within minutes of filing.
    Returns (new_filing_found, filing_dict_or_None).
    """
    try:
        resp = requests.get(EDGAR_CIK_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        filings = data.get("filings", {}).get("recent", {})
        accession_numbers = filings.get("accessionNumber", [])
        form_types = filings.get("form", [])
        filing_dates = filings.get("filingDate", [])
        descriptions = filings.get("primaryDocument", [])

        if not accession_numbers:
            print("[EDGAR] No filings found.")
            return False, None

        # Most recent filing is index 0
        latest_accession = accession_numbers[0]
        latest_form = form_types[0] if form_types else "Unknown"
        latest_date = filing_dates[0] if filing_dates else "Unknown"
        latest_doc = descriptions[0] if descriptions else ""

        print(f"[EDGAR] Latest accession: {latest_accession} ({latest_form} on {latest_date})")

        if latest_accession != state.get("last_accession"):
            state["last_accession"] = latest_accession
            return True, {
                "accession": latest_accession,
                "form": latest_form,
                "date": latest_date,
                "document": latest_doc,
                "company": data.get("name", "JM Group Limited"),
            }

    except Exception as e:
        print(f"[EDGAR] Check error: {e}")

    return False, None


# ---------------------------------------------------------------------------
# Check 2: NYSE trading halts feed (primary halt detector)
# ---------------------------------------------------------------------------

def check_nyse_halts(state: dict):
    """
    Queries the live NYSE halts feed. If JMG is NOT in the list,
    the halt has been lifted. If it IS in the list, halt is still active.
    Returns (halt_status_changed, is_now_halted, halt_detail_or_None).
    """
    try:
        resp = requests.get(NYSE_HALTS_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        halts = resp.json()

        # Search for JMG in current halts list
        jmg_halt = None
        for halt in halts:
            symbol = halt.get("symbolTicker", "").upper()
            if symbol == TICKER:
                jmg_halt = halt
                break

        currently_halted = jmg_halt is not None
        was_halted = state.get("halt_active", True)

        print(f"[NYSE] JMG currently halted: {currently_halted} | Previously halted: {was_halted}")

        if was_halted and not currently_halted:
            # Halt has been LIFTED
            state["halt_active"] = False
            return True, False, None  # changed=True, now_halted=False

        if not was_halted and currently_halted:
            # Stock has been RE-halted (edge case)
            state["halt_active"] = True
            return True, True, jmg_halt  # changed=True, now_halted=True

        # No change
        return False, currently_halted, jmg_halt

    except Exception as e:
        print(f"[NYSE] Halts check error: {e}")

    return False, state.get("halt_active", True), None


# ---------------------------------------------------------------------------
# Check 3: Yahoo Finance price (confirmation only, used after halt lift)
# ---------------------------------------------------------------------------

def get_yahoo_price() -> dict:
    """Fetch current market data from Yahoo as supplementary info."""
    try:
        resp = requests.get(
            YAHOO_QUOTE_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15
        )
        resp.raise_for_status()
        result = resp.json().get("chart", {}).get("result")
        if result:
            return result[0].get("meta", {})
    except Exception as e:
        print(f"[Yahoo] Price check error: {e}")
    return {}


# ---------------------------------------------------------------------------
# Email bodies
# ---------------------------------------------------------------------------

def filing_email(filing: dict, now: str) -> tuple:
    accession_clean = filing["accession"].replace("-", "")
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
        <a href="{edgar_url}"
           style="background:#2563eb;color:#fff;padding:10px 20px;border-radius:6px;
                  text-decoration:none;margin-right:12px;font-weight:bold;">
          View on SEC EDGAR →
        </a>
        <a href="https://finance.yahoo.com/quote/{TICKER}"
           style="background:#16a34a;color:#fff;padding:10px 20px;border-radius:6px;
                  text-decoration:none;font-weight:bold;">
          Yahoo Finance →
        </a>
      </p>
      <p style="font-size:12px;color:#6b7280;margin-top:24px;">
        This alert was triggered by the JMG Monitor running on GitHub Actions.
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
        <p style="margin:8px 0 0;color:#166534;">
          JMG no longer appears on the NYSE trading halts list.
        </p>
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
      <div style="background:#fef9c3;border-left:4px solid #ca8a04;padding:14px;
                  border-radius:6px;margin-top:20px;font-size:14px;">
        ⚠️ <b>Please verify independently before taking any action.</b>
        Confirm on NYSE or your broker before trading.
      </div>
      <p style="margin-top:24px;">
        <a href="https://www.nyse.com/trade-halt-resumptions"
           style="background:#16a34a;color:#fff;padding:10px 20px;border-radius:6px;
                  text-decoration:none;margin-right:12px;font-weight:bold;">
          NYSE Halt List →
        </a>
        <a href="https://finance.yahoo.com/quote/{TICKER}"
           style="background:#2563eb;color:#fff;padding:10px 20px;border-radius:6px;
                  text-decoration:none;font-weight:bold;">
          Yahoo Finance →
        </a>
      </p>
      <p style="font-size:12px;color:#6b7280;margin-top:24px;">
        This alert was triggered by the JMG Monitor running on GitHub Actions.
      </p>
    </div>
    """
    return subject, body


def halt_resumed_email(halt_detail: dict, now: str) -> tuple:
    """Edge case — stock gets re-halted after being active."""
    subject = f"🔴 JMG RE-HALTED on NYSE — {now}"
    reason = halt_detail.get("haltReasonCode", "Unknown") if halt_detail else "Unknown"
    body = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;">
      <div style="background:#fee2e2;border-left:4px solid #dc2626;padding:16px;border-radius:6px;">
        <h2 style="color:#dc2626;margin:0;">🔴 JMG Has Been Re-Halted</h2>
        <p style="margin:8px 0 0;color:#991b1b;">JMG has reappeared on the NYSE trading halts list.</p>
      </div>
      <table style="border-collapse:collapse;width:100%;font-size:15px;margin-top:20px;">
        <tr><td style="padding:10px;font-weight:bold;background:#f9fafb;">Halt Reason</td>
            <td style="padding:10px;background:#f9fafb;">{reason}</td></tr>
        <tr><td style="padding:10px;font-weight:bold;background:#f0fdf4;">Detected At</td>
            <td style="padding:10px;background:#f0fdf4;">{now}</td></tr>
      </table>
      <p style="margin-top:24px;">
        <a href="https://www.nyse.com/trade-halt-resumptions"
           style="background:#dc2626;color:#fff;padding:10px 20px;border-radius:6px;
                  text-decoration:none;font-weight:bold;">
          NYSE Halt List →
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

    # --- Check 1: New SEC filing via direct CIK feed ---
    new_filing, filing_data = check_sec_filings(state)
    if new_filing and filing_data:
        subject, body = filing_email(filing_data, now)
        send_email(subject, body)
        alerts_sent += 1

    # --- Check 2: NYSE halts feed (most reliable halt detector) ---
    halt_changed, now_halted, halt_detail = check_nyse_halts(state)
    if halt_changed:
        if not now_halted:
            # Halt lifted — get Yahoo price as supplementary info
            yahoo_meta = get_yahoo_price()
            subject, body = halt_lifted_email(yahoo_meta, now)
            send_email(subject, body)
            alerts_sent += 1
        else:
            # Re-halted (edge case)
            subject, body = halt_resumed_email(halt_detail, now)
            send_email(subject, body)
            alerts_sent += 1

    if alerts_sent == 0:
        print(f"[{now}] No changes detected for {TICKER}. Halt active: {state.get('halt_active', True)}")
    else:
        print(f"[{now}] {alerts_sent} alert(s) sent.")

    save_state(state)


if __name__ == "__main__":
    main()
