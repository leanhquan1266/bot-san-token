import time
import requests
import concurrent.futures
from flask import Flask
from threading import Thread
import os

# ================= 1. CẤU HÌNH HỆ THỐNG =================
TELEGRAM_BOT_TOKEN = "8815874105:AAGg45aPCWiSo03fN4NQb5H9yZ_saqwkj_Q"
TELEGRAM_CHAT_ID = "1848411087"

# CẤU HÌNH TỐC ĐỘ & BỘ LỌC
MAX_WORKERS = 20          # Số luồng quét đồng thời
MAX_AGE_HOURS = 24        
MIN_LIQUIDITY = 40000     
MIN_VOLUME = 70000        
MIN_TXNS = 700            
MIN_HOLDERS = 400         
MAX_PRICE = 0.00001       

# SESSION ĐỂ TỐI ƯU TỐC ĐỘ API
session = requests.Session()
session.headers.update({'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'})

# DANH SÁCH MẠNG LƯỚI
NETWORKS = {
    "bsc": {"name": "BSC (BNB)", "gecko": "bsc", "goplus": "56", "dex": "bsc"},
    "solana": {"name": "Solana", "gecko": "solana", "goplus": "solana", "dex": "solana"},
    "base": {"name": "Base", "gecko": "base", "goplus": "8453", "dex": "base"},
    "polygon": {"name": "Polygon", "gecko": "polygon_pos", "goplus": "137", "dex": "polygon"},
    "sui": {"name": "Sui", "gecko": "sui", "goplus": "sui", "dex": "sui"},
    "tron": {"name": "Tron", "gecko": "tron", "goplus": "tron", "dex": "tron"},
    "ton": {"name": "TON", "gecko": "ton", "goplus": "ton", "dex": "ton"},
    "arbitrum": {"name": "Arbitrum", "gecko": "arbitrum", "goplus": "42161", "dex": "arbitrum"}
}
checked_tokens = set()

# ================= 2. CÁC HÀM XỬ LÝ =================

def send_telegram_msg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True}
    try: session.post(url, data=payload, timeout=5)
    except: pass

def get_trending_tokens(gecko_slug):
    url = f"https://api.geckoterminal.com/api/v2/networks/{gecko_slug}/trending_pools"
    try:
        data = session.get(url, timeout=10).json().get('data', [])
        tokens = [p['relationships']['base_token']['data']['id'].split('_')[1] for p in data]
        return [t for t in tokens if t not in checked_tokens]
    except: return []

def get_token_data(token_address, net_config):
    # Lấy dữ liệu DexScreener
    dex_url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
    try:
        pair = session.get(dex_url, timeout=10).json().get('pairs', [])
        if not pair: return None
        pair = pair[0]
        
        # Tính toán cơ bản
        age_hours = (int(time.time() * 1000) - pair.get('pairCreatedAt', 0)) / (1000 * 60 * 60)
        price = float(pair.get('priceUsd', 0))
        txns = pair.get('txns', {}).get('h24', {})
        buys = int(txns.get('buys', 0))
        
        return {
            "age_hours": age_hours, "price": price, "buys": buys,
            "liquidity": float(pair.get('liquidity', {}).get('usd', 0)),
            "volume": float(pair.get('volume', {}).get('h24', 0)),
            "mcap": float(pair.get('marketCap', pair.get('fdv', 0))),
            "txns": buys + int(txns.get('sells', 0)),
            "info": pair.get('info', {})
        }
    except: return None

def analyze_token(token_address, net_config):
    dex = get_token_data(token_address, net_config)
    if not dex or dex["age_hours"] > MAX_AGE_HOURS or dex["liquidity"] < MIN_LIQUIDITY or \
       dex["volume"] < MIN_VOLUME or dex["txns"] < MIN_TXNS or dex["price"] > MAX_PRICE or dex["price"] == 0:
        return

    # Lấy dữ liệu bảo mật
    goplus_url = f"https://api.gopluslabs.io/api/v1/token_security/{net_config['goplus']}?contract_addresses={token_address}"
    try:
        data = session.get(goplus_url, timeout=10).json().get('result', {}).get(token_address.lower(), {})
        if not data: return
        
        # Check Fake Holders
        holders = int(data.get('holder_count', 0))
        if holders < MIN_HOLDERS or holders > (dex['buys'] * 1.2): return
        
        # Check Honeypot/Tax
        if data.get('is_honeypot') == '1' or float(data.get('buy_tax', 0)) > 0.05 or float(data.get('sell_tax', 0)) > 0.05: return

        checked_tokens.add(token_address)
        
        # Gửi cảnh báo
        buy_pressure = (dex['buys'] / dex['txns']) * 100 if dex['txns'] > 0 else 0
        fire = "🔥🔥🔥 CỰC NÓNG" if buy_pressure > 60 else "🔥 TIỀM NĂNG"
        
        msg = f"🏆 <b>SIÊU PHẨM {net_config['name'].upper()}</b>\n" \
              f"📝 Contract: <code>{token_address}</code>\n" \
              f"\n📊 {fire} | 🟢 Lực mua: {buy_pressure:.1f}%\n" \
              f"💲 Giá: ${dex['price']:.12f}\n" \
              f"💰 Vốn: ${dex['mcap']:,.0f} | 💧 Liq: ${dex['liquidity']:,.0f}\n" \
              f"👥 Holders: {holders:,}\n" \
              f"\n📈 <a href='https://dexscreener.com/{net_config['dex']}/{token_address}'>Mở Biểu Đồ</a>"
        send_telegram_msg(msg)
    except: pass

def bot_loop():
    print("🚀 BOT V11 ĐÃ KHỞI ĐỘNG TỐI ƯU!")
    while True:
        for key, config in NETWORKS.items():
            tokens = get_trending_tokens(config['gecko'])
            if tokens:
                with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    for t in tokens: executor.submit(analyze_token, t, config)
        time.sleep(30)

# ================= 3. KHỞI CHẠY WEB & BOT =================
app = Flask(__name__)
@app.route('/')
def home(): return "✅ BOT V11 ACTIVE"

if __name__ == "__main__":
    Thread(target=bot_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
