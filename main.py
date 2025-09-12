def track_all(interval=2):
    prev_volume, prev_ltp = {}, {}
    ACCESS_TOKEN = None
    fyers = None

    while True:
        # -------------------- Market Hours Check --------------------
        if not is_market_open():
            sleep_until_market()  # Sleep until next market open
            continue

        try:
            # -------------------- Token & Fyers Init --------------------
            if not fyers:
                try:
                    ACCESS_TOKEN = get_access_token()
                    fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN)
                except Exception as e:
                    print("⚠️ Token fetch failed:", e)
                    fyers = None
                    time.sleep(10)
                    continue

            # -------------------- Track Active Symbols --------------------
            symbols_to_track = active_symbols or set(all_symbols)
            if not symbols_to_track:
                time.sleep(1)
                continue

            res = fyers.quotes({"symbols": ",".join(symbols_to_track)}) if fyers else None

            # -------------------- Token Expiry Handling --------------------
            if res and res.get("code") == 401:
                print("⚠️ Unauthorized → refreshing token")
                ACCESS_TOKEN = get_access_token()
                fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN)
                continue

            # -------------------- Process Data --------------------
            if res and res.get("s") == "ok" and 'd' in res:
                now_time = time.time()
                for item in res['d']:
                    s, data = item['n'], item['v']
                    ltp = data.get('lp', 0) or data.get('ltp', 0)
                    volume = data.get('volume', 0)
                    delta = max(0, volume - prev_volume.get(s, 0))
                    prev_volume[s] = volume
                    prev_ltp[s] = ltp

                    # Update latest_data
                    latest_data[s] = {
                        "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "Symbol": s,
                        "CumulativeVolume": volume,
                        "Quantity": delta,
                        "LTP": ltp
                    }
                    cache_expiry[s] = now_time

                print(f"✅ Updated {len(res['d'])} symbols at {datetime.now().strftime('%H:%M:%S')}")
            else:
                print("⚠️ No data / dummy mode")

        except Exception as e:
            print("⚠️ Exception inside loop:", e)

        time.sleep(interval)
