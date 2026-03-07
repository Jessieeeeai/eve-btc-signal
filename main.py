import os
import requests
import schedule
import time
from datetime import datetime
import collections

# CONFIG
TELEGRAM_BOT_TOKEN = "7820830319:AAEorAawsME1kl_OjfayWsvJseuEjZ1n0tM"
TELEGRAM_CHAT_ID = "-5066300574"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
BOT_NAME = "Eve BTC"
BASE_URL = "https://www.okx.com"

# Eve-style crowding thresholds
EXTREME_FUNDING = 0.0005
ELEVATED_FUNDING = 0.0002
HISTORY_LIMIT = 50

# Signal counter for numbering
signal_counter = 0

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

        # Approximate CVD from order book delta
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
        print(f"Error fetching OKX data: {e}")
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
                        except Exception as e:
                                        print(f"Claude error: {e}")
                                    return None

def analyze():
            global signal_counter
    data = get_okx_data()
    if not data:
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
                                                    if fr <= -EXTREME_FUNDING: score += 2   # extreme short -> long bias
    if data['ob_delta'] < 0: score -= 1
                if fr > 0: score -= 1
                            if divergence == 'BEARISH': score -= 2
                                        if fr >= EXTREME_FUNDING: score -= 2    # extreme long -> short bias

    if score >= 3:
                    signal = 'LONG'
elif score <= -3:
        signal = 'SHORT'
else:
        signal = 'OBSERVE'

    # R:R calculation
    rr = 2.5

    # Dynamic monitoring frequency
    freq = 480  # 8h default
    if abs(c_slope) > 0.2:
                    freq = 30   # 30min on CVD anomaly
    if abs(p_slope) > 0.05:
                    freq = 15   # 15min on high volatility

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
    signal_counter += 1
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')

    if signal == "LONG":
                    sig_emoji = "\U0001F7E2"  # green circle
        direction = "LONG"
elif signal == "SHORT":
        sig_emoji = "\U0001F534"  # red circle
        direction = "SHORT"
else:
        sig_emoji = "\U0001F7E1"  # yellow circle
        direction = "OBSERVE"

    msg = f"<b>{BOT_NAME} #{signal_counter:03d} BTC {direction} Signal</b>\n"
    msg += f"{now}\n"
    msg += f"Data: OKX API\n\n"
    msg += f"<b>\U0001F4B0 Price: ${data['price']:,.2f}</b>\n\n"
    msg += f"\U0001F4CA 资金费率: {fr:.6f} ({crowding})\n"
    msg += f"\U0001F4C8 CVD: {data['cvd']:.4f} (slope {c_slope:.3f})\n"
    msg += f"\U0001F4CC OI: {data['oi']:,.0f} BTC\n"
    msg += f"\U0001F4CB OB Delta: {data['ob_delta']:.4f}\n"
    if divergence:
                    msg += f"\U000026A0 Divergence: {divergence}\n"
                msg += f"\n{sig_emoji} <b>Signal: {direction}</b>\n"

    if signal != "OBSERVE":
                    if signal == 'LONG':
                                        sl = round(data['price'] * 0.98, 1)
                                        tp1 = round(data['price'] * 1.03, 1)
                                        tp2 = round(data['price'] * 1.05, 1)
                                        sl_pct = "-2.00%"
                                        tp1_pct = "+3.00%"
                                        tp2_pct = "+5.00%"
    else:
            sl = round(data['price'] * 1.02, 1)
            tp1 = round(data['price'] * 0.97, 1)
            tp2 = round(data['price'] * 0.95, 1)
            sl_pct = "+2.00%"
            tp1_pct = "-3.00%"
            tp2_pct = "-5.00%"

        msg += f"\U0001F3AF \u5165\u573a: ${data['price']:,.2f}\n"
        msg += f"\U0001F6D1 \u6b62\u635f: ${sl:,.1f} ({sl_pct})\n"
        msg += f"TP1: ${tp1:,.1f} ({tp1_pct}) — \u89E6\u53D1\u540E\u5E7350%\uff0cSL\u79FB\u81F3\u6210\u672C\n"
        msg += f"TP2: ${tp2:,.1f} ({tp2_pct})\n"
        msg += f"R:R = {rr}:1\n"
else:
        msg += "\u89C2\u5BDF\u4E2D\uff0c\u7B49\u5F85\u6570\u636E\u4E00\u81F4\u6027\u3002\n"

    if ai_comment:
                    msg += f"\n\U0001F916 AI\u5206\u6790: {ai_comment}"

    if freq < 480:
                    msg += f"\n\u26A0 \u5F02\u5E38\u68C0\u6D4B \u2192 \u76D1\u63A7\u9891\u7387\u8C03\u6574\u4E3A {freq}min"

    msg += f"\n\n\U0001F916 Powered by {BOT_NAME}"

    send_telegram(msg)

    # Dynamic frequency update
    schedule.clear('main-task')
    schedule.every(freq).minutes.do(analyze).tag('main-task')

def send_telegram(text):
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
                    resp = requests.post(url, json={
                                        "chat_id": TELEGRAM_CHAT_ID,
                                        "text": text,
                                        "parse_mode": "HTML"
                    }, timeout=15)
                    if resp.status_code != 200:
                                        print(f"TG error {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"TG send error: {e}")

schedule.every(8).hours.do(analyze).tag('main-task')

if __name__ == "__main__":
            startup = (
                f"\U0001F916 {BOT_NAME} started\n"
                f"\U0001F4CA Monitoring BTC/USDT Perps\n"
                f"\U0001F527 Mode: Crowding + Divergence + Slope + AI\n"
                f"\U000023F0 Default interval: 8h (auto-adjusts on anomaly)"
)
    print(startup)
    send_telegram(startup)
    analyze()
    while True:
                    schedule.run_pending()
                    time.sleep(1)
