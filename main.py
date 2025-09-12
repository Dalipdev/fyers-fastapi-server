import os
import requests
import hashlib
import time
import random
from datetime import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import asyncio
from fyers_apiv3 import fyersModel

# ------------------ Credentials ------------------
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

# ------------------ Token Refresh ------------------
def get_appid_hash(client_id, secret_key):
    return hashlib.sha256(f"{client_id}:{secret_key}".encode()).hexdigest()

async def get_access_token():
    url = "https://api-t1.fyers.in/api/v3/validate-refresh-token"
    payload = {
        "grant_type": "refresh_token",
        "appIdHash": get_appid_hash(CLIENT_ID, SECRET_KEY),
        "refresh_token": REFRESH_TOKEN,
        "pin": PIN
    }
    headers = {"Content-Type": "application/json"}
    try:
        res = requests.post(url, json=payload, headers=headers).json()
        if res.get("s") == "ok" and "access_token" in res:
            print("✅ Token refreshed")
            return res["access_token"]
        else:
            print("⚠️ Token refresh failed:", res)
            return None
    except Exception as e:
        print("⚠️ Token fetch exception:", e)
        return None

# ------------------ Async Background Worker ------------------
async def track_all(interval=4):
    prev_volume, prev_ltp = {}, {}
    fyers = None
    ACCESS_TOKEN = None

    for sym in all_symbols:
        active_symbols.add(sym)

    while True:
        try:
            if not fyers:
                ACCESS_TOKEN = await get_access_token()
                if ACCESS_TOKEN:
                    fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN)

            if fyers:
                res = fyers.quotes({"symbols": ",".join(all_symbols)})
            else:
                res = None

            now = time.time()

            for sym in all_symbols:
                clean_symbol = sym.replace("NSE:", "").replace("-EQ", "")
                if res and res.get("s") == "ok" and "d" in res:
                    item = next((i for i in res["d"] if i["n"] == sym), {})
                    data = item.get("v", {})
                    ltp = data.get('lp') or data.get('ltp') or prev_ltp.get(clean_symbol, random.randint(500, 1500))
                    volume = data.get('volume') or prev_volume.get(clean_symbol, random.randint(10000, 50000))
                else:
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
                    "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "Symbol": clean_symbol,
                    "CumulativeVolume": volume,
                    "Quantity": delta,
                    "LTP": ltp,
                    "BuyVolume": buy_vol,
                    "SellVolume": sell_vol,
                    "Mode": "live" if fyers else "dummy"
                }
                cache_expiry[clean_symbol] = now

            await asyncio.sleep(interval)

        except Exception as e:
            print("⚠️ Exception in worker:", e)
            await asyncio.sleep(interval)

# ------------------ Force Fetch ------------------
async def force_fetch(symbol: str):
    clean_symbol = symbol.upper()
    now = time.time()
    ltp, volume = None, None
    mode = "live"

    try:
        ACCESS_TOKEN = await get_access_token()
        if ACCESS_TOKEN:
            fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN)
            res = fyers.quotes({"symbols": f"NSE:{clean_symbol}-EQ"})
            if res.get("s") == "ok" and "d" in res:
                item = res["d"][0]
                data = item.get("v", {})
                ltp = data.get("lp") or data.get("ltp")
                volume = data.get("volume")
    except Exception:
        mode = "dummy"

    if not ltp or not volume:
        ltp = random.randint(500, 1500)
        volume = random.randint(10000, 50000)
        mode = "dummy"

    latest_data[clean_symbol] = {
        "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
@app.get("/quotes/{symbol}")
async def get_symbol(symbol: str):
    now = time.time()
    if symbol in latest_data and (now - cache_expiry.get(symbol, 0)) < 5:
        return latest_data[symbol]
    return await force_fetch(symbol)

@app.get("/quotes")
async def get_multiple(symbol_list: str = ""):
    resp = {}
    now = time.time()
    symbols_req = symbol_list.split(",") if symbol_list else [s.replace("NSE:", "").replace("-EQ", "") for s in all_symbols]
    for sym in symbols_req:
        if sym in latest_data and (now - cache_expiry.get(sym, 0)) < 5:
            resp[sym] = latest_data[sym]
        else:
            resp[sym] = await force_fetch(sym)
    return resp

# ------------------ Start Worker on Startup ------------------
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(track_all(interval=4))
