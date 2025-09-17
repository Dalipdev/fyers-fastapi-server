import requests
import hashlib
import time
from fyers_apiv3 import fyersModel
from datetime import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import threading
import random
import os
import pytz

# ------------------ Environment Variables ------------------
CLIENT_ID = os.getenv("CLIENT_ID")
SECRET_KEY = os.getenv("SECRET_KEY")
REFRESH_TOKEN = os.getenv("REFRESH_TOKEN")
PIN = os.getenv("PIN")

# ------------------ FastAPI Setup ------------------
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------ Shared Data ------------------
latest_data = {}
cache_expiry = {}
active_symbols = set()

# ------------------ Stock List ------------------
all_symbols = [
    "NSE:ADANIENT-EQ","NSE:ADANIPORTS-EQ","NSE:APOLLOHOSP-EQ","NSE:ASIANPAINT-EQ","NSE:BAJAJ-AUTO-EQ",
    "NSE:BAJFINANCE-EQ","NSE:BAJAJFINSV-EQ","NSE:BEL-EQ","NSE:BHARTIARTL-EQ","NSE:CIPLA-EQ","NSE:COALINDIA-EQ",
    "NSE:DRREDDY-EQ","NSE:EICHERMOT-EQ","NSE:GRASIM-EQ","NSE:HCLTECH-EQ","NSE:HDFCLIFE-EQ",
    "NSE:HEROMOTOCO-EQ","NSE:HINDALCO-EQ","NSE:HINDUNILVR-EQ","NSE:INFY-EQ","NSE:ITC-EQ",
    "NSE:JIOFIN-EQ","NSE:JSWSTEEL-EQ","NSE:LT-EQ","NSE:MARUTI-EQ","NSE:NESTLEIND-EQ",
    "NSE:NTPC-EQ","NSE:ONGC-EQ","NSE:POWERGRID-EQ","NSE:RELIANCE-EQ","NSE:SBILIFE-EQ",
    "NSE:SHRIRAMFIN-EQ","NSE:SUNPHARMA-EQ","NSE:TCS-EQ","NSE:TATACONSUM-EQ","NSE:TATAMOTORS-EQ",
    "NSE:TATASTEEL-EQ","NSE:TECHM-EQ","NSE:TITAN-EQ","NSE:TRENT-EQ","NSE:ULTRACEMCO-EQ",
    "NSE:WIPRO-EQ","NSE:HDFCBANK-EQ","NSE:ICICIBANK-EQ","NSE:SBIN-EQ","NSE:KOTAKBANK-EQ",
    "NSE:AXISBANK-EQ","NSE:INDUSINDBK-EQ","NSE:FEDERALBNK-EQ","NSE:IDFCFIRSTB-EQ","NSE:BANKBARODA-EQ",
    "NSE:PNB-EQ","NSE:CANBK-EQ","NSE:AUBANK-EQ"
]

# ------------------ Trading Window Check ------------------
IST = pytz.timezone("Asia/Kolkata")

def is_market_open():
    now = datetime.now(IST)
    return (
        now.weekday() < 5 and
        (now.hour > 9 or (now.hour == 9 and now.minute >= 14)) and
        (now.hour < 15 or (now.hour == 15 and now.minute <= 31))
    )

# ------------------ Token Refresh ------------------
def get_appid_hash(client_id, secret_key):
    return hashlib.sha256(f"{client_id}:{secret_key}".encode()).hexdigest()

def get_access_token():
    url = "https://api-t1.fyers.in/api/v3/validate-refresh-token"
    payload = {
        "grant_type": "refresh_token",
        "appIdHash": get_appid_hash(CLIENT_ID, SECRET_KEY),
        "refresh_token": REFRESH_TOKEN,
        "pin": PIN
    }
    headers = {"Content-Type": "application/json"}
    res = requests.post(url, json=payload, headers=headers).json()
    if res.get("s") == "ok" and "access_token" in res:
        print("✅ Token refreshed")
        return res["access_token"]
    else:
        raise Exception(f"❌ Token refresh failed: {res}")

# ------------------ Background Worker ------------------
def track_all(interval=300):  # 5 minutes
    prev_volume, prev_ltp = {}, {}
    for sym in all_symbols:
        active_symbols.add(sym)

    ACCESS_TOKEN, fyers = None, None

    while True:
        now = datetime.now(IST)
        if is_market_open():
            try:
                if not fyers:
                    try:
                        ACCESS_TOKEN = get_access_token()
                        fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN)
                    except Exception as e:
                        print("⚠️ Token fetch failed, dummy mode:", e)
                        fyers = None

                res = fyers.quotes({"symbols": ",".join(all_symbols)}) if fyers else None
                now_ts = time.time()

                if res and res.get("s") == "ok" and 'd' in res:
                    data_map = {item['n']: item['v'] for item in res['d']}
                    for sym in all_symbols:
                        clean_symbol = sym.replace("NSE:", "").replace("-EQ", "")
                        data = data_map.get(sym, {})

                        ltp = data.get('lp', 0) or data.get('ltp', 0)
                        volume = data.get('volume', 0)

                        if not ltp or not volume:
                            ltp = prev_ltp.get(clean_symbol, random.randint(500, 1500))
                            volume = prev_volume.get(clean_symbol, random.randint(10000, 50000))

                        prev_vol = prev_volume.get(clean_symbol)
                        delta = max(0, volume - prev_vol) if prev_vol is not None else 0
                        prev_volume[clean_symbol] = volume

                        prev_price = prev_ltp.get(clean_symbol)
                        buy_vol, sell_vol = 0, 0
                        if delta > 0 and prev_price is not None:
                            if ltp > prev_price: buy_vol = delta
                            elif ltp < prev_price: sell_vol = delta
                        prev_ltp[clean_symbol] = ltp

                        latest_data[clean_symbol] = {
                            "Timestamp": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
                            "Symbol": clean_symbol,
                            "CumulativeVolume": volume,
                            "Quantity": delta,
                            "LTP": ltp,
                            "BuyVolume": buy_vol,
                            "SellVolume": sell_vol,
                            "Mode": "live" if fyers else "dummy"
                        }
                        cache_expiry[clean_symbol] = now_ts

                    print(f"✅ Updated {len(all_symbols)} symbols at {datetime.now(IST).strftime('%H:%M:%S')}")
                else:
                    print("⚠️ API unavailable, skipping cycle")

            except Exception as e:
                print("⚠️ Exception inside loop:", e)

        else:
            print(f"⏸ Paused at {now.strftime('%H:%M:%S')} (outside trading window)")

        time.sleep(interval)  # ✅ wait full interval between fetches

# ------------------ Force Fetch ------------------
def force_fetch(symbol: str):
    clean_symbol = symbol.upper()
    now = time.time()
    ltp, volume = None, None
    mode = "live"

    try:
        sym_code = f"NSE:{clean_symbol}-EQ"
        ACCESS_TOKEN = get_access_token()
        fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN)
        res = fyers.quotes({"symbols": sym_code})
        if res.get("s") == "ok" and "d" in res:
            item = res["d"][0]
            data = item["v"]
            ltp = data.get("lp", 0) or data.get("ltp", 0)
            volume = data.get("volume", 0)
    except Exception:
        ltp, volume, mode = None, None, "dummy"

    if not ltp or not volume:
        ltp = random.randint(500, 1500)
        volume = random.randint(10000, 50000)
        mode = "dummy"

    latest_data[clean_symbol] = {
        "Timestamp": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
        "Symbol": clean_symbol,
        "CumulativeVolume": volume,
        "Quantity": 0,
        "LTP": ltp,
        "BuyVolume": 0,
        "SellVolume": 0,
        "Mode": mode
    }
    cache_expiry[clean_symbol] = now
    return latest_data[clean_symbol]

# ------------------ API Endpoints ------------------
@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "API is running. Try /quotes or /quotes/RELIANCE"
    }

@app.get("/quotes/{symbol}")
def get_symbol(symbol: str):
    if not is_market_open():
        return {"status": "closed", "message": "Market closed. Data unavailable outside 09:14–15:31"}

    now = time.time()
    if symbol in latest_data and (now - cache_expiry.get(symbol, 0)) < 5:
        return latest_data[symbol]
    return force_fetch(symbol)

@app.get("/quotes")
def get_multiple(symbol_list: str = ""):
    if not is_market_open():
        return {"status": "closed", "message": "Market closed. Data unavailable outside 09:14–15:31"}

    resp, now = {}, time.time()
    symbols_req = symbol_list.split(",") if symbol_list else [s.replace("NSE:", "").replace("-EQ", "") for s in all_symbols]

    for sym in symbols_req:
        if sym in latest_data and (now - cache_expiry.get(sym, 0)) < 5:
            resp[sym] = latest_data[sym]
        else:
            resp[sym] = force_fetch(sym)
    return resp

# ------------------ Start Worker ------------------
if __name__ == "__main__":
    t = threading.Thread(target=track_all, args=(300,), daemon=True)  # 5 minutes
    t.start()
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
