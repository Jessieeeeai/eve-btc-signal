import os
import time
import requests
import schedule
from datetime import datetime, timezone

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '7820830319:AAEorAawsME1kl_OjfayWsvJseuEjZ1n0tM')
TELEGRAM_CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID',   '-5066300574')
ANTHROPIC_API_KEY  = os.environ.get('ANTHROPIC_API_KEY',  '')
BOT_NAME           = 'Eve'
prev_snapshot = None

def get_okx_data(inst_id='BTC-USDT-SWAP'):
    try:
        base = 'https://www.okx.com'
        ticker = requests.get(f'{base}/api/v5/market/ticker?instId={inst_id}', timeout=10).json()['data'][0]
        price  = float(ticker['last'])
        open24 = float(ticker['open24h'])
        pct24  = (price - open24) / open24 * 100
        funding = requests.get(f'{base}/api/v5/public/funding-rate?instId={inst_id}', timeout=10).json()['data'][0]
        fr      = float(funding['fundingRate'])
        oi_r = requests.get(f'{base}/api/v5/rubik/stat/contracts/open-interest-volume?ccy=BTC&period=5m&limit=20', timeout=10).json()
        oi   = float(oi_r['data'][0][2]) if oi_r.get('data') else 0
        trades = requests.get(f'{base}/api/v5/market/trades?instId={inst_id}&limit=500', timeout=10).json()['data']
        cvd_futures = sum(float(t['sz']) * (1 if t['side'] == 'buy' else -1) for t in trades)
        spot_trades = requests.get(f'{base}/api/v5/market/trades?instId=BTC-USDT&limit=500', timeout=10).json().get('data', [])
        cvd_spot = sum(float(t['sz']) * (1 if t['side'] == 'buy' else -1) for t in spot_trades)
        book = requests.get(f'{base}/api/v5/market/books?instId={inst_id}&sz=20', timeout=10).json()['data'][0]
        bid_vol = sum(float(b[1]) for b in book['bids'][:10])
        ask_vol = sum(float(a[1]) for a in book['asks'][:10])
        ob_delta = bid_vol - ask_vol
        return {'price': price, 'pct24': pct24, 'fr': fr, 'oi': oi, 'cvd_futures': cvd_futures, 'cvd_spot': cvd_spot, 'ob_delta': ob_delta}
    except Exception as e:
        print(f'OKX error: {e}')
        return None

def ask_claude(prompt):
    if not ANTHROPIC_API_KEY:
        return ''
    try:
        resp = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={'x-api-key': ANTHROPIC_API_KEY, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
            json={'model': 'claude-sonnet-4-20250514', 'max_tokens': 250, 'messages': [{'role': 'user', 'content': prompt}]},
            timeout=20,
        ).json()
        return resp['content'][0]['text'].strip()
    except Exception as e:
        print(f'Claude error: {e}')
        return ''

def get_verdict(signal, fr, cvd_f, pct24, prev):
    if signal == 'LONG':  return 'LONG_SIGNAL'
    if signal == 'SHORT': return 'SHORT_SIGNAL'
    if prev:
        fr_ch = fr - prev['fr']
        if abs(fr) < 0.0001 and abs(fr_ch) < 0.0001: return 'WATCH_NEUTRAL'
        if fr > 0 and fr < prev['fr'] and cvd_f > prev['cvd_futures']: return 'WATCH_STABILIZING'
        if fr < 0 and fr > prev['fr']: return 'WATCH_RECOVERING'
        if pct24 < -2 and cvd_f < 0: return 'WATCH_BEARISH_PRESSURE'
        if pct24 > 2 and cvd_f > 0:  return 'WATCH_BULLISH_PRESSURE'
    return 'WATCH_MONITORING'

def get_mode(fr, cvd_f, pct24):
    if abs(fr) > 0.0003 or abs(cvd_f) > 500 or abs(pct24) > 3:
        return 'high_frequency (极端资金费率/CVD异常)'
    if abs(fr) > 0.0001 or abs(cvd_f) > 200:
        return 'enhanced (中等异常信号)'
    return 'standard_8h (正常监控)'

def analyze():
    global prev_snapshot
    data = get_okx_data()
    if not data:
        return
    fr = data['fr']
    price = data['price']
    pct24 = data['pct24']
    oi = data['oi']
    cvd_f = data['cvd_futures']
    cvd_s = data['cvd_spot']
    ob = data['ob_delta']

    if fr > 0.0003:      crowding = 'Extreme Long Crowded'
    elif fr < -0.0003:   crowding = 'Extreme Short Crowded'
    elif fr > 0.0001:    crowding = 'Longs Paying'
    elif fr < -0.0001:   crowding = 'Shorts Paying'
    else:                crowding = 'Neutral'

    score = 0
    if cvd_f > 50:   score += 2
    elif cvd_f > 0:  score += 1
    if cvd_f < -50:  score -= 2
    elif cvd_f < 0:  score -= 1
    if ob > 0:       score += 1
    if ob < 0:       score -= 1
    if fr < -0.0001: score += 1
    if fr >  0.0001: score -= 1
    if cvd_s > 0 and cvd_f < 0: score += 1
    if cvd_s < 0 and cvd_f > 0: score -= 1

    if score >= 3:    signal = 'LONG'
    elif score <= -3: signal = 'SHORT'
    else:             signal = 'OBSERVE'

    long_ok  = fr < -0.0004
    short_ok = fr >  0.0004
    obs_ok   = not long_ok and not short_ok
    long_need  = f'资金费率需 < -0.04% (当前 {fr*100:+.2f}%)'
    short_need = f'资金费率需 > +0.04% (当前 {fr*100:+.2f}%)'
    verdict = get_verdict(signal, fr, cvd_f, pct24, prev_snapshot)
    mode    = get_mode(fr, cvd_f, pct24)

    freq = 480
    if abs(cvd_f) > 500 or abs(fr) > 0.0003 or abs(pct24) > 3: freq = 30
    elif abs(cvd_f) > 200 or abs(fr) > 0.0001:                  freq = 60

    now_utc = datetime.now(timezone.utc)
    try:
        ts = now_utc.strftime('%Y-%m-%d %-I:%M %p PT')
    except Exception:
        ts = now_utc.strftime('%Y-%m-%d %I:%M %p PT')

    fr_arrow = '\U00002B07' if fr < 0 else '\U00002B06'
    ob_side  = '买方' if ob > 0 else '卖方'

    msg  = f'<b>BTC 8小时监控报告 \u2014 {ts}</b>\n\n'
    msg += f'\U0001F4CA <b>当前数据 (OKX BTC/USDT)</b>\n'
    msg += f'\u2022 价格: ${price:,.2f} ({pct24:+.2f}%)\n'
    msg += f'\u2022 资金费率: {fr*100:+.4f}% {fr_arrow}\n'
    msg += f'\u2022 持仓量: {oi/1000:.3f}K BTC\n'
    msg += f'\u2022 CVD(期货): {cvd_f:+.3f}\n'
    msg += f'\u2022 CVD(现货): {cvd_s:+.1f}\n'
    msg += f'\u2022 订单簿: {ob:+.1f} ({ob_side})\n\n'

    if prev_snapshot:
        p = prev_snapshot
        hrs = max(1, round(freq/60)) if freq < 480 else 8
        price_ch = f"{p['price']:,.0f} \u2192 {price:,.0f} ({(price-p['price'])/p['price']*100:+.1f}%)"
        fr_ch    = f"{p['fr']*100:+.4f}% \u2192 {fr*100:+.4f}%"
        oi_ch    = f"{p['oi']/1000:.3f}K \u2192 {oi/1000:.3f}K ({(oi-p['oi'])/max(p['oi'],1)*100:+.1f}%)"
        cvd_ch   = f"{p['cvd_futures']:+.3f} \u2192 {cvd_f:+.3f}"
        fr_cmt  = ' (大幅正常化!)' if abs(p['fr']) > abs(fr) and abs(p['fr']-fr) > 0.0001 else (' (费率上升!)' if fr > p['fr'] + 0.0001 else '')
        cvd_cmt = ' (期货买盘回归)' if cvd_f > p['cvd_futures'] else (' (期货卖压增加)' if cvd_f < p['cvd_futures'] else '')
        msg += f'\U0001F4C8 <b>变化 (vs {hrs}小时前 @ {p["time"]})</b>\n'
        msg += f'\u2022 价格: {price_ch}\n'
        msg += f'\u2022 资金费率: {fr_ch}{fr_cmt}\n'
        msg += f'\u2022 持仓量: {oi_ch}\n'
        msg += f'\u2022 CVD: {cvd_ch}{cvd_cmt}\n\n'

    long_mark  = '\u2705' if long_ok  else '\u274C'
    short_mark = '\u2705' if short_ok else '\u274C'
    obs_mark   = '\u2705' if obs_ok   else '\u274C'

    msg += f'\U0001F50D <b>信号分析</b>\n'
    msg += f'\u2022 做多: {long_mark} {long_need}\n'
    msg += f'\u2022 做空: {short_mark} {short_need}\n'
    if obs_ok:
        obs_reason = f'资金费率在中性区 ({crowding})'
        if pct24 < -1:   obs_reason += f' + 价格回调{pct24:.1f}%'
        elif pct24 > 1:  obs_reason += f' + 价格上涨{pct24:.1f}%'
        msg += f'\u2022 观望: {obs_mark} {obs_reason}\n\n'
    else:
        msg += '\n'

    msg += f'\U0001F4CB <b>判断: {verdict}</b>\n'

    if ANTHROPIC_API_KEY:
        explanation = ask_claude(
            f'You are {BOT_NAME}, a BTC trading bot. Write 2 sentences in Chinese explaining the verdict {verdict}. '
            f'BTC ${price:,.0f} ({pct24:+.1f}%), FR {fr*100:+.4f}%, OI {oi/1000:.2f}K, '
            f'Futures CVD {cvd_f:+.0f}, Spot CVD {cvd_s:+.0f}, OB {ob:+.0f}, {crowding}.'
        )
    else:
        if verdict == 'WATCH_STABILIZING':
            explanation = f'资金费率从高位回落至{fr*100:+.4f}%，多空力量趋于平衡。CVD改善说明期货市场有买盘流入，价格在支撑位企稳。'
        elif verdict == 'WATCH_NEUTRAL':
            explanation = '市场处于中性状态，资金费率接近零，多空博弈均衡。等待方向性突破信号。'
        elif verdict == 'WATCH_RECOVERING':
            explanation = '资金费率从负值回升，空头超配正在松动。CVD改善说明底部买盘在积累。'
        elif verdict == 'WATCH_BEARISH_PRESSURE':
            explanation = f'价格下跌{abs(pct24):.1f}%，CVD持续为负，卖压仍在。需等待CVD企稳才考虑做多。'
        elif verdict == 'WATCH_BULLISH_PRESSURE':
            explanation = f'价格上涨{pct24:.1f}%，CVD持续为正，买压强劲。但资金费率未达阈值，谨慎追高。'
        elif verdict == 'LONG_SIGNAL':
            explanation = f'资金费率深度为负({fr*100:+.4f}%)，空头过度拥挤，反弹条件成熟。'
        elif verdict == 'SHORT_SIGNAL':
            explanation = f'资金费率高达{fr*100:+.4f}%，多头极度拥挤，获利回吐压力大。'
        else:
            explanation = f'当前信号混合，建议继续观察。资金费率{fr*100:+.4f}%，CVD {cvd_f:+.0f}。'
    msg += explanation + '\n\n'

    observations = []
    if fr > 0.0002:    observations.append(f'资金费率{fr*100:+.4f}%偏高 \u2192 若转负则做多信号形成')
    elif fr < -0.0002: observations.append(f'资金费率{fr*100:+.4f}%偏低 \u2192 若持续则做多信号增强')
    else:              observations.append(f'资金费率{fr*100:+.4f}%处于中性区 \u2192 无明显方向偏向')

    key_level = round(price / 1000) * 1000
    if pct24 < -1:
        observations.append(f'{key_level:,}支撑位目前{"有效" if price > key_level * 0.992 else "告急"}')
    elif pct24 > 1:
        observations.append(f'{key_level:,}阻力位，关注能否有效突破')
    else:
        observations.append(f'{key_level:,}关键整数位，价格在此附近整理')

    if cvd_f > 50 and cvd_s < -50:
        observations.append('期货买入 + 现货卖出 \u2192 可能为对冲，谨慎看涨')
    elif cvd_f < -50 and cvd_s > 50:
        observations.append('期货卖出 + 现货买入 \u2192 可能为移仓，注意方向')
    elif cvd_f > 30 and cvd_s > 30:
        observations.append('期货CVD + 现货CVD同步改善 \u2192 真实买盘特征')
    elif cvd_f < -30 and cvd_s < -30:
        observations.append('期货CVD + 现货CVD同步恶化 \u2192 真实卖盘特征')
    else:
        if prev_snapshot and cvd_f > prev_snapshot.get('cvd_futures', 0):
            observations.append('CVD改善 + 费率正常化 = 短期底部特征')
        else:
            observations.append('CVD持平，市场方向待确认')

    msg += f'\U0001F4A1 <b>观察要点</b>\n'
    for obs in observations[:4]:
        msg += f'\u2022 {obs}\n'
    msg += '\n'
    msg += f'<b>监控模式: {mode}</b>'

    print(msg[:200])
    send_telegram(msg)

    prev_snapshot = {
        'price': price, 'fr': fr, 'oi': oi,
        'cvd_futures': cvd_f, 'cvd_spot': cvd_s,
        'ob_delta': ob, 'score': score,
        'time': now_utc.strftime('%I:%M %p').lstrip('0'),
    }

    schedule.clear('main-task')
    schedule.every(freq).minutes.do(analyze).tag('main-task')


def send_telegram(text):
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
    try:
        resp = requests.post(url, json={'chat_id': TELEGRAM_CHAT_ID, 'text': text, 'parse_mode': 'HTML'}, timeout=15)
        if resp.status_code != 200:
            print(f'TG error {resp.status_code}: {resp.text}')
        else:
            print('TG sent OK')
    except Exception as e:
        print(f'TG error: {e}')


schedule.every(8).hours.do(analyze).tag('main-task')

if __name__ == '__main__':
    startup = 'Eve BTC 8h Monitor started | OKX BTC/USDT | Crowding+CVD+OB+AI'
    print(startup)
    send_telegram(startup)
    analyze()
    while True:
        schedule.run_pending()
        time.sleep(1)
