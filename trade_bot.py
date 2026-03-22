from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
import yfinance as yf


# ==============================
# たいき専用 トレードBOT 完全版
# 候補抽出 + 判定 + LINE通知 + エントリー自動化
# ==============================

# ---------- LINE設定 ----------
# 必ず自分の値に入れ替える
LINE_CHANNEL_ACCESS_TOKEN = "H9pAb5DvQAc7PVFombicOwBeIsrmrr16TUhyLglsHVdpGXPx5Jh5RbqtTZZua2IqRd/SXFyXBWsenlhlSm59KxPDSTJ+tJ4HTGh2/+g+0OGv2xNyQhi7OWs5HglNp9rYrUVyfN23SXYb73TtdyOfGAdB04t89/1O/w1cDnyilFU="
LINE_USER_ID = "U8c68daef32fba3b5ac4610f1693adf86"

# ---------- 設定 ----------
CONFIG = {
    "min_volume": 1_000_000,         # 最低出来高
    "max_candidates": 10,            # 最大候補数
    "min_score_to_notify": 60,       # 通知スコア
    "pullback_ma_tolerance": 0.01,   # 25日線接触許容（1%）
    "max_distance_from_ma25": 6.0,   # 25日線からの乖離許容（%）
    "breakout_buffer": 0.002,        # ブレイク用に高値の少し上
    "take_profit_pct": 0.03,         # 利確目安 3%
    "stop_buffer": 0.995,            # 損切りは直近安値の少し下
    "candidate_symbols": [
        "7203.T", "6758.T", "9984.T", "8306.T", "7974.T",
        "8035.T", "9432.T", "4063.T", "6954.T", "6861.T",
        "6501.T", "4578.T", "5401.T", "9101.T", "9104.T",
        "9501.T", "4755.T", "7201.T", "7733.T", "2802.T",
        "9983.T", "7267.T", "7011.T", "8058.T", "2914.T",
    ],
}


# ---------- 共通 ----------
def log(message: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}")


def safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def sma(values: List[float], period: int) -> List[Optional[float]]:
    result: List[Optional[float]] = []
    for i in range(len(values)):
        if i + 1 < period:
            result.append(None)
        else:
            window = values[i + 1 - period : i + 1]
            result.append(sum(window) / period)
    return result


# ---------- データ構造 ----------
@dataclass
class PriceBar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class NewsItem:
    title: str
    source: str
    published_at: str
    summary: str
    url: str
    sentiment: str = "neutral"
    impact: str = "medium"


@dataclass
class SymbolSnapshot:
    symbol: str
    name: str
    current_price: float
    prev_close: float
    volume: int
    price_change_pct: float
    bars: List[PriceBar]
    news: List[NewsItem]


@dataclass
class RuleResult:
    symbol: str
    passed: bool
    score: int
    reasons_positive: List[str]
    reasons_negative: List[str]
    entry_idea: str
    stop_idea: str
    take_profit_idea: str
    verdict: str


# ---------- 候補抽出 ----------
class MarketDataClient:
    def get_top_movers(self) -> List[Dict[str, Any]]:
        data: List[Dict[str, Any]] = []

        for symbol in CONFIG["candidate_symbols"]:
            try:
                stock = yf.Ticker(symbol)
                hist = stock.history(period="2d", auto_adjust=False)

                if hist.empty or len(hist) < 2:
                    continue

                prev_close = float(hist["Close"].iloc[-2])
                current_price = float(hist["Close"].iloc[-1])
                volume = int(hist["Volume"].iloc[-1])

                change_pct = safe_div((current_price - prev_close), prev_close) * 100

                if volume >= CONFIG["min_volume"] and change_pct > 0:
                    data.append(
                        {
                            "symbol": symbol,
                            "name": symbol,
                            "change_pct": change_pct,
                            "volume": volume,
                        }
                    )
            except Exception as e:
                log(f"候補取得スキップ: {symbol} / {e}")

        data.sort(key=lambda x: x["change_pct"], reverse=True)
        top = data[: CONFIG["max_candidates"]]
        return [{"symbol": x["symbol"], "name": x["name"]} for x in top]


# ---------- ニュース ----------
class NewsClient:
    def get_news_for_symbol(self, symbol: str) -> List[NewsItem]:
        # 今はダミー
        return []


# ---------- 相場データ取得 ----------
def get_stock_snapshot(ticker: str) -> Optional[Dict[str, Any]]:
    stock = yf.Ticker(ticker)
    hist = stock.history(period="5d", auto_adjust=False)

    if hist.empty or len(hist) < 2:
        return None

    latest = hist.iloc[-1]
    prev = hist.iloc[-2]

    return {
        "current_price": float(latest["Close"]),
        "prev_close": float(prev["Close"]),
        "high": float(latest["High"]),
        "low": float(latest["Low"]),
        "volume": int(latest["Volume"]),
    }


def get_real_bars(ticker: str, period: str = "6mo") -> List[PriceBar]:
    stock = yf.Ticker(ticker)
    hist = stock.history(period=period, auto_adjust=False)

    if hist.empty:
        return []

    bars: List[PriceBar] = []
    for date, row in hist.iterrows():
        bars.append(
            PriceBar(
                date=str(date.date()),
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=int(row["Volume"]),
            )
        )
    return bars


# ---------- 判定ロジック ----------
class RuleEngine:
    def evaluate(self, snapshot: SymbolSnapshot) -> RuleResult:
        closes = [b.close for b in snapshot.bars]
        highs = [b.high for b in snapshot.bars]
        lows = [b.low for b in snapshot.bars]
        opens = [b.open for b in snapshot.bars]

        if len(closes) < 30:
            return RuleResult(
                symbol=snapshot.symbol,
                passed=False,
                score=0,
                reasons_positive=[],
                reasons_negative=["ローソク足データ不足"],
                entry_idea="データ不足のため判定保留",
                stop_idea="判定保留",
                take_profit_idea="判定保留",
                verdict="保留",
            )

        ma25 = sma(closes, 25)
        ma25_now = ma25[-1]
        ma25_prev = ma25[-2] if len(ma25) >= 2 else None

        latest_close = closes[-1]
        latest_open = opens[-1]
        latest_high = highs[-1]
        latest_low = lows[-1]

        score = 0
        positives: List[str] = []
        negatives: List[str] = []

        # 1. 出来高
        if snapshot.volume >= CONFIG["min_volume"]:
            score += 20
            positives.append(f"出来高が基準以上 ({snapshot.volume:,}株)")
        else:
            negatives.append(f"出来高不足 ({snapshot.volume:,}株)")

        # 2. 前日比
        if snapshot.price_change_pct > 0:
            score += 15
            positives.append(f"前日比プラス ({snapshot.price_change_pct:.2f}%)")
        else:
            negatives.append(f"前日比マイナス ({snapshot.price_change_pct:.2f}%)")

        # 3. 株価が25日線の上
        if ma25_now is not None and latest_close > ma25_now:
            score += 15
            positives.append("株価が25日線の上")
        else:
            negatives.append("株価が25日線より弱い")

        # 4. 25日線の向き
        if ma25_now is not None and ma25_prev is not None and ma25_now > ma25_prev:
            score += 10
            positives.append("25日線が上向き")
        else:
            negatives.append("25日線の勢いが弱い")

        # 5. 高値・安値切り上げ
        if len(highs) >= 5 and len(lows) >= 5:
            recent_highs = highs[-5:]
            recent_lows = lows[-5:]
            if (
                recent_highs[-1] >= max(recent_highs[:-1])
                and recent_lows[-1] >= min(recent_lows[:-1])
            ):
                score += 15
                positives.append("直近で高値・安値の切り上げ傾向")
            else:
                negatives.append("トレンドが少し荒い")
        else:
            negatives.append("直近トレンド判定用データ不足")

        # 6. 25日線からの距離
        distance_to_ma25 = None
        if ma25_now is not None:
            distance_to_ma25 = safe_div((latest_close - ma25_now), ma25_now) * 100
            if latest_close > ma25_now:
                if 0 <= distance_to_ma25 <= CONFIG["max_distance_from_ma25"]:
                    score += 10
                    positives.append(f"25日線からの乖離が適正 ({distance_to_ma25:.2f}%)")
                elif distance_to_ma25 > CONFIG["max_distance_from_ma25"]:
                    negatives.append(f"25日線から離れすぎ ({distance_to_ma25:.2f}%)")
                else:
                    negatives.append(f"25日線を割り気味 ({distance_to_ma25:.2f}%)")
            else:
                negatives.append(f"25日線より下なので押し目候補ではない ({distance_to_ma25:.2f}%)")
        else:
            negatives.append("25日線が計算できない")

        # 7. 陽線
        is_bullish_candle = latest_close > latest_open
        if is_bullish_candle:
            score += 10
            positives.append("当日が陽線")
        else:
            negatives.append("当日が陰線")

        # 8. 押し目反発
        is_pullback_ready = False
        if ma25_now is not None:
            touched_ma25 = latest_low <= ma25_now * (1 + CONFIG["pullback_ma_tolerance"])
            bounced = latest_close > latest_open and latest_close > latest_low
            recovered_above_ma25 = latest_close > ma25_now

            if touched_ma25 and bounced and recovered_above_ma25:
                score += 20
                positives.append("押し目からの反発確認（エントリー候補）")
                is_pullback_ready = True
            else:
                negatives.append("押し目反発がまだ弱い")
        else:
            negatives.append("押し目判定不可")

        # 9. 直近戻し
        if len(closes) >= 2 and closes[-1] > closes[-2]:
            score += 10
            positives.append("直近で買い戻しが入っている")
        else:
            negatives.append("直近の戻しが弱い")

        # 10. ニュース
        if snapshot.news:
            positive_news_count = sum(1 for n in snapshot.news if n.sentiment == "positive")
            high_impact_count = sum(1 for n in snapshot.news if n.impact == "high")
            score += positive_news_count * 5
            score += high_impact_count * 5
            positives.append(f"材料ニュースあり ({len(snapshot.news)}件)")
        else:
            negatives.append("材料ニュースは目立たない")

        passed = score >= CONFIG["min_score_to_notify"]
        if not is_pullback_ready:
            negatives.append("押し目未完成（早めの監視候補）")

        # ---------- エントリー自動化 ----------
        recent_high = max(highs[-5:])
        recent_low = min(lows[-5:])

        break_entry = round(recent_high * (1 + CONFIG["breakout_buffer"]), 2)
        pullback_entry = round(ma25_now, 2) if ma25_now is not None else None
        stop_loss = round(recent_low * CONFIG["stop_buffer"], 2)
        take_profit = round(break_entry * (1 + CONFIG["take_profit_pct"]), 2)

        entry_idea = (
            "【エントリー】\n"
            f"・ブレイク：{break_entry}円\n"
            f"・押し目：{pullback_entry}円\n"
        )
        stop_idea = f"{stop_loss}円（直近安値割れ）"
        take_profit_idea = f"{take_profit}円 or トレンド継続"
        verdict = "監視強" if passed else "様子見"

        return RuleResult(
            symbol=snapshot.symbol,
            passed=passed,
            score=score,
            reasons_positive=positives,
            reasons_negative=negatives,
            entry_idea=entry_idea,
            stop_idea=stop_idea,
            take_profit_idea=take_profit_idea,
            verdict=verdict,
        )


# ---------- レポート整形 ----------
class ReportFormatter:
    def format_line_message(self, snapshot: SymbolSnapshot, result: RuleResult) -> str:
        positives = "\n".join([f"・{x}" for x in result.reasons_positive]) if result.reasons_positive else "・なし"
        negatives = "\n".join([f"・{x}" for x in result.reasons_negative]) if result.reasons_negative else "・なし"

        return (
            f"【トレードBOT通知】\n"
            f"銘柄: {snapshot.symbol} / {snapshot.name}\n"
            f"現在値: {snapshot.current_price:.2f} 円\n"
            f"前日比: {snapshot.price_change_pct:.2f}%\n"
            f"出来高: {snapshot.volume:,} 株\n"
            f"スコア: {result.score}\n"
            f"判定: {result.verdict}\n\n"
            f"■プラス材料\n{positives}\n\n"
            f"■マイナス材料\n{negatives}\n\n"
            f"■エントリー案\n{result.entry_idea}\n"
            f"■損切り案\n{result.stop_idea}\n"
            f"■利確案\n{result.take_profit_idea}"
        )

    def format_log_json(self, snapshot: SymbolSnapshot, result: RuleResult) -> Dict[str, Any]:
        return {
            "timestamp": datetime.now().isoformat(),
            "snapshot": {
                "symbol": snapshot.symbol,
                "name": snapshot.name,
                "current_price": snapshot.current_price,
                "prev_close": snapshot.prev_close,
                "volume": snapshot.volume,
                "price_change_pct": snapshot.price_change_pct,
                "news_count": len(snapshot.news),
            },
            "rule_result": asdict(result),
        }


# ---------- LINE通知 ----------
class LineNotifier:
    PUSH_URL = "https://api.line.me/v2/bot/message/push"

    def __init__(self, channel_access_token: str, user_id: str):
        self.channel_access_token = channel_access_token
        self.user_id = user_id

    def send_text(self, text: str) -> None:
        if (
            not self.channel_access_token
            or not self.user_id
            or "YOUR_LINE_CHANNEL_ACCESS_TOKEN" in self.channel_access_token
            or "YOUR_LINE_USER_ID" in self.user_id
        ):
            log("LINE設定未入力のため、通知をスキップ")
            print("\n===== LINE送信プレビュー =====")
            print(text)
            print("============================\n")
            return

        headers = {
            "Authorization": f"Bearer {self.channel_access_token}",
            "Content-Type": "application/json",
        }
        body = {
            "to": self.user_id,
            "messages": [{"type": "text", "text": text[:5000]}],
        }
        response = requests.post(self.PUSH_URL, headers=headers, json=body, timeout=20)
        response.raise_for_status()
        log("LINE通知送信完了")


# ---------- 保存 ----------
class LocalStorage:
    def __init__(self, base_dir: str = "logs"):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def save_result(self, symbol: str, payload: Dict[str, Any]) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self.base_dir, f"{timestamp}_{symbol.replace('.', '_')}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return path


# ---------- BOT本体 ----------
class TradeBot:
    def __init__(self):
        self.market_client = MarketDataClient()
        self.news_client = NewsClient()
        self.rule_engine = RuleEngine()
        self.formatter = ReportFormatter()
        self.notifier = LineNotifier(
            channel_access_token=LINE_CHANNEL_ACCESS_TOKEN,
            user_id=LINE_USER_ID,
        )
        self.storage = LocalStorage()

    def build_snapshot(self, item: Dict[str, Any]) -> SymbolSnapshot:
        symbol = item["symbol"]

        real = get_stock_snapshot(symbol)
        if real is None:
            raise ValueError(f"価格取得失敗: {symbol}")

        bars = get_real_bars(symbol)
        if not bars:
            raise ValueError(f"ローソク足取得失敗: {symbol}")

        news = self.news_client.get_news_for_symbol(symbol=symbol)

        current_price = real["current_price"]
        prev_close = real["prev_close"]
        price_change_pct = safe_div((current_price - prev_close), prev_close) * 100

        return SymbolSnapshot(
            symbol=symbol,
            name=item["name"],
            current_price=current_price,
            prev_close=prev_close,
            volume=real["volume"],
            price_change_pct=price_change_pct,
            bars=bars,
            news=news,
        )

    def run_once(self) -> None:
        log("BOT開始")
        universe = self.market_client.get_top_movers()
        log(f"候補数: {len(universe)}")

        notify_count = 0

        for item in universe[: CONFIG["max_candidates"]]:
            try:
                snapshot = self.build_snapshot(item)
                result = self.rule_engine.evaluate(snapshot)
                payload = self.formatter.format_log_json(snapshot, result)

                saved_path = self.storage.save_result(snapshot.symbol, payload)
                log(f"保存: {saved_path}")

                if result.passed:
                    message = self.formatter.format_line_message(snapshot, result)
                    self.notifier.send_text(message)
                    notify_count += 1

                    log(
                        f"\n通知送信: {snapshot.symbol}\n"
                        f"score: {result.score}\n"
                        f"entry:\n{result.entry_idea}\n"
                        f"stop: {result.stop_idea}\n"
                        f"take: {result.take_profit_idea}\n"
                        f"+ {result.reasons_positive}\n"
                        f"- {result.reasons_negative}\n"
                    )
                else:
                    log(
                        f"\n通知見送り: {snapshot.symbol}\n"
                        f"score: {result.score}\n"
                        f"+ {result.reasons_positive}\n"
                        f"- {result.reasons_negative}\n"
                    )

            except Exception as e:
                log(f"エラー: {item.get('symbol', 'UNKNOWN')} / {e}")

        log(f"BOT終了 / 通知数: {notify_count}")


# ---------- 実行 ----------
def main() -> None:
    bot = TradeBot()
    bot.run_once()


if __name__ == "__main__":
    main()
