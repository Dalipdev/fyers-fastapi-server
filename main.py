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
latest_data = {}        # {symbol: {...}}
prev_ltp = {}           # {symbol: last LTP}
prev_volume = {}        # {symbol: last cumulative volume}
lock = threading.Lock() # thread-safe

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

# ------------------ Fyers Token ------------------
def get_appid_hash(client_id, secret_key):
    return hashlib.sha256(f"{client_id}:{secret_key}".encode()).hexdigest()

def get_access_token():
    """Get Fyers access token with timeout & error handling"""
    try:
        url = "https://api-t1.fyers.in/api/v3/validate-refresh-token"
        payload = {
            "grant_type": "refresh_token",
            "appIdHash": get_appid_hash(CLIENT_ID, SECRET_KEY),
            "refresh_token": REFRESH_TOKEN,
            "pin": PIN
        }
        res = requests.post(url, json=payload, headers={"Content-Type":"application/json"}, timeout=5)
        res_json = res.json()
        if res_json.get("s") == "ok" and "access_token" in res_json:
            return res_json["access_token"]
    except Exception as e:
        print("⚠️ Token refresh failed:", e)
    return None

# ------------------ Initialize previous LTP & Volume ------------------
def initialize_prev_values():
    token = get_access_token()
    if not token:
        print("⚠️ Cannot initialize, token missing")
        return
    try:
        fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=token)
        res = fyers.quotes({"symbols": ",".join(all_symbols)})
        if res.get("s") == "ok" and 'd' in res:
            with lock:
                for item in res['d']:
                    sym = item['n'].replace("NSE:", "").replace("-EQ", "")
                    data = item['v']
                    prev_volume[sym] = data.get('volume', 0)
                    prev_ltp[sym] = data.get('lp', 0) or data.get('ltp', 0)
            print("✅ Initialized previous LTP & volume")
    except Exception as e:
        print("⚠️ Failed to initialize previous values:", e)

# ------------------ Background Thread to Track All Symbols ------------------
def track_all(interval=300):
    while True:
        token = get_access_token()
        if not token:
            print("⚠️ Cannot fetch token, skipping this cycle")
            time.sleep(interval)
            continue
        try:
            fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=token)
            res = fyers.quotes({"symbols": ",".join(all_symbols)})
            if res.get("s") == "ok" and 'd' in res:
                with lock:
                    for item in res['d']:
                        sym_full = item['n']
                        sym = sym_full.replace("NSE:", "").replace("-EQ", "")
                        data = item['v']
                        ltp = data.get('lp', 0) or data.get('ltp', 0)
                        volume = data.get('volume', 0)

                        prev_v = prev_volume.get(sym, volume)
                        prev_p = prev_ltp.get(sym, ltp)
                        delta_qty = max(volume - prev_v, 0)

                        buy_vol, sell_vol = 0, 0
                        if delta_qty > 0:
                            if ltp > prev_p:
                                buy_vol = delta_qty
                            elif ltp < prev_p:
                                sell_vol = delta_qty

                        # Update prev values
                        prev_volume[sym] = volume
                        prev_ltp[sym] = ltp

                        # Update latest data cache
                        latest_data[sym] = {
                            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "Symbol": sym,
                            "CumulativeVolume": volume,
                            "Quantity": delta_qty,
                            "LTP": ltp,
                            "BuyVolume": buy_vol,
                            "SellVolume": sell_vol,
                            "Mode": "live"
                        }
        except Exception as e:
            print("⚠️ Exception in track_all:", e)

        time.sleep(interval)

# ------------------ API Endpoints ------------------
@app.get("/")
def root():
    return {"status": "ok", "message": "API running. Try /quotes or /quotes/SBIN"}

@app.get("/ping")
def ping():
    return {"status": "alive", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

@app.get("/quotes/{symbol}")
def get_symbol(symbol: str):
    sym = symbol.upper()
    with lock:
        data = latest_data.get(sym)
    if data:
        return data
    return {"error": "Data not yet available for symbol"}

@app.get("/quotes")
def get_multiple(symbol_list: str = ""):
    symbols_req = symbol_list.split(",") if symbol_list else [s.replace("NSE:", "").replace("-EQ", "") for s in all_symbols]
    resp = {}
    with lock:
        for sym in symbols_req:
            if sym in latest_data:
                resp[sym] = latest_data[sym]
            else:
                resp[sym] = {"error": "Data not yet available"}
    return resp

# ------------------ Startup ------------------
@app.on_event("startup")
def start_worker():
    initialize_prev_values()
    t = threading.Thread(target=track_all, args=(60,), daemon=True)  # fetch every 1 min
    t.start()
