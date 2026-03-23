import os
import json
from datetime import datetime, timedelta
import requests
import yfinance as yf

LOG_DIR = "logs"

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.getenv("LINE_USER_ID", "")


def load_logs():
    logs = []
    if not os.path.exists(LOG_DIR):
        return logs

    for file in os.listdir(LOG_DIR):
        if file.endswith(".json"):
            path = os.path.join(LOG_DIR, file)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    data["_file"] = file
                    logs.append(data)
            except Exception as e:
                print(f"読み込みエラー: {file} / {e}")
    return logs


def get_future_prices(symbol, base_date, days=(1, 3, 5)):
    try:
        stock = yf.Ticker(symbol)

        start = base_date - timedelta(days=2)
        end = base_date + timedelta(days=14)

        hist = stock.history(start=start, end=end, auto_adjust=False)

        if hist.empty:
            return {}

        if getattr(hist.index, "tz", None) is not None:
            hist.index = hist.index.tz_localize(None)

        result = {}

        for d in days:
            target_date = base_date + timedelta(days=d)
            candidates = hist.index[hist.index.date >= target_date]

            if len(candidates) > 0:
                result[d] = float(hist.loc[candidates[0]]["Close"])
            else:
                result[d] = None

        return result

    except Exception as e:
        print(f"価格取得エラー: {symbol} / {e}")
        return {}


def is_new_style_log(rule_result):
    return "entry_price" in rule_result


def score_bucket(score):
    if 50 <= score <= 59:
        return "50-59"
    elif 60 <= score <= 69:
        return "60-69"
    elif score >= 70:
        return "70+"
    else:
        return "49以下"


def send_line_message(text: str):
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID:
        print("LINE設定が無いので送信スキップ")
        print(text)
        return

    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    body = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": text[:5000]}],
    }

    response = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers=headers,
        json=body,
        timeout=20,
    )
    response.raise_for_status()
    print("LINE分析通知送信完了")


def build_report(results, total_files, new_style_count, old_style_count, analyzed_count, skipped_count):
    lines = []
    lines.append("【トレードBOT分析レポート】")
    lines.append(f"全ログ数: {total_files}")
    lines.append(f"新形式ログ: {new_style_count}")
    lines.append(f"旧形式ログ: {old_style_count}")
    lines.append(f"分析対象: {analyzed_count}")
    lines.append(f"スキップ数: {skipped_count}")
    lines.append("")

    if not results:
        lines.append("分析できる新形式の通知ログがまだありません")
        return "\n".join(lines)

    valid_1d = [r["ret_1d"] for r in results if r["ret_1d"] is not None]
    valid_3d = [r["ret_3d"] for r in results if r["ret_3d"] is not None]
    valid_5d = [r["ret_5d"] for r in results if r["ret_5d"] is not None]

    win_1d = sum(1 for r in valid_1d if r > 0)
    lose_1d = sum(1 for r in valid_1d if r <= 0)
    total_1d = win_1d + lose_1d
    win_rate_1d = (win_1d / total_1d * 100) if total_1d > 0 else 0.0

    lines.append(f"1日後 勝ち: {win_1d} / 負け: {lose_1d}")
    lines.append(f"1日後 勝率: {win_rate_1d:.2f}%")

    if valid_1d:
        lines.append(f"平均1日後: {sum(valid_1d) / len(valid_1d):.2f}%")
    if valid_3d:
        lines.append(f"平均3日後: {sum(valid_3d) / len(valid_3d):.2f}%")
    if valid_5d:
        lines.append(f"平均5日後: {sum(valid_5d) / len(valid_5d):.2f}%")

    setups = {}
    for r in results:
        setups.setdefault(r["setup"], []).append(r)

    lines.append("")
    lines.append("■ setup別")
    for k, v in setups.items():
        rets = [x["ret_1d"] for x in v if x["ret_1d"] is not None]
        if not rets:
            lines.append(f"{k}: {len(v)}件 / データ不足")
            continue
        wr = sum(1 for x in rets if x > 0) / len(rets) * 100
        avg = sum(rets) / len(rets)
        lines.append(f"{k}: {len(v)}件 / 勝率 {wr:.2f}% / 平均 {avg:.2f}%")

    buckets = {}
    for r in results:
        buckets.setdefault(r["score_bucket"], []).append(r)

    lines.append("")
    lines.append("■ score帯別")
    for k in ["49以下", "50-59", "60-69", "70+"]:
        v = buckets.get(k, [])
        if not v:
            continue
        rets = [x["ret_1d"] for x in v if x["ret_1d"] is not None]
        if not rets:
            lines.append(f"{k}: {len(v)}件 / データ不足")
            continue
        wr = sum(1 for x in rets if x > 0) / len(rets) * 100
        avg = sum(rets) / len(rets)
        lines.append(f"{k}: {len(v)}件 / 勝率 {wr:.2f}% / 平均 {avg:.2f}%")

    top = sorted(
        [r for r in results if r["ret_1d"] is not None],
        key=lambda x: x["ret_1d"],
        reverse=True,
    )[:3]

    if top:
        lines.append("")
        lines.append("■ 直近の上位")
        for r in top:
            lines.append(
                f'{r["symbol"]} {r["setup"]} score={r["score"]} 1d={r["ret_1d"]:.2f}%'
            )

    return "\n".join(lines)


def analyze():
    logs = load_logs()

    if not logs:
        report = "【トレードBOT分析レポート】\nログがありません"
        print(report)
        send_line_message(report)
        return

    total_files = len(logs)
    new_style_count = 0
    old_style_count = 0
    analyzed_count = 0
    skipped_count = 0

    results = []

    for log in logs:
        try:
            rule = log.get("rule_result", {})
            snap = log.get("snapshot", {})

            symbol = snap.get("symbol")
            if not symbol:
                skipped_count += 1
                continue

            if not is_new_style_log(rule):
                old_style_count += 1
                skipped_count += 1
                continue

            new_style_count += 1

            passed = rule.get("passed", False)
            if not passed:
                skipped_count += 1
                continue

            entry = rule.get("entry_price")
            stop = rule.get("stop_price")
            take = rule.get("take_profit_price")
            score = rule.get("score", 0)
            setup = rule.get("setup_type", "unknown")
            rr = rule.get("rr", 0.0)

            if entry is None or entry <= 0:
                skipped_count += 1
                continue

            timestamp = log.get("timestamp")
            if not timestamp:
                skipped_count += 1
                continue

            base_date = datetime.fromisoformat(timestamp).date()
            future = get_future_prices(symbol, base_date)

            if not future:
                skipped_count += 1
                continue

            ret_1d = None
            ret_3d = None
            ret_5d = None

            if future.get(1) is not None:
                ret_1d = (future[1] - entry) / entry * 100
            if future.get(3) is not None:
                ret_3d = (future[3] - entry) / entry * 100
            if future.get(5) is not None:
                ret_5d = (future[5] - entry) / entry * 100

            results.append(
                {
                    "symbol": symbol,
                    "setup": setup,
                    "score": score,
                    "score_bucket": score_bucket(score),
                    "rr": rr,
                    "entry": entry,
                    "stop": stop,
                    "take": take,
                    "ret_1d": ret_1d,
                    "ret_3d": ret_3d,
                    "ret_5d": ret_5d,
                    "file": log.get("_file", ""),
                }
            )
            analyzed_count += 1

        except Exception as e:
            print(f"ログ解析エラー: {log.get('_file', 'unknown')} / {e}")
            skipped_count += 1

    report = build_report(
        results,
        total_files,
        new_style_count,
        old_style_count,
        analyzed_count,
        skipped_count,
    )
    print(report)
    send_line_message(report)


if __name__ == "__main__":
    analyze()
