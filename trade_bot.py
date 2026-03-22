from __future__ import annotations

import os
import json
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests


# =====================================
# たいき専用 半自動トレードBOT 実装ひな形
# =====================================
# 目的:
# 1. 情報収集
# 2. スクリーニング
# 3. ルール判定
# 4. 通知
# 5. 自分が最終判断
#
# このファイルは「完成品」ではなく、実装の土台。
# APIキーやデータ取得元を差し替えれば、そのまま育てられる構成にしてある。
# =====================================


# ---------- 環境変数 ----------
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "H9pAb5DvQAc7PVFombicOwBeIsrmrr16TUhyLglsHVdpGXPx5Jh5RbqtTZZua2IqRd/SXFyXBWsenlhlSm59KxPDSTJ+tJ4HTGh2/+g+0OGv2xNyQhi7OWs5HglNp9rYrUVyfN23SXYb73TtdyOfGAdB04t89/1O/w1cDnyilFU=")
LINE_USER_ID = os.getenv("LINE_USER_ID", "U8c68daef32fba3b5ac4610f1693adf86")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MARKET_DATA_API_KEY = os.getenv("MARKET_DATA_API_KEY", "")
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")


# ---------- 設定 ----------
CONFIG = {
    "min_volume": 1_000_000,
    "max_candidates": 10,
    "min_score_to_notify": 70,
    "watchlist": [
        # 例: "7203.T", "9984.T", "6758.T"
    ],
    "market_prefix": "JP",
}


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


# ---------- 共通 ----------
def log(message: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}")


def safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def sma(values: List[float], window: int) -> List[Optional[float]]:
    result: List[Optional[float]] = []
    for i in range(len(values)):
        if i + 1 < window:
            result.append(None)
        else:
            result.append(sum(values[i + 1 - window:i + 1]) / window)
    return result


# ---------- データ取得層 ----------
class MarketDataClient:
    """
    実運用ではここを証券API / 市場データAPI / yfinance相当 / スクレイピング元に差し替える
    """

    def get_top_movers(self) -> List[Dict[str, Any]]:
        # TODO: 本番では実データ取得に差し替え
        # 例: 値上がり率ランキング、出来高ランキング、監視銘柄など
        mock = [
            {"symbol": "9999.T", "name": "サンプルA", "current_price": 1280, "prev_close": 1210, "volume": 1800000},
            {"symbol": "8888.T", "name": "サンプルB", "current_price": 920, "prev_close": 900, "volume": 800000},
            {"symbol": "7777.T", "name": "サンプルC", "current_price": 2450, "prev_close": 2320, "volume": 2300000},
        ]
        return mock

    def get_daily_bars(self, symbol: str, limit: int = 90) -> List[PriceBar]:
        # TODO: 本番では symbol ごとに取得
        closes = [1000, 1010, 1005, 1020, 1035, 1040, 1050, 1065, 1060, 1070,
                  1085, 1090, 1100, 1110, 1105, 1120, 1135, 1140, 1150, 1165,
                  1175, 1180, 1190, 1200, 1210, 1225, 1230, 1240, 1255, 1260,
                  1270, 1280, 1275, 1290, 1300, 1310, 1305, 1320, 1335, 1340]
        bars: List[PriceBar] = []
        base_date = 1
        for i, close in enumerate(closes[-limit:]):
            bars.append(
                PriceBar(
                    date=f"2026-03-{base_date + i:02d}",
                    open=close - 10,
                    high=close + 15,
                    low=close - 20,
                    close=close,
                    volume=1_200_000 + i * 10_000,
                )
            )
        return bars


class NewsClient:
    """
    実運用ではニュースAPI / RSS / 自前要約フローに接続
    すでに持ってる ニュース→AI→LINE BOT のニュース部分をここに差し込める
    """

    def get_news_for_symbol(self, symbol: str) -> List[NewsItem]:
        # TODO: 本番では RSS / API 取得 + 要約 + 感情分析
        mock_news = {
            "9999.T": [
                NewsItem(
                    title="サンプルA、業績見通しを上方修正",
                    source="MockNews",
                    published_at="2026-03-22T08:00:00",
                    summary="来期利益予想の上方修正が発表され、短期資金の流入が期待される。",
                    url="https://example.com/news/a",
                    sentiment="positive",
                    impact="high",
                )
            ],
            "7777.T": [
                NewsItem(
                    title="サンプルC、新規提携を発表",
                    source="MockNews",
                    published_at="2026-03-22T07:20:00",
                    summary="中長期では評価余地があるが、短期では材料出尽くしも警戒。",
                    url="https://example.com/news/c",
                    sentiment="mixed",
                    impact="medium",
                )
            ],
        }
        return mock_news.get(symbol, [])


# ---------- 判定ロジック層 ----------
class RuleEngine:
    """
    ユーザーのテンプレをベースに判定
    - 上昇してるやつだけ
    - 出来高100万以上
    - 押し目候補
    - 流れに逆らわない
    - 自分で最終判断するための材料を返す
    """

    def evaluate(self, snapshot: SymbolSnapshot) -> RuleResult:
        closes = [b.close for b in snapshot.bars]
        highs = [b.high for b in snapshot.bars]
        lows = [b.low for b in snapshot.bars]
        volumes = [b.volume for b in snapshot.bars]

        ma25 = sma(closes, 25)
        ma75 = sma(closes, 75)

        latest_close = closes[-1]
        latest_high = highs[-1]
        latest_low = lows[-1]
        latest_volume = volumes[-1]
        prev_close = closes[-2] if len(closes) >= 2 else closes[-1]

        score = 0
        positives: List[str] = []
        negatives: List[str] = []

        # 1. 出来高
        if snapshot.volume >= CONFIG["min_volume"]:
            score += 20
            positives.append(f"出来高が基準以上（{snapshot.volume:,}株）")
        else:
            negatives.append(f"出来高不足（{snapshot.volume:,}株）")

        # 2. 前日比上昇
        if snapshot.price_change_pct > 0:
            score += 15
            positives.append(f"前日比プラス（{snapshot.price_change_pct:.2f}%）")
        else:
            negatives.append(f"前日比マイナス（{snapshot.price_change_pct:.2f}%）")

        # 3. 25日線の上
        if ma25[-1] is not None and latest_close > ma25[-1]:
            score += 15
            positives.append("株価が25日線の上")
        else:
            negatives.append("株価が25日線より弱い")

        # 4. 25日線の向き
        if len(ma25) >= 2 and ma25[-1] is not None and ma25[-2] is not None and ma25[-1] > ma25[-2]:
            score += 10
            positives.append("25日線が上向き")
        else:
            negatives.append("25日線の勢いが弱い")

        # 5. 高値・安値切り上げ簡易判定
        recent_highs = highs[-5:]
        recent_lows = lows[-5:]
        if recent_highs[-1] >= max(recent_highs[:-1]) and recent_lows[-1] >= min(recent_lows[:-1]):
            score += 15
            positives.append("直近で高値・安値の切り上げ傾向")
        else:
            negatives.append("トレンドが少し荒い")

        # 6. 押し目余地
        if ma25[-1] is not None:
            distance_to_ma25 = safe_div((latest_close - ma25[-1]), ma25[-1]) * 100
            if 0 <= distance_to_ma25 <= 6:
                score += 10
                positives.append(f"25日線からの乖離が小さい（{distance_to_ma25:.2f}%）")
            elif distance_to_ma25 > 6:
                negatives.append(f"25日線から離れすぎ（{distance_to_ma25:.2f}%）")
            else:
                negatives.append(f"25日線を割り気味（{distance_to_ma25:.2f}%）")

        # 7. ニュース加点
        if snapshot.news:
            positive_news_count = sum(1 for n in snapshot.news if n.sentiment == "positive")
            high_impact_count = sum(1 for n in snapshot.news if n.impact == "high")
            score += positive_news_count * 5
            score += high_impact_count * 5
            positives.append(f"材料ニュースあり（{len(snapshot.news)}件）")
        else:
            negatives.append("材料ニュースは目立たない")

        passed = score >= CONFIG["min_score_to_notify"]

        entry_idea = (
            "25日線付近までの押し目待ち。下げ止まり→陽線確認で候補。"
            if passed else
            "今は飛びつき禁止。押し目か再度の形待ち。"
        )
        stop_idea = f"直近安値 {latest_low:.2f} 円の少し下を候補"
        take_profit_idea = f"直近高値 {latest_high:.2f} 円更新後の伸びを見る or 分割利確"
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


# ---------- 出力整形 ----------
class ReportFormatter:
    def format_line_message(self, snapshot: SymbolSnapshot, result: RuleResult) -> str:
        positive_text = "\n".join([f"・{x}" for x in result.reasons_positive]) or "・なし"
        negative_text = "\n".join([f"・{x}" for x in result.reasons_negative]) or "・なし"

        news_block = "\n".join(
            [f"・{n.title}｜{n.sentiment}/{n.impact}" for n in snapshot.news[:3]]
        ) or "・ニュースなし"

        message = f"""
【トレード候補通知】
銘柄: {snapshot.name} ({snapshot.symbol})
株価: {snapshot.current_price:.2f}
前日比: {snapshot.price_change_pct:.2f}%
出来高: {snapshot.volume:,}株
総合点: {result.score}点
判定: {result.verdict}

■良い点
{positive_text}

■気になる点
{negative_text}

■ニュース
{news_block}

■エントリー案
{result.entry_idea}

■損切り案
{result.stop_idea}

■利確案
{result.take_profit_idea}

※最終判断は自分。
※飛びつき禁止、形を待つ。
        """.strip()
        return message

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


# ---------- 通知層 ----------
class LineNotifier:
    PUSH_URL = "https://api.line.me/v2/bot/message/push"

    def __init__(self, channel_access_token: str, user_id: str):
        self.channel_access_token = channel_access_token
        self.user_id = user_id

    def send_text(self, text: str) -> None:
        if not self.channel_access_token or not self.user_id:
            log("LINE設定未入力のため、通知をスキップ")
            print("\n===== LINE送信プレビュー =====")
            print(text)
            print("==========================\n")
            return

        headers = {
            "Authorization": f"Bearer {self.channel_access_token}",
            "Content-Type": "application/json",
        }
        body = {
            "to": self.user_id,
            "messages": [
                {"type": "text", "text": text[:5000]}
            ],
        }
        response = requests.post(self.PUSH_URL, headers=headers, json=body, timeout=20)
        response.raise_for_status()
        log("LINE通知送信完了")


# ---------- 保存層 ----------
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
        bars = self.market_client.get_daily_bars(symbol=symbol)
        news = self.news_client.get_news_for_symbol(symbol=symbol)

        current_price = float(item["current_price"])
        prev_close = float(item["prev_close"])
        price_change_pct = safe_div((current_price - prev_close), prev_close) * 100

        return SymbolSnapshot(
            symbol=symbol,
            name=item["name"],
            current_price=current_price,
            prev_close=prev_close,
            volume=int(item["volume"]),
            price_change_pct=price_change_pct,
            bars=bars,
            news=news,
        )

    def run_once(self) -> None:
        log("BOT開始")
        universe = self.market_client.get_top_movers()
        log(f"候補数: {len(universe)}")

        notify_count = 0
        for item in universe[:CONFIG["max_candidates"]]:
            snapshot = self.build_snapshot(item)
            result = self.rule_engine.evaluate(snapshot)
            payload = self.formatter.format_log_json(snapshot, result)
            saved_path = self.storage.save_result(snapshot.symbol, payload)
            log(f"保存: {saved_path}")

            if result.passed:
                message = self.formatter.format_line_message(snapshot, result)
                self.notifier.send_text(message)
                notify_count += 1
            else:
                log(f"通知見送り: {snapshot.symbol} / score={result.score}")

        log(f"BOT終了 / 通知数: {notify_count}")


# ---------- 手動実行用 ----------
def main() -> None:
    bot = TradeBot()
    bot.run_once()


if __name__ == "__main__":
    main()
