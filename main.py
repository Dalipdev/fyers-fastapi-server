import requests
import hashlib
import time
from fyers_apiv3 import fyersModel
from datetime import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import threading
import os

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
prev_ltp = {}
prev_volume = {}
lock = threading.Lock()

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

# ------------------ Initialize Prev Values ------------------
def initialize_prev_values():
    try:
        ACCESS_TOKEN = get_access_token()
        fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN)
        res = fyers.quotes({"symbols": ",".join(all_symbols)})
        if res.get("s") == "ok" and 'd' in res:
            with lock:
                for item in res['d']:
                    sym = item['n'].replace("NSE:", "").replace("-EQ", "")
                    data = item['v']
                    prev_volume[sym] = data.get('volume', 0)
                    prev_ltp[sym] = data.get('lp', 0) or data.get('ltp', 0)
            print("✅ Initialized prev_volume & prev_ltp")
    except Exception as e:
        print("⚠️ Failed to initialize previous values:", e)

# ------------------ Background Worker ------------------
def track_all(interval=300):
    ACCESS_TOKEN = None
    fyers = None

    while True:
        try:
            if not fyers:
                try:
                    ACCESS_TOKEN = get_access_token()
                    fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN)
                except Exception as e:
                    print("⚠️ Token fetch failed:", e)
                    fyers = None

            res = fyers.quotes({"symbols": ",".join(all_symbols)}) if fyers else None
            now_ts = time.time()

            if res and res.get("s") == "ok" and 'd' in res:
                data_map = {item['n']: item['v'] for item in res['d']}

                with lock:
                    for sym in all_symbols:
                        clean_symbol = sym.replace("NSE:", "").replace("-EQ", "")
                        data = data_map.get(sym, {})
                        ltp = data.get('lp', 0) or data.get('ltp', 0)
                        volume = data.get('volume', 0)

                        prev_v = prev_volume.get(clean_symbol, volume)
                        prev_p = prev_ltp.get(clean_symbol, ltp)

                        delta_qty = max(volume - prev_v, 0)

                        buy_vol, sell_vol = 0, 0
                        if delta_qty > 0:
                            if ltp > prev_p:
                                buy_vol = delta_qty
                            elif ltp < prev_p:
                                sell_vol = delta_qty

                        # update prev values always
                        prev_volume[clean_symbol] = volume
                        prev_ltp[clean_symbol] = ltp

                        latest_data[clean_symbol] = {
                            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "Symbol": clean_symbol,
                            "CumulativeVolume": volume,
                            "Quantity": delta_qty,
                            "LTP": ltp,
                            "BuyVolume": buy_vol,
                            "SellVolume": sell_vol,
                            "Mode": "live" if fyers else "offline"
                        }
                        cache_expiry[clean_symbol] = now_ts

                print(f"✅ Updated {len(all_symbols)} symbols at {datetime.now().strftime('%H:%M:%S')}")
            else:
                print("⚠️ API unavailable, skipping this cycle")

        except Exception as e:
            print("⚠️ Exception inside loop:", e)

        time.sleep(interval)

# ------------------ Force Fetch ------------------
def force_fetch(symbol: str):
    clean_symbol = symbol.upper()
    ACCESS_TOKEN = get_access_token()
    fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN)
    res = fyers.quotes({"symbols": f"NSE:{clean_symbol}-EQ"})

    now_ts = time.time()
    ltp, volume = 0, 0
    buy_vol = sell_vol = delta_qty = 0

    if res.get("s") == "ok" and "d" in res:
        data = res["d"][0]["v"]
        ltp = data.get("lp", 0) or data.get("ltp", 0)
        volume = data.get("volume", 0)

        with lock:
            prev_v = prev_volume.get(clean_symbol, volume)
            prev_p = prev_ltp.get(clean_symbol, ltp)

            delta_qty = max(volume - prev_v, 0)
            if delta_qty > 0:
                if ltp > prev_p:
                    buy_vol = delta_qty
                elif ltp < prev_p:
                    sell_vol = delta_qty

            # always update prev values
            prev_volume[clean_symbol] = volume
            prev_ltp[clean_symbol] = ltp

    latest_data[clean_symbol] = {
        "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Symbol": clean_symbol,
        "CumulativeVolume": volume,
        "Quantity": delta_qty,
        "LTP": ltp,
        "BuyVolume": buy_vol,
        "SellVolume": sell_vol,
        "Mode": "live"
    }
    cache_expiry[clean_symbol] = now_ts
    return latest_data[clean_symbol]

# ------------------ API Endpoints ------------------
@app.get("/")
def root():
    return {"status": "ok", "message": "API running. Try /quotes or /quotes/RELIANCE"}

@app.get("/ping")
def ping():
    return {"status": "alive", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

@app.get("/quotes/{symbol}")
def get_symbol(symbol: str):
    now = time.time()
    with lock:
        if symbol in latest_data and (now - cache_expiry.get(symbol, 0)) < 5:
            return latest_data[symbol]
    return force_fetch(symbol)

@app.get("/quotes")
def get_multiple(symbol_list: str = ""):
    resp, now = {}, time.time()
    symbols_req = symbol_list.split(",") if symbol_list else [s.replace("NSE:", "").replace("-EQ", "") for s in all_symbols]
    for sym in symbols_req:
        with lock:
            if sym in latest_data and (now - cache_expiry.get(sym, 0)) < 5:
                resp[sym] = latest_data[sym]
            else:
                resp[sym] = force_fetch(sym)
    return resp

# ------------------ Startup ------------------
@app.on_event("startup")
def start_worker():
    initialize_prev_values()
    t = threading.Thread(target=track_all, args=(300,), daemon=True)
    t.start()
