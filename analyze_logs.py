import os
import json
from datetime import datetime, timedelta
import yfinance as yf

LOG_DIR = "logs"


def load_logs():
    logs = []
    for file in os.listdir(LOG_DIR):
        if file.endswith(".json"):
            path = os.path.join(LOG_DIR, file)
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                logs.append(data)
    return logs


def get_future_prices(symbol, base_date, days=[1, 3, 5]):
    try:
        stock = yf.Ticker(symbol)
        hist = stock.history(period="10d")

        if hist.empty:
            return {}

        hist.index = hist.index.tz_localize(None)

        result = {}

        for d in days:
            target_date = base_date + timedelta(days=d)

            closest = hist.index[hist.index >= target_date]

            if len(closest) > 0:
                price = hist.loc[closest[0]]["Close"]
                result[d] = float(price)
            else:
                result[d] = None

        return result

    except Exception as e:
        print(f"価格取得エラー: {symbol} {e}")
        return {}


def analyze():
    logs = load_logs()

    total = 0
    win = 0
    lose = 0

    results = []

    for log in logs:
        try:
            r = log["rule_result"]

            if not r["passed"]:
                continue

            symbol = log["snapshot"]["symbol"]
            entry = r["entry_price"]
            stop = r["stop_price"]
            take = r["take_profit_price"]

            timestamp = log["timestamp"]
            base_date = datetime.fromisoformat(timestamp).date()

            future = get_future_prices(symbol, base_date)

            if not future or 1 not in future:
                continue

            total += 1

            day1_price = future[1]

            if day1_price is None:
                continue

            ret = (day1_price - entry) / entry * 100

            result = {
                "symbol": symbol,
                "setup": r["setup_type"],
                "score": r["score"],
                "rr": r["rr"],
                "ret_1d": ret
            }

            results.append(result)

            if ret > 0:
                win += 1
            else:
                lose += 1

        except Exception as e:
            print("ログ解析エラー:", e)

    if total == 0:
        print("データなし")
        return

    win_rate = win / total * 100

    print("======== 分析結果 ========")
    print(f"総トレード数: {total}")
    print(f"勝ち: {win} / 負け: {lose}")
    print(f"勝率: {win_rate:.2f}%")

    avg_return = sum(r["ret_1d"] for r in results) / len(results)
    print(f"平均1日後リターン: {avg_return:.2f}%")

    # setup別
    setups = {}
    for r in results:
        setups.setdefault(r["setup"], []).append(r)

    print("\n■ setup別")
    for k, v in setups.items():
        wr = sum(1 for x in v if x["ret_1d"] > 0) / len(v) * 100
        print(f"{k}: {len(v)}件 / 勝率 {wr:.2f}%")

    # score帯別
    print("\n■ score帯別")
    buckets = {"50-59": [], "60-69": [], "70+": []}

    for r in results:
        s = r["score"]
        if 50 <= s <= 59:
            buckets["50-59"].append(r)
        elif 60 <= s <= 69:
            buckets["60-69"].append(r)
        else:
            buckets["70+"].append(r)

    for k, v in buckets.items():
        if len(v) == 0:
            continue
        wr = sum(1 for x in v if x["ret_1d"] > 0) / len(v) * 100
        print(f"{k}: {len(v)}件 / 勝率 {wr:.2f}%")

    print("==========================")


if __name__ == "__main__":
    analyze()
