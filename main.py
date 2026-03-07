import os
import requests
import schedule
import time
from datetime import datetime
import collections

# CONFIG
TELEGRAM_BOT_TOKEN = "7820830319:AAEorAawsME1kl_OjfayWsvJseuEjZ1n0tM"
TELEGRAM_CHAT_ID = "-1005066300574"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
BOT_NAME = "Eve BTC 🐻"
BASE_URL = "https://www.okx.com"

# Eve-style crowding thresholds
EXTREME_FUNDING = 0.0005
ELEVATED_FUNDING = 0.0002
HISTORY_LIMIT = 50

# History for slope/trend analysis
history = collections.deque(maxlen=HISTORY_LIMIT)

def get_okx_data(inst_id="BTC-USDT-SWAP"):
        try:
                    ticker = requests.get(f"{BASE_URL}/api/v5/market/ticker?instId={inst_id}", timeout=10).json()['data'][0]
                    funding = requests.get(f"{BASE_URL}/api/v5/public/funding-rate?instId={inst_id}", timeout=10).json()['data'][0]
                    oi_data = requests.get(f"{BASE_URL}/api/v5/public/open-interest?instId={inst_id}", timeout=10).json()['data'][0]
                    books = requests.get(f"{BASE_URL}/api/v5/market/books?instId={inst_id}", timeout=10).json()['data'][0]
                    price = float(ticker['last'])
                    funding_rate = float(funding['fundingRate'])
                    oi = float(oi_data['oi'])
                    bids_vol = sum([float(b[1]) for b in books['bids']])
                    asks_vol = sum([float(a[1]) for a in books['asks']])
                    ob_delta = bids_vol - asks_vol
                    cvd = ob_delta * 0.1
                    data = {
                        "timestamp": datetime.now(),
                        "price": price,
                        "funding_rate": funding_rate,
                        "oi": oi,
                        "ob_delta": ob_delta,
                        "cvd": cvd
                    }
                    history.append(data)
                    return data
except Exception as e:
        print(f"OKX API Error: {e}")
        return None

def calculate_slopes():
        if len(history) < 2:
                    return 0, 0
                price_change = (history[-1]['price'] - history[0]['price']) / history[0]['price']
    cvd_change = (history[-1]['cvd'] - history[0]['cvd']) / (abs(history[0]['cvd']) or 1)
    return price_change, cvd_change

def ask_claude(prompt):
        if not ANTHROPIC_API_KEY:
                    return None
                try:
                            headers = {
                                            "x-api-key": ANTHROPIC_API_KEY,
                                            "content-type": "application/json",
                                            "anthropic-version": "2023-06-01"
                            }
                            payload = {
                                "model": "claude-sonnet-4-20250514",
                                "max_tokens": 512,
                                "messages": [{"role": "user", "content": prompt}]
                            }
                            r = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload, timeout=30)
                            if r.status_code == 200:
                                            return r.json()["content"][0]["text"]
                else:
            print(f"Claude API error {r.status_code}: {r.text}")
                except Exception as e:
        print(f"Claude error: {e}")
    return None

def send_telegram(text):
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
                r = requests.post(url, json={
                                "chat_id": TELEGRAM_CHAT_ID,
                                "text": text,
                                "parse_mode": "HTML"
                }, timeout=15)
                print(f"TG send status: {r.status_code}")
                if r.status_code != 200:
                                print(f"TG error response: {r.text}")
    else:
            print("TG message sent successfully!")
        return r.status_code == 200
except Exception as e:
        print(f"TG send exception: {e}")
        return False

def analyze():
        data = get_okx_data()
    if not data:
                print("Failed to get OKX data, skipping analysis")
                return

    p_slope, c_slope = calculate_slopes()
    fr = data['funding_rate']

    # Divergence detection (Eve-style)
    divergence = ""
    if p_slope > 0 and c_slope < 0:
                divergence = 'BEARISH'
    elif p_slope < 0 and c_slope > 0:
        divergence = 'BULLISH'

    # Crowding detection (Eve-style)
    if fr >= EXTREME_FUNDING:
                crowding = "Extreme Long Crowded"
    elif fr <= -EXTREME_FUNDING:
        crowding = "Extreme Short Crowded"
elif fr >= ELEVATED_FUNDING:
        crowding = "Longs Paying"
elif fr <= -ELEVATED_FUNDING:
        crowding = "Shorts Paying"
else:
        crowding = "Neutral"

    # Signal scoring
    score = 0
    if data['ob_delta'] > 0: score += 1
            if fr < 0: score += 1
                    if divergence == 'BULLISH': score += 2
                            if c_slope > 0.2: score += 1
                                    if fr <= -EXTREME_FUNDING: score += 2
                                            if data['ob_delta'] < 0: score -= 1
                                                    if fr > 0: score -= 1
                                                            if divergence == 'BEARISH': score -= 2
                                                                    if fr >= EXTREME_FUNDING: score -= 2

    if score >= 3:
                signal = 'LONG'
    elif score <= -3:
        signal = 'SHORT'
else:
        signal = 'OBSERVE'

    rr = 2.5

    # Dynamic monitoring frequency
    freq = 480
    if abs(c_slope) > 0.2:
                freq = 30
            if abs(p_slope) > 0.05:
                        freq = 15

    # AI analysis
    ai_comment = None
    if ANTHROPIC_API_KEY:
                ai_comment = ask_claude(
                                f"You are {BOT_NAME}. BTC: ${data['price']:,.2f}, "
                                f"Funding {fr:.6f}, CVD {data['cvd']:.2f} (slope {c_slope:.3f}), "
                                f"OI {data['oi']:,.0f}, OB Delta {data['ob_delta']:.2f}. "
                                f"Crowding: {crowding}. Signal: {signal}. "
                                f"2-3 sentences: spot vs leverage? crowding risk? key level? Chinese first then English."
                )

    # Build Eve-style Telegram message
    now = datetime.now().strftime('%Y-%m-%d %H:%M UTC')

    if signal == "LONG":
                sig_emoji = "🟢"
    elif signal == "SHORT":
        sig_emoji = "🔴"
else:
        sig_emoji = "🟡"

    msg = f"<b>{BOT_NAME} BTC Signal</b>\n"
    msg += f"{now}\n"
    msg += f"Data: OKX API\n\n"
    msg += f"<b>Price: ${data['price']:,.2f}</b>\n\n"
    msg += f"Funding: {fr:.6f} ({crowding})\n"
    msg += f"CVD: {data['cvd']:.4f} (slope {c_slope:.3f})\n"
    msg += f"OI: {data['oi']:,.0f} BTC\n"
    msg += f"OB Delta: {data['ob_delta']:.4f}\n"
    if divergence:
                msg += f"Divergence: {divergence}\n"
            msg += f"\n<b>{sig_emoji} Signal: {signal}</b>\n"

    if signal != "OBSERVE":
                sl = round(data['price'] * (0.98 if signal == 'LONG' else 1.02), 1)
                tp1 = round(data['price'] * (1.03 if signal == 'LONG' else 0.97), 1)
                tp2 = round(data['price'] * (1.05 if signal == 'LONG' else 0.95), 1)
                msg += f"Entry: ${data['price']:,.2f}\n"
                msg += f"SL: ${sl:,.1f}\n"
                msg += f"TP1: ${tp1:,.1f} | TP2: ${tp2:,.1f}\n"
                msg += f"R:R = {rr}:1\n"
    else:
        msg += "Observing, waiting for consistency.\n"

    if ai_comment:
                msg += f"\n<b>AI Analysis:</b>\n{ai_comment}"

    if freq < 480:
                msg += f"\n⚠️ Anomaly detected → monitoring every {freq}min"

    msg += f"\n\nPowered by {BOT_NAME}"

    success = send_telegram(msg)
    print(f"Analysis complete. Signal: {signal}, Score: {score}, TG sent: {success}")

    # Dynamic frequency update
    schedule.clear('main-task')
    schedule.every(freq).minutes.do(analyze).tag('main-task')

# Start
schedule.every(8).hours.do(analyze).tag('main-task')

if __name__ == "__main__":
        startup = f"🚀 {BOT_NAME} started | Monitoring BTC/USDT Perps | Crowding+Divergence+Slope+AI"
    print(startup)
    send_telegram(startup)
    analyze()
    while True:
                schedule.run_pending()
                time.sleep(1)
