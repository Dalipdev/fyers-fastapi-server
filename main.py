import requests
import hashlib
import time
from fyers_apiv3 import fyersModel
from datetime import datetime, timedelta
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import threading
import os

# ------------------ Credentials ------------------
CLIENT_ID = os.getenv("CLIENT_ID")
SECRET_KEY = os.getenv("SECRET_KEY")
REFRESH_TOKEN = os.getenv("REFRESH_TOKEN")
PIN = os.getenv("PIN")

# ------------------ FastAPI ------------------
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------ Shared data ------------------
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

# ------------------ Market Hours (can bypass for testing) ------------------
def is_market_open():
    return True  # always open for testing

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
def track_all(interval=2):
    try:
        ACCESS_TOKEN = get_access_token()
        fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN)
        prev_volume, prev_ltp = {}, {}

        while True:
            try:
                if not active_symbols:
                    time.sleep(1)
                    continue

                res = fyers.quotes({"symbols": ",".join(active_symbols)})

                if res.get("code") == 401:
                    ACCESS_TOKEN = get_access_token()
                    fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN)
                    continue

                if res.get("s") == "ok" and 'd' in res:
                    now_ts = time.time()
                    for item in res['d']:
                        s, data = item['n'], item['v']
                        volume = data.get('volume', 0)
                        ltp = data.get('lp', 0) or data.get('ltp', 0)
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                        prev_vol = prev_volume.get(s)
                        delta = max(0, volume - prev_vol) if prev_vol is not None else 0
                        prev_volume[s] = volume

                        buy_vol, sell_vol = 0, 0
                        prev_price = prev_ltp.get(s)
                        if delta > 0 and prev_price is not None:
                            if ltp > prev_price:
                                buy_vol = delta
                            elif ltp < prev_price:
                                sell_vol = delta
                        prev_ltp[s] = ltp

                        latest_data[s] = {
                            "Timestamp": timestamp,
                            "Symbol": s,
                            "CumulativeVolume": volume,
                            "Quantity": delta,
                            "LTP": ltp,
                            "BuyVolume": buy_vol,
                            "SellVolume": sell_vol
                        }
                        cache_expiry[s] = now_ts
                    print(f"✅ Updated {len(res['d'])} symbols at {datetime.now().strftime('%H:%M:%S')}")
                else:
                    print("❌ Data error:", res)
            except Exception as e:
                print("⚠️ Exception inside loop:", e)
            time.sleep(interval)
    except Exception as e:
        print("❌ Worker startup error:", e)

# ------------------ API Endpoints ------------------
@app.get("/quotes/{symbol}")
def get_symbol(symbol: str):
    symbol_code = f"NSE:{symbol}-EQ"
    active_symbols.add(symbol_code)
    now = time.time()
    if symbol_code in latest_data and (now - cache_expiry.get(symbol_code, 0)) < 5:
        return latest_data[symbol_code]
    # Return mock data immediately
    return {
        "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Symbol": symbol_code,
        "CumulativeVolume": 0,
        "Quantity": 0,
        "LTP": 0,
        "BuyVolume": 0,
        "SellVolume": 0
    }

@app.get("/quotes")
def get_multiple(symbol_list: str):
    symbols_req = symbol_list.split(",")
    resp, now = {}, time.time()
    for sym in symbols_req:
        symbol_code = f"NSE:{sym}-EQ"
        active_symbols.add(symbol_code)
        if symbol_code in latest_data and (now - cache_expiry.get(symbol_code, 0)) < 5:
            resp[sym] = latest_data[symbol_code]
        else:
            resp[sym] = {
                "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Symbol": symbol_code,
                "CumulativeVolume": 0,
                "Quantity": 0,
                "LTP": 0,
                "BuyVolume": 0,
                "SellVolume": 0
            }
    return resp

# ------------------ Startup ------------------
@app.on_event("startup")
def start_worker():
    # Preload all symbols so first request works
    active_symbols.update(all_symbols)
    t = threading.Thread(target=track_all, daemon=True)
    t.start()
    print("🟢 Background worker started, all symbols preloaded")
