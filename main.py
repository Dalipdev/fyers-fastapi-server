import requests
import hashlib
import time
from fyers_apiv3 import fyersModel
from datetime import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import threading
import os
import pytz  # timezone

# ------------------ Environment Variables ----------------
CLIENT_ID = os.getenv("CLIENT_ID")
SECRET_KEY = os.getenv("SECRET_KEY")
REFRESH_TOKEN = os.getenv("REFRESH_TOKEN")
PIN = os.getenv("PIN")

# ------------------ FastAPI Setup ----------------
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------ Shared Data ----------------
latest_data = {}
cache_expiry = {}
active_symbols = set()

# ------------------ Stock List ----------------
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

# ------------------ Timezone & Market Check ----------------
IST = pytz.timezone("Asia/Kolkata")

def is_market_open():
    now = datetime.now(IST)
    return (
        now.weekday() < 5 and
        (now.hour > 9 or (now.hour == 9 and now.minute >= 14)) and
        (now.hour < 15 or (now.hour == 15 and now.minute <= 31))
    )

# ------------------ Token Refresh ----------------
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

# ------------------ Background Worker ----------------
def track_all(interval=300):
    prev_volume, prev_ltp = {}, {}
    ACCESS_TOKEN = None
    fyers = None

    # Add all symbols to active set
    for sym in all_symbols:
        active_symbols.add(sym)

    while True:
        try:
            if not is_market_open():
                print("⏸ Market closed. Sleeping 5 min")
                time.sleep(interval)
                continue

            if not fyers:
                ACCESS_TOKEN = get_access_token()
                fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN)

            res = fyers.quotes({"symbols": ",".join(all_symbols)})
            now_ts = time.time()

            if res.get("s") == "ok" and 'd' in res:
                data_map = {item['n']: item['v'] for item in res['d']}
                for sym in all_symbols:
                    clean_symbol = sym.replace("NSE:", "").replace("-EQ", "")
                    data = data_map.get(sym, {})

                    ltp = data.get('lp', 0) or data.get('ltp', 0)
                    volume = data.get('volume', 0)

                    # Skip if no live data
                    if not ltp or not volume:
                        continue

                    delta = max(0, volume - prev_volume.get(clean_symbol, 0))
                    buy_vol, sell_vol = 0, 0
                    if delta > 0:
                        prev_price = prev_ltp.get(clean_symbol, ltp)
                        if ltp > prev_price: buy_vol = delta
                        elif ltp < prev_price: sell_vol = delta

                    prev_ltp[clean_symbol] = ltp
                    prev_volume[clean_symbol] = volume

                    latest_data[clean_symbol] = {
                        "Timestamp": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
                        "Symbol": clean_symbol,
                        "CumulativeVolume": volume,
                        "Quantity": delta,
                        "LTP": ltp,
                        "BuyVolume": buy_vol,
                        "SellVolume": sell_vol,
                        "Mode": "live"
                    }
                    cache_expiry[clean_symbol] = now_ts

                print(f"✅ Updated {len(all_symbols)} symbols at {datetime.now(IST).strftime('%H:%M:%S')}")
            else:
                print("⚠️ API returned error, skipping cycle")

        except Exception as e:
            print("⚠️ Exception inside loop:", e)

        # Sleep exactly 5 minutes
        time.sleep(interval)

# ------------------ Force Fetch ----------------
def force_fetch(symbol: str):
    clean_symbol = symbol.upper()
    now = time.time()
    ltp, volume = None, None

    try:
        sym_code = f"NSE:{clean_symbol}-EQ"
        ACCESS_TOKEN = get_access_token()
        fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN)
        res = fyers.quotes({"symbols": sym_code})
        if res.get("s") == "ok" and "d" in res:
            data = res['d'][0]['v']
            ltp = data.get('lp', 0) or data.get('ltp', 0)
            volume = data.get('volume', 0)
    except Exception as e:
        print(f"⚠️ Force fetch failed for {symbol}: {e}")
        return None

    if not ltp or not volume:
        return None

    latest_data[clean_symbol] = {
        "Timestamp": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
        "Symbol": clean_symbol,
        "CumulativeVolume": volume,
        "Quantity": 0,
        "LTP": ltp,
        "BuyVolume": 0,
        "SellVolume": 0,
        "Mode": "live"
    }
    cache_expiry[clean_symbol] = now
    return latest_data[clean_symbol]

# ------------------ API Endpoints ----------------
@app.get("/")
def root():
    return {"status": "ok", "message": "API is running. Try /quotes or /quotes/RELIANCE"}

@app.get("/quotes/{symbol}")
def get_symbol(symbol: str):
    now = time.time()
    if symbol in latest_data and (now - cache_expiry.get(symbol, 0)) < 5:
        return latest_data[symbol]
    return force_fetch(symbol)

@app.get("/quotes")
def get_multiple(symbol_list: str = ""):
    resp, now = {}, time.time()
    symbols_req = symbol_list.split(",") if symbol_list else [s.replace("NSE:", "").replace("-EQ", "") for s in all_symbols]
    for sym in symbols_req:
        if sym in latest_data and (now - cache_expiry.get(sym, 0)) < 5:
            resp[sym] = latest_data[sym]
        else:
            fetched = force_fetch(sym)
            if fetched:
                resp[sym] = fetched
    return resp

# ------------------ Start Worker on Startup ----------------
@app.on_event("startup")
def start_background_worker():
    t = threading.Thread(target=track_all, args=(300,), daemon=True)  # 5-minute interval
    t.start()
