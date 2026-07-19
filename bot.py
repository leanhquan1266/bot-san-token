import time
import requests
import concurrent.futures
from flask import Flask
from threading import Thread

# ================= 1. CẤU HÌNH TELEGRAM =================
TELEGRAM_BOT_TOKEN = "8815874105:AAGg45aPCWiSo03fN4NQb5H9yZ_saqwkj_Q"
TELEGRAM_CHAT_ID = "1848411087"
# =======================================================

# ================= 2. BỘ LỌC CHỈ SỐ VÀNG =================
MAX_AGE_HOURS = 24        
MIN_LIQUIDITY = 40000     
MIN_VOLUME = 50000        
MIN_TXNS = 500            
MIN_HOLDERS = 400         
MAX_PRICE = 0.00001       
# =========================================================

# ================= 3. HỆ SINH THÁI (MULTI-CHAIN) =================
NETWORKS = {
    "bsc": {"name": "BSC (BNB)", "gecko": "bsc", "goplus": "56", "dex": "bsc"},
    "solana": {"name": "Solana", "gecko": "solana", "goplus": "solana", "dex": "solana"},
    "base": {"name": "Base", "gecko": "base", "goplus": "8453", "dex": "base"},
    "polygon": {"name": "Polygon", "gecko": "polygon_pos", "goplus": "137", "dex": "polygon"}
}
checked_tokens = set()

def send_telegram_msg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True}
    try: requests.post(url, data=payload)
    except: pass

def get_trending_tokens(gecko_slug):
    url = f"https://api.geckoterminal.com/api/v2/networks/{gecko_slug}/trending_pools"
    try:
        response = requests.get(url, headers={"Accept": "application/json"}).json()
        if 'data' not in response: return []
        tokens = [pool['relationships']['base_token']['data']['id'].split('_')[1] for pool in response['data']]
        return [t for t in tokens if t not in checked_tokens]
    except: return []

def get_dex_data(token_address):
    url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
    try:
        res = requests.get(url).json()
        pairs = res.get('pairs', [])
        if not pairs: return None
        
        pair = pairs[0]
        created_at = pair.get('pairCreatedAt')
        if not created_at: return None
        
        age_hours = (int(time.time() * 1000) - created_at) / (1000 * 60 * 60)
        price_usd = float(pair.get('priceUsd', 0))
        liquidity = float(pair.get('liquidity', {}).get('usd', 0))
        volume = float(pair.get('volume', {}).get('h24', 0))
        mcap = float(pair.get('marketCap', pair.get('fdv', 0)))
        
        txns_data = pair.get('txns', {}).get('h24', {})
        buys = int(txns_data.get('buys', 0))
        sells = int(txns_data.get('sells', 0))
        total_txns = buys + sells
        
        websites = pair.get('info', {}).get('websites', [])
        socials = pair.get('info', {}).get('socials', [])
        web = websites[0]['url'] if websites else ""
        tg = tw = ""
        for s in socials:
            if s['type'] == 'telegram': tg = s['url']
            elif s['type'] == 'twitter': tw = s['url']
            
        return {
            "age_hours": age_hours,
            "price": price_usd,
            "liquidity": liquidity,
            "volume": volume,
            "mcap": mcap,
            "buys": buys,
            "sells": sells,
            "txns": total_txns,
            "twitter": tw,
            "telegram": tg,
            "website": web
        }
    except: return None

def analyze_token(token_address, net_config):
    dex_info = get_dex_data(token_address)
    if not dex_info: return
    
    if dex_info["age_hours"] > MAX_AGE_HOURS: return
    if dex_info["liquidity"] < MIN_LIQUIDITY: return
    if dex_info["volume"] < MIN_VOLUME: return
    if dex_info["txns"] < MIN_TXNS: return
    if dex_info["price"] == 0 or dex_info["price"] > MAX_PRICE: return 

    chain_id = net_config['goplus']
    url = f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}?contract_addresses={token_address}"
    try:
        response = requests.get(url).json()
        if response.get('code') != 1 or not response.get('result'): return
        
        data = response['result'].get(token_address.lower(), response['result'].get(token_address, {}))
        if not data: return

        holders_count = int(data.get('holder_count', 0))
        if chain_id != 'solana':
            if holders_count < MIN_HOLDERS: return 
            
            # --- CẬP NHẬT: KHIÊN CHỐNG FAKE HOLDERS (ÁP DỤNG MỌI MẠNG) ---
        holders_count = int(data.get('holder_count', 0))
        
        # Kiểm tra tối thiểu ví theo cấu hình
        if holders_count < MIN_HOLDERS: 
            return 
            
        # So sánh số lượng ví với số lệnh Mua (Buys)
        # Nếu số ví Hold nhiều hơn số lệnh Mua 1.2 lần -> Khả năng cao là ví ảo
        if holders_count > (dex_info['buys'] * 1.2):
            return
        # -------------------------------------------------------------

        if data.get('is_honeypot', '0') == '1' or data.get('is_mintable', '0') == '1': return
        buy_tax = float(data.get('buy_tax', '0')) * 100 if data.get('buy_tax') else 0
        sell_tax = float(data.get('sell_tax', '0')) * 100 if data.get('sell_tax') else 0
        if buy_tax > 5 or sell_tax > 5: return

        holders_list = data.get('holders', [])
        whale_warning = ""
        for h in holders_list:
            if h.get('is_contract', 0) == 0 and h.get('is_locked', 0) == 0:
                percent = float(h.get('percent', '0'))
                if percent > 15.0: return 
                elif percent > 5.0: whale_warning += f"- Ví <code>{h.get('address')[:6]}...</code> ({percent:.1f}%)\n"

        checked_tokens.add(token_address)

        buy_pressure = (dex_info['buys'] / dex_info['txns']) * 100 if dex_info['txns'] > 0 else 0
        vol_mcap_ratio = (dex_info['volume'] / dex_info['mcap']) if dex_info['mcap'] > 0 else 0

        fire_emoji = "🔥"
        if vol_mcap_ratio > 1.0 and buy_pressure > 60: fire_emoji = "🔥🔥🔥 CỰC NÓNG (FOMO LỚN)"
        elif vol_mcap_ratio > 0.5 and buy_pressure > 50: fire_emoji = "🔥🔥 ĐANG BƠM MẠNH"

        formatted_price = f"{dex_info['price']:.12f}".rstrip('0').rstrip('.')

        tele_msg = f"🏆 <b>SIÊU PHẨM DÒNG TIỀN MẠNH</b>\n"
        tele_msg += f"🌐 Mạng: <b>{net_config['name']}</b>\n"
        tele_msg += f"📝 Contract: <code>{token_address}</code>\n"
        tele_msg += f"\n📊 <b>CHỈ SỐ FOMO:</b> {fire_emoji}\n"
        tele_msg += f"🟢 Lực Mua: <b>{buy_pressure:.1f}%</b> ({dex_info['buys']} M/ {dex_info['sells']} B)\n"
        tele_msg += f"🌪 Vol/MCap Ratio: <b>{vol_mcap_ratio:.2f}x</b>\n"
        tele_msg += f"\n💲 Giá: <b>${formatted_price}</b>\n"
        tele_msg += f"⏱ Tuổi đời: <b>{dex_info['age_hours']:.1f} giờ</b>\n"
        tele_msg += f"💰 Vốn hóa: <b>${dex_info['mcap']:,.0f}</b>\n"
        tele_msg += f"💧 Thanh khoản: <b>${dex_info['liquidity']:,.0f}</b>\n"
        
        if chain_id != 'solana':
            tele_msg += f"👥 Holders: <b>{holders_count:,} ví</b>\n"
            tele_msg += f"💸 Thuế: Mua {buy_tax}% | Bán {sell_tax}%\n"
            tele_msg += f"🕵️‍♂️ Code & Dev: <b>Sạch 100%</b>\n"
            
        if whale_warning: tele_msg += f"\n🐋 <b>Cá voi gom hàng:</b>\n{whale_warning}"
        
        tele_msg += f"\n🔗 <b>Dự án:</b>"
        if dex_info['twitter']: tele_msg += f" 🐦 <a href='{dex_info['twitter']}'>Twitter</a> |"
        if dex_info['website']: tele_msg += f" 🌐 <a href='{dex_info['website']}'>Web</a> |"
        if dex_info['telegram']: tele_msg += f" ✈️ <a href='{dex_info['telegram']}'>Tele</a>"
        if not (dex_info['twitter'] or dex_info['website'] or dex_info['telegram']): 
            tele_msg += " <i>(Chưa cập nhật Social)</i>"
            
        tele_msg += f"\n\n📈 <a href='https://dexscreener.com/{net_config['dex']}/{token_address}'>Mở Biểu Đồ DexScreener</a>"

        print(f"\n[+] ĐÃ TÌM THẤY KÈO CHẤT LƯỢNG ({net_config['name']}): {token_address}. Đang báo Telegram!")
        send_telegram_msg(tele_msg)
            
    except Exception as e:
        pass

def bot_loop():
    print("🚀 BOT ĐÃ CHẠY TRÊN WEB 24/7!")
    send_telegram_msg("🤖 <b>Bot Web Đã Bật!</b>\nHệ thống đang chạy ngầm trên đám mây 24/7.")
    while True:
        for net_key, net_config in NETWORKS.items():
            tokens = get_trending_tokens(net_config['gecko'])
            if tokens:
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                    for token in tokens:
                        executor.submit(analyze_token, token, net_config)
                        time.sleep(0.2) 
        time.sleep(60)

# ================= WEB SERVER GỈA ĐỂ GIỮ BOT SỐNG 24/7 =================
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ MÁY CHỦ BOT SĂN TOKEN ĐANG HOẠT ĐỘNG 24/7!"

def run_bot_in_background():
    # Khởi động Bot chạy ngầm
    t = Thread(target=bot_loop)
    t.daemon = True
    t.start()

if __name__ == "__main__":
    # 1. Bật bot quét token trước
    run_bot_in_background()
    
    # 2. Bật trang web ảo (để UptimeRobot gõ cửa)
    import os
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    keep_alive() # Bật trang web ảo trước
    bot_loop()   # Sau đó mới chạy bot
