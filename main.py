import os
import time
import requests
import schedule
from datetime import datetime

# ── Config ─────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "7820830319:AAEorAawsME1kl_OjfayWsvJseuEjZ1n0tM")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "-5066300574")
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY",  "")
BOT_NAME           = "Eve"

signal_counter = 0

# ── OKX helpers ────────────────────────────────────────────────────────────
def get_okx_data(inst_id="BTC-USDT-SWAP"):
    try:
        base = "https://www.okx.com"
        ticker = requests.get(f"{base}/api/v5/market/ticker?instId={inst_id}", timeout=10).json()["data"][0]
        price  = float(ticker["last"])

        funding = requests.get(f"{base}/api/v5/public/funding-rate?instId={inst_id}", timeout=10).json()["data"][0]
        fr      = float(funding["fundingRate"])

        oi_r = requests.get(f"{base}/api/v5/rubik/stat/contracts/open-interest-volume?ccy=BTC&period=5m&limit=20", timeout=10).json()
        oi   = float(oi_r["data"][0][2]) if oi_r.get("data") else 0

        trades = requests.get(f"{base}/api/v5/market/trades?instId={inst_id}&limit=200", timeout=10).json()["data"]
        cvd = sum(float(t["sz"]) * (1 if t["side"] == "buy" else -1) for t in trades)

        book = requests.get(f"{base}/api/v5/market/books?instId={inst_id}&sz=20", timeout=10).json()["data"][0]
        bid_vol = sum(float(b[1]) for b in book["bids"][:10])
        ask_vol = sum(float(a[1]) for a in book["asks"][:10])
        ob_delta = bid_vol - ask_vol

        return {"price": price, "fr": fr, "oi": oi, "cvd": cvd, "ob_delta": ob_delta}
    except Exception as e:
        print(f"OKX error: {e}")
        return None


def ask_claude(prompt):
    if not ANTHROPIC_API_KEY:
        return ""
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=20,
        ).json()
        return resp["content"][0]["text"].strip()
    except Exception as e:
        print(f"Claude error: {e}")
        return ""


def analyze():
    global signal_counter

    data = get_okx_data()
    if not data:
        return

    fr   = data["fr"]
    cvd  = data["cvd"]
    oi   = data["oi"]
    ob   = data["ob_delta"]

    # ── Crowding state ──────────────────────────────────────────────────────
    if fr > 0.0003:
        crowding = "Extreme Long Crowded"
    elif fr < -0.0003:
        crowding = "Extreme Short Crowded"
    elif fr > 0.0001:
        crowding = "Longs Paying"
    elif fr < -0.0001:
        crowding = "Shorts Paying"
    else:
        crowding = "Neutral"

    # ── CVD slope (simple diff proxy) ──────────────────────────────────────
    c_slope = cvd / max(abs(cvd), 1)   # sign direction placeholder

    # ── Divergence ─────────────────────────────────────────────────────────
    # price proxy via ob_delta sign vs cvd sign
    divergence = "NONE"
    if ob < 0 and cvd > 0:
        divergence = "BULLISH"
    elif ob > 0 and cvd < 0:
        divergence = "BEARISH"

    # ── Score ───────────────────────────────────────────────────────────────
    score = 0
    if cvd > 50:       score += 2
    elif cvd > 0:      score += 1
    if cvd < -50:      score -= 2
    elif cvd < 0:      score -= 1
    if ob > 0:         score += 1
    if ob < 0:         score -= 1
    if fr < -0.0001:   score += 1   # shorts paying => long-friendly
    if fr >  0.0001:   score -= 1   # longs paying  => short-friendly
    if divergence == "BULLISH":  score += 1
    if divergence == "BEARISH":  score -= 1

    if score >= 3:
        signal = "LONG"
    elif score <= -3:
        signal = "SHORT"
    else:
        signal = "OBSERVE"

    # ── Adaptive frequency ─────────────────────────────────────────────────
    freq = 480   # default 8 h
    if abs(cvd) > 200:
        freq = 30
    if abs(cvd) > 500:
        freq = 15

    # ── AI commentary ──────────────────────────────────────────────────────
    ai_comment = ""
    if signal != "OBSERVE":
        ai_comment = ask_claude(
            f"You are {BOT_NAME}. BTC: ${data['price']:,.2f}, "
            f"Funding {fr:.6f}, CVD {cvd:.2f} (slope {c_slope:.3f}), "
            f"OI {oi:,.0f}, OB Delta {ob:.2f}. "
            f"Crowding: {crowding}. Signal: {signal}. "
            f"2-3 sentences: spot vs leverage? crowding risk? key level? "
            f"Chinese first then English."
        )

    signal_counter += 1
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')

    if signal == "LONG":
        sig_emoji = "\U0001F7E2"
        direction = "LONG"
        entry  = data['price']
        sl     = round(entry * 0.985, 1)
        tp1    = round(entry * 1.008, 1)
        tp2    = round(entry * 1.020, 1)
        sl_pct = "-1.50%"
        tp1_pct = "+0.80%"
        tp2_pct = "+2.00%"
        rr     = round((entry * 1.020 - entry) / (entry - entry * 0.985), 2)
    elif signal == "SHORT":
        sig_emoji = "\U0001F534"
        direction = "SHORT"
        entry  = data['price']
        sl     = round(entry * 1.015, 1)
        tp1    = round(entry * 0.992, 1)
        tp2    = round(entry * 0.980, 1)
        sl_pct = "+1.50%"
        tp1_pct = "-0.80%"
        tp2_pct = "-2.00%"
        rr     = round((entry - entry * 0.980) / (entry * 1.015 - entry), 2)
    else:
        sig_emoji = "\U0001F7E1"
        direction = "OBSERVE"

    msg = f"<b>{sig_emoji} {BOT_NAME} #{signal_counter:03d} BTC {direction}</b>\n{now}\n\n"
    msg += f"<b>Price: ${data['price']:,.2f}</b>\n"
    msg += f"Funding: {fr:.6f} ({crowding})\n"
    msg += f"CVD: {cvd:.4f} (slope {c_slope:.3f})\n"
    msg += f"OI: {oi:,.0f} BTC\n"
    msg += f"OB Delta: {ob:.2f}\n"
    msg += f"Divergence: {divergence}\n"
    msg += f"Score: {score:+d}\n\n"

    if signal in ("LONG", "SHORT"):
        msg += f"入场: ${entry:,.2f}\n"
        msg += f"止损: ${sl:,.2f} ({sl_pct})\n"
        msg += f"TP1: ${tp1:,.2f} ({tp1_pct}) — 触发后平50%，SL移至成本\n"
        msg += f"TP2: ${tp2:,.2f} ({tp2_pct})\n"
        msg += f"R:R = {rr}:1\n"
    else:
        msg += "Observing, waiting for consistency.\n"

    if ai_comment:
        msg += f"\nAI: {ai_comment}"
    if freq < 480:
        msg += f"\n\nAnomaly -> freq {freq}min"
    msg += f"\n\nPowered by {BOT_NAME}"

    send_telegram(msg)
    schedule.clear('main-task')
    schedule.every(freq).minutes.do(analyze).tag('main-task')


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=15)
        if resp.status_code != 200:
            print(f"TG error {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"TG error: {e}")


schedule.every(8).hours.do(analyze).tag('main-task')

if __name__ == "__main__":
    startup = f"Eve BTC started | Monitoring BTC/USDT | Crowding+Divergence+Slope+AI"
    print(startup)
    send_telegram(startup)
    analyze()
    while True:
        schedule.run_pending()
        time.sleep(1)
