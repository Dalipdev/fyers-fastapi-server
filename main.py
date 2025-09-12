import requests
import hashlib
import time
from fyers_apiv3 import fyersModel
from datetime import datetime, timedelta
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import threading
import os
import random

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

# ------------------ Market Hours Logic ------------------
def is_market_open():
    now = datetime.now()
    weekday = now.weekday()  # Mon=0 ... Sun=6
    if weekday >= 5:  # Sat/Sun
        return False
    market_open = now.replace(hour=9, minute=14, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=31, second=0, microsecond=0)
    return market_open <= now <= market_close

def sleep_until_market():
    now = datetime.now()
    weekday = now.weekday()
    if weekday >= 5:  # Sat/Sun → next Monday
        days_ahead = 7 - weekday
        next_open = (now + timedelta(days=days_ahead)).replace(hour=9, minute=14, second=0, microsecond=0)
    else:
        market_open_today = now.replace(hour=9, minute=14, second=0, microsecond=0)
        market_close_today = now.replace(hour=15, minute=31, second=0, microsecond=0)
        if now < market_open_today:  # before market
            next_open = market_open_today
        elif now > market_close_today:  # after market → next valid weekday
            next_day = now + timedelta(days=1)
            while next_day.weekday() >= 5:  # skip weekends
                next_day += timedelta(days=1)
            next_open = next_day.replace(hour=9, minute=14, second=0, microsecond=0)
        else:  # market is open → no sleep
            return
    sleep_secs = (next_open - now).total_seconds()
    print(f"⏸ Market closed. Sleeping until {next_open}")
    time.sleep(sleep_secs)

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

# ------------------ Worker ------------------
def track_all(interval=2):
    prev_volume, prev_ltp = {}, {}
    ACCESS_TOKEN = None
    fyers = None

    while True:
        # ---------------- Market Check ----------------
        if not is_market_open():
            sleep_until_market()
            fyers = None   # release API client
            ACCESS_TOKEN = None
            continue

        try:
            # Initialize Fyers client only when market is open
            if not fyers:
                try:
                    ACCESS_TOKEN = get_access_token()
                    fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN)
                except Exception as e:
                    print("⚠️ Token fetch failed → dummy mode:", e)
                    fyers = None
                    time.sleep(10)
                    continue

            symbols_to_track = active_symbols or set(all_symbols)
            if not symbols_to_track:
                time.sleep(1)
                continue

            # Check again mid-loop in case market closes during processing
            if not is_market_open():
                print("⏸ Market closed during update, sleeping...")
                sleep_until_market()
                fyers = None
                ACCESS_TOKEN = None
                continue

            res = fyers.quotes({"symbols": ",".join(symbols_to_track)}) if fyers else None

            # Token expired → refresh
            if res and res.get("code") == 401:
                print("⚠️ Unauthorized → refreshing token")
                ACCESS_TOKEN = get_access_token()
                fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN)
                continue

            now_time = time.time()
            for sym in symbols_to_track:
                data = {}
                if res and res.get("s") == "ok" and 'd' in res:
                    for item in res['d']:
                        if item['n'] == sym:
                            data = item['v']
                            break

                # Fallback dummy data
                ltp = data.get('lp', 0) or data.get('ltp', 0) or random.randint(500, 1500)
                volume = data.get('volume', 0) or random.randint(10000, 50000)
                delta = max(0, volume - prev_volume.get(sym, 0))
                prev_volume[sym] = volume
                prev_ltp[sym] = ltp

                latest_data[sym] = {
                    "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "Symbol": sym,
                    "CumulativeVolume": volume,
                    "Quantity": delta,
                    "LTP": ltp,
                    "Mode": "live" if fyers else "dummy"
                }
                cache_expiry[sym] = now_time

            print(f"✅ Updated {len(symbols_to_track)} symbols at {datetime.now().strftime('%H:%M:%S')}")

        except Exception as e:
            print("⚠️ Exception inside worker:", e)

        time.sleep(interval)

# ------------------ API Endpoints ------------------
@app.get("/quotes/{symbol}")
def get_symbol(symbol: str):
    symbol_code = f"NSE:{symbol}-EQ"
    active_symbols.add(symbol_code)
    now = time.time()
    if symbol_code in latest_data and (now - cache_expiry.get(symbol_code, 0)) < 5:
        return latest_data[symbol_code]
    return {"message": f"No data yet for {symbol}"}

@app.get("/quotes")
def get_multiple(symbol_list: str = ""):
    symbols_req = symbol_list.split(",") if symbol_list else [s.replace("NSE:", "") for s in all_symbols]
    resp, now = {}, time.time()
    for sym in symbols_req:
        symbol_code = f"NSE:{sym}-EQ"
        active_symbols.add(symbol_code)
        if symbol_code in latest_data and (now - cache_expiry.get(symbol_code, 0)) < 5:
            resp[sym] = latest_data[symbol_code]
        else:
            resp[sym] = {"message": f"No data yet for {sym}"}
    return resp

# ------------------ Start Background Worker ------------------
def start_worker():
    t = threading.Thread(target=track_all, daemon=True)
    t.start()

@app.on_event("startup")
def on_startup():
    start_worker()
