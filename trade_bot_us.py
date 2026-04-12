from __future__ import annotations

import math
import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
import yfinance as yf


# ==============================
# たいき専用 トレードBOT 米国株版
# 候補抽出 + 判定 + ログ保存 + 事前シナリオ通知
# ※ 1株単位対応
# ※ ドル建て
# ※ GO通知は廃止
# ※ 前兆シグナル対応版
# ==============================

# ---------- LINE設定 ----------
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.getenv("LINE_USER_ID", "")

# ---------- 実行モード ----------
# trade       : 日中判定（LINE送信なし・ログ保存のみ）
# prescenario : 事前シナリオ通知
RUN_MODE = os.getenv("RUN_MODE", "trade").lower()

# ---------- 銘柄名 ----------
SYMBOL_NAME_MAP = {
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "NVIDIA",
    "TSLA": "Tesla",
    "AMZN": "Amazon",
    "GOOGL": "Alphabet",
    "META": "Meta",
    "AMD": "AMD",
    "NFLX": "Netflix",
    "INTC": "Intel",
    "PLTR": "Palantir",
    "SNOW": "Snowflake",
    "COIN": "Coinbase",
    "SHOP": "Shopify",
    "SQ": "Block",
    "UBER": "Uber",
    "DIS": "Disney",
    "BA": "Boeing",
    "JPM": "JPMorgan",
    "XOM": "Exxon Mobil",
    "CVX": "Chevron",
    "V": "Visa",
    "MA": "Mastercard",
}

# ---------- 設定 ----------
CONFIG = {
    "min_volume": 500_000,           # 最低出来高
    "max_candidates": 50,            # 最大候補数
    "min_score_to_notify": 45,       # 判定用スコア

    "pullback_ma_tolerance": 0.015,  # 25日線接触許容（1.5%）
    "max_distance_from_ma25": 8.0,   # 25日線からの乖離許容（%）
    "breakout_buffer": 0.003,        # ブレイク判定用
    "take_profit_pct": 0.04,         # 押し目利確目安
    "stop_buffer": 0.995,            # 損切りは直近安値の少し下

    # 資金管理（ドル建て）
    "account_size": 1000,            # 例: 1000ドル
    "risk_per_trade": 0.01,          # 1回の許容損失 1%
    "max_position_ratio": 0.30,      # 1銘柄に入れる最大資金比率
    "min_rr": 1.5,                   # 最低RR
    "max_stop_pct": 0.08,            # 損切り幅8%超は見送り

    # 事前シナリオ
    "prescenario_top_n": 5,
    "monitor_top_n": 5,
    "signal_top_n": 5,               # 前兆シグナル表示数

    # 夜仕込み用
    "night_breakout_buffer": 0.003,
    "night_pullback_buffer": 0.003,
    "night_rr_target": 2.2,

    "candidate_symbols": [
        "AAPL", "MSFT", "NVDA", "TSLA",
        "AMZN", "GOOGL", "META",
        "AMD", "NFLX", "INTC",
        "PLTR", "SNOW", "COIN",
        "SHOP", "SQ", "UBER",
        "DIS", "BA", "JPM",
        "XOM", "CVX", "V", "MA",
    ],
}


# ---------- 共通 ----------
def log(message: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}")


def safe_div(a: float, b: float) -> float:
    if a is None or b is None:
        return 0.0
    if isinstance(a, float) and math.isnan(a):
        return 0.0
    if isinstance(b, float) and math.isnan(b):
        return 0.0
    if b == 0:
        return 0.0
    return a / b


def sma(values: List[float], period: int) -> List[Optional[float]]:
    result: List[Optional[float]] = []
    for i in range(len(values)):
        if i + 1 < period:
            result.append(None)
            continue

        window = values[i + 1 - period: i + 1]

        if any(v is None for v in window):
            result.append(None)
            continue

        if any(isinstance(v, float) and math.isnan(v) for v in window):
            result.append(None)
            continue

        result.append(sum(window) / period)

    return result


def calc_rr(entry: float, stop: float, take: float) -> float:
    risk = entry - stop
    reward = take - entry
    if risk <= 0:
        return 0.0
    return reward / risk


def calc_position_size(entry: float, stop: float) -> Tuple[int, float]:
    risk_amount = CONFIG["account_size"] * CONFIG["risk_per_trade"]
    risk_per_share = entry - stop

    if risk_per_share <= 0:
        return 0, 0.0

    raw_size = risk_amount / risk_per_share
    max_size_by_cash = (CONFIG["account_size"] * CONFIG["max_position_ratio"]) / entry
    size = int(min(raw_size, max_size_by_cash))

    return max(size, 0), risk_amount


def pick_recent_high_low_from_bars(
    bars: List["PriceBar"], lookback: int = 10
) -> Tuple[float, float]:
    recent = bars[-lookback:] if len(bars) >= lookback else bars
    highs = [b.high for b in recent]
    lows = [b.low for b in recent]
    return max(highs), min(lows)


def calc_dynamic_score_from_snapshot(snapshot: "SymbolSnapshot") -> float:
    closes = [b.close for b in snapshot.bars]
    highs = [b.high for b in snapshot.bars]
    lows = [b.low for b in snapshot.bars]
    volumes = [b.volume for b in snapshot.bars]

    if len(closes) < 75 or len(volumes) < 20:
        return 0.0

    sma25_now = sma(closes, 25)[-1]
    sma75_now = sma(closes, 75)[-1]
    close = closes[-1]
    high = highs[-1]
    low = lows[-1]
    volume = volumes[-1]
    avg_volume20 = sum(volumes[-20:]) / 20

    volume_score = safe_div(volume, avg_volume20)
    range_pct = safe_div((high - low), close)

    trend_score = 0
    if sma25_now is not None and close > sma25_now:
        trend_score += 1
    if sma25_now is not None and sma75_now is not None and sma25_now > sma75_now:
        trend_score += 1

    dynamic_score = (
        volume_score * 0.5 +
        range_pct * 25 * 0.3 +
        trend_score * 0.2
    )
    return round(dynamic_score, 2)


def format_reason_lines(items: List[str], limit: int = 5) -> str:
    if not items:
        return "・なし"
    return "\n".join([f"・{x}" for x in items[:limit]])


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
    setup_type: str = "none"
    entry_price: float = 0.0
    stop_price: float = 0.0
    take_profit_price: float = 0.0
    rr: float = 0.0
    position_size: int = 0
    risk_amount: float = 0.0


@dataclass
class SignalHint:
    symbol: str
    name: str
    status: str
    hint_type: str
    trigger_text: str
    dynamic_score: float
    reasons_positive: List[str] = field(default_factory=list)
    reasons_negative: List[str] = field(default_factory=list)


@dataclass
class PreScenario:
    symbol: str
    name: str
    status: str
    scenario_type: str
    recent_high: float
    recent_low: float
    sma25: float
    sma75: float
    entry_condition: str
    invalid_condition: str
    comment: str
    dynamic_score: float
    volume_ratio: float
    range_pct: float

    order_type: str = "なし"
    entry_price: float = 0.0
    stop_price: float = 0.0
    take_profit_price: float = 0.0
    rr: float = 0.0
    position_size: int = 0
    risk_amount: float = 0.0
    order_ready: bool = False

    reasons_positive: List[str] = field(default_factory=list)
    reasons_negative: List[str] = field(default_factory=list)


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
                            "name": SYMBOL_NAME_MAP.get(symbol, symbol),
                            "change_pct": change_pct,
                            "volume": volume,
                        }
                    )
            except Exception as e:
                log(f"候補取得スキップ: {symbol} / {e}")

        data.sort(key=lambda x: x["change_pct"], reverse=True)
        top = data[:CONFIG["max_candidates"]]
        return [{"symbol": x["symbol"], "name": x["name"]} for x in top]

    def get_all_candidates(self) -> List[Dict[str, Any]]:
        return [
            {"symbol": symbol, "name": SYMBOL_NAME_MAP.get(symbol, symbol)}
            for symbol in CONFIG["candidate_symbols"]
        ]


# ---------- ニュース ----------
class NewsClient:
    def get_news_for_symbol(self, symbol: str) -> List[NewsItem]:
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
        try:
            o = float(row["Open"])
            h = float(row["High"])
            l = float(row["Low"])
            c = float(row["Close"])
            v = float(row["Volume"])
        except Exception:
            continue

        values = [o, h, l, c, v]

        if any(math.isnan(x) for x in values):
            continue

        if c <= 0 or h <= 0 or l <= 0:
            continue

        bars.append(
            PriceBar(
                date=str(date.date()),
                open=o,
                high=h,
                low=l,
                close=c,
                volume=int(v),
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

        if snapshot.price_change_pct >= 1.0:
            score += 10
            positives.append(f"前日比がしっかりプラス ({snapshot.price_change_pct:.2f}%)")
        elif snapshot.price_change_pct > 0:
            score += 3
            positives.append(f"前日比プラス ({snapshot.price_change_pct:.2f}%)")
        else:
            negatives.append(f"前日比マイナス ({snapshot.price_change_pct:.2f}%)")

        if snapshot.volume >= CONFIG["min_volume"]:
            score += 20
            positives.append(f"出来高が基準以上 ({snapshot.volume:,}株)")
        else:
            negatives.append(f"出来高不足 ({snapshot.volume:,}株)")

        if ma25_now is not None and latest_close > ma25_now:
            score += 15
            positives.append("株価が25日線の上")
        else:
            negatives.append("株価が25日線より弱い")

        if ma25_now is not None and ma25_prev is not None and ma25_now > ma25_prev:
            score += 10
            positives.append("25日線が上向き")
        else:
            negatives.append("25日線の勢いが弱い")

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

        is_bullish_candle = latest_close > latest_open
        if is_bullish_candle:
            score += 10
            positives.append("当日が陽線")
        else:
            negatives.append("当日が陰線")

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

        if len(closes) >= 2 and closes[-1] > closes[-2]:
            score += 10
            positives.append("直近で買い戻しが入っている")
        else:
            negatives.append("直近の戻しが弱い")

        if snapshot.news:
            positive_news_count = sum(1 for n in snapshot.news if n.sentiment == "positive")
            high_impact_count = sum(1 for n in snapshot.news if n.impact == "high")
            score += positive_news_count * 5
            score += high_impact_count * 5
            positives.append(f"材料ニュースあり ({len(snapshot.news)}件)")
        else:
            negatives.append("材料ニュースは目立たない")

        recent_high = max(highs[-5:])
        recent_low = min(lows[-5:])

        setup_type = "none"
        entry_price = 0.0
        stop_price = 0.0
        take_profit_price = 0.0

        is_breakout_ready = (
            latest_close > recent_high * (1 + CONFIG["breakout_buffer"])
            and snapshot.volume >= CONFIG["min_volume"]
            and latest_close > latest_open
        )

        if is_pullback_ready:
            setup_type = "pullback"
            entry_price = round(latest_close, 2)
            stop_price = round(recent_low * CONFIG["stop_buffer"], 2)
            take_profit_price = round(entry_price * (1 + CONFIG["take_profit_pct"]), 2)

        elif is_breakout_ready:
            setup_type = "breakout"
            entry_price = round(latest_close, 2)
            stop_price = round(recent_low * CONFIG["stop_buffer"], 2)
            take_profit_price = round(entry_price + (entry_price - stop_price) * 2.2, 2)

        else:
            negatives.append("押し目/ブレイクの形が未完成")

        rr = 0.0
        position_size = 0
        risk_amount = 0.0

        if entry_price > 0 and stop_price > 0 and take_profit_price > 0:
            stop_pct = safe_div((entry_price - stop_price), entry_price)

            if stop_pct <= CONFIG["max_stop_pct"]:
                rr = calc_rr(entry_price, stop_price, take_profit_price)
                position_size, risk_amount = calc_position_size(entry_price, stop_price)
            else:
                negatives.append(f"損切り幅が大きすぎる ({stop_pct * 100:.2f}%)")

        if setup_type != "none" and rr < CONFIG["min_rr"]:
            negatives.append(f"RR不足 ({rr:.2f})")

        if setup_type != "none" and position_size <= 0:
            negatives.append("資金または上限不足で発注不可")

        passed = (
            score >= CONFIG["min_score_to_notify"]
            and setup_type in ["pullback", "breakout"]
            and rr >= CONFIG["min_rr"]
            and position_size > 0
        )

        if setup_type == "pullback":
            entry_idea = f"押し目候補 / {entry_price:.2f}ドル付近"
            stop_idea = f"{stop_price:.2f}ドル割れで損切り"
            take_profit_idea = f"{take_profit_price:.2f}ドル付近で利確候補"
            verdict = "押し目監視"

        elif setup_type == "breakout":
            entry_idea = f"ブレイク候補 / {entry_price:.2f}ドル付近"
            stop_idea = f"{stop_price:.2f}ドル割れで損切り"
            take_profit_idea = f"{take_profit_price:.2f}ドル付近で利確候補"
            verdict = "ブレイク監視"

        else:
            entry_idea = "見送り"
            stop_idea = "見送り"
            take_profit_idea = "見送り"
            verdict = "様子見"

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
            setup_type=setup_type,
            entry_price=entry_price,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            rr=rr,
            position_size=position_size,
            risk_amount=risk_amount,
        )


# ---------- 前兆シグナル ----------
class SignalHintEngine:
    def evaluate(self, snapshot: SymbolSnapshot) -> Optional[SignalHint]:
        closes = [b.close for b in snapshot.bars]
        highs = [b.high for b in snapshot.bars]
        lows = [b.low for b in snapshot.bars]
        opens = [b.open for b in snapshot.bars]
        volumes = [b.volume for b in snapshot.bars]

        if len(closes) < 80:
            return None

        ma25_list = sma(closes, 25)
        ma75_list = sma(closes, 75)
        sma25_now = ma25_list[-1]
        sma75_now = ma75_list[-1]

        if sma25_now is None or sma75_now is None:
            return None

        close = closes[-1]
        open_ = opens[-1]
        low = lows[-1]
        volume = volumes[-1]
        avg_volume20 = sum(volumes[-20:]) / 20
        volume_ratio = safe_div(volume, avg_volume20)

        recent_high, recent_low = pick_recent_high_low_from_bars(snapshot.bars, lookback=10)
        dynamic_score = calc_dynamic_score_from_snapshot(snapshot)

        is_uptrend = close > sma25_now and sma25_now > sma75_now
        bullish_today = close > open_
        near_recent_high = close >= recent_high * 0.975
        near_sma25 = safe_div(abs(close - sma25_now), close) <= 0.03
        touched_ma25 = low <= sma25_now * 1.01
        strong_volume = volume_ratio >= 1.2

        positives: List[str] = []
        negatives: List[str] = []

        if is_uptrend:
            positives.append("上昇トレンド継続")
        else:
            negatives.append("トレンド弱い")

        if bullish_today:
            positives.append("当日陽線")
        else:
            negatives.append("当日陰線")

        if strong_volume:
            positives.append(f"出来高倍率 {round(volume_ratio, 2)}")
        else:
            negatives.append(f"出来高弱め {round(volume_ratio, 2)}")

        if near_recent_high:
            positives.append("高値圏にかなり接近")
        if near_sma25:
            positives.append("25日線近辺")
        if touched_ma25 and bullish_today:
            positives.append("25日線タッチ後の戻し")

        hint_type = ""
        trigger_text = ""
        status = ""

        if is_uptrend and near_recent_high and strong_volume:
            hint_type = "ブレイク前兆"
            trigger_text = f"{round(recent_high, 2)}超えで本格化"
            status = "高値圏で発射準備"
        elif is_uptrend and near_sma25 and bullish_today:
            hint_type = "押し目前兆"
            trigger_text = f"{round(sma25_now, 2)}付近反発継続で候補化"
            status = "25日線反発待ち"
        elif is_uptrend and touched_ma25 and not bullish_today:
            hint_type = "監視継続"
            trigger_text = "次の陽線確認待ち"
            status = "押し目形成中"
        else:
            return None

        return SignalHint(
            symbol=snapshot.symbol,
            name=snapshot.name,
            status=status,
            hint_type=hint_type,
            trigger_text=trigger_text,
            dynamic_score=dynamic_score,
            reasons_positive=positives,
            reasons_negative=negatives,
        )


# ---------- 事前シナリオ ----------
class PreScenarioEngine:
    def evaluate(self, snapshot: SymbolSnapshot) -> Optional[PreScenario]:
        closes = [b.close for b in snapshot.bars]
        highs = [b.high for b in snapshot.bars]
        lows = [b.low for b in snapshot.bars]
        opens = [b.open for b in snapshot.bars]
        volumes = [b.volume for b in snapshot.bars]

        if len(closes) < 80:
            return None

        if snapshot.price_change_pct < 1.0:
            return None

        ma25_list = sma(closes, 25)
        ma75_list = sma(closes, 75)

        sma25_now = ma25_list[-1]
        sma75_now = ma75_list[-1]

        if sma25_now is None or sma75_now is None:
            return None
        if isinstance(sma25_now, float) and math.isnan(sma25_now):
            return None
        if isinstance(sma75_now, float) and math.isnan(sma75_now):
            return None

        close = closes[-1]
        open_ = opens[-1]
        high = highs[-1]
        low = lows[-1]
        volume = volumes[-1]

        raw_vals = [close, open_, high, low, volume]
        for v in raw_vals:
            if v is None:
                return None
            if isinstance(v, float) and math.isnan(v):
                return None

        if len(volumes) < 20:
            return None

        recent_high, recent_low = pick_recent_high_low_from_bars(snapshot.bars, lookback=10)

        if recent_high is None or recent_low is None:
            return None
        if isinstance(recent_high, float) and math.isnan(recent_high):
            return None
        if isinstance(recent_low, float) and math.isnan(recent_low):
            return None

        avg_volume20 = sum(volumes[-20:]) / 20
        if isinstance(avg_volume20, float) and math.isnan(avg_volume20):
            return None

        dynamic_score = calc_dynamic_score_from_snapshot(snapshot)
        volume_ratio = round(safe_div(volume, avg_volume20), 2)
        range_pct = round(safe_div((high - low), close) * 100, 2)

        is_uptrend = (close > sma25_now) and (sma25_now > sma75_now)
        near_recent_high = close >= recent_high * 0.985
        near_sma25 = safe_div(abs(close - sma25_now), close) <= 0.025
        weak_trend = close < sma25_now
        bullish_today = close > open_

        scenario_type = "様子見"
        status = "中立"
        entry_condition = ""
        invalid_condition = f"{round(recent_low, 2)}割れ"
        comment = "方向感が固まるまで待つ"

        order_type = "なし"
        entry_price = 0.0
        stop_price = 0.0
        take_profit_price = 0.0
        rr = 0.0
        position_size = 0
        risk_amount = 0.0
        order_ready = False

        positives: List[str] = []
        negatives: List[str] = []

        if is_uptrend:
            positives.append("上昇トレンド（株価 > 25日線 > 75日線）")
        else:
            negatives.append("上昇トレンド条件未達")

        if bullish_today:
            positives.append("当日陽線")
        else:
            negatives.append("当日陰線")

        positives.append(f"出来高倍率 {volume_ratio}")
        positives.append(f"値幅 {range_pct}%")

        distance_to_sma25_pct = safe_div(abs(close - sma25_now), close) * 100
        positives.append(f"25日線乖離 {round(distance_to_sma25_pct, 2)}%")

        if near_recent_high:
            positives.append("高値圏に接近")
        else:
            negatives.append("高値圏までもう少し")

        if near_sma25:
            positives.append("25日線付近")
        else:
            negatives.append("25日線からやや遠い")

        if is_uptrend and near_sma25 and bullish_today:
            scenario_type = "押し目待ち"
            status = "上昇トレンド継続"
            entry_condition = f"{round(sma25_now, 2)}付近で反発陽線"
            comment = "高値追いせず、25日線付近の指値待ち"

            order_type = "指値"
            entry_price = round(sma25_now * (1 + CONFIG["night_pullback_buffer"]), 2)
            stop_price = round(recent_low * CONFIG["stop_buffer"], 2)
            take_profit_price = round(
                entry_price + (entry_price - stop_price) * CONFIG["night_rr_target"], 2
            )
            positives.append("押し目条件に合致")

        elif is_uptrend and near_recent_high:
            scenario_type = "ブレイク待ち"
            status = "高値圏で強い持ち合い"
            entry_condition = f"{round(recent_high, 2)}上抜け＋出来高増"
            comment = "逆指値で高値抜けを狙う"

            order_type = "逆指値"
            entry_price = round(recent_high * (1 + CONFIG["night_breakout_buffer"]), 2)
            stop_price = round(recent_low * CONFIG["stop_buffer"], 2)
            take_profit_price = round(
                entry_price + (entry_price - stop_price) * CONFIG["night_rr_target"], 2
            )
            positives.append("ブレイク監視条件に合致")

        elif weak_trend:
            scenario_type = "見送り"
            status = "トレンド弱め"
            entry_condition = "まだ弱いので待機"
            comment = "優先度低め。無理に触らない"
            negatives.append("株価が25日線より下")

        else:
            scenario_type = "様子見"
            status = "中立"
            entry_condition = f"{round(recent_high, 2)}上抜け or {round(sma25_now, 2)}反発待ち"
            comment = "条件がまだ中途半端"
            negatives.append("押し目/ブレイクどちらも未完成")

        if entry_price > 0 and stop_price > 0 and take_profit_price > 0:
            stop_pct = safe_div((entry_price - stop_price), entry_price)

            if stop_pct <= CONFIG["max_stop_pct"] and entry_price > stop_price:
                rr = calc_rr(entry_price, stop_price, take_profit_price)
                position_size, risk_amount = calc_position_size(entry_price, stop_price)

                positives.append(f"損切り幅 {round(stop_pct * 100, 2)}%")
                positives.append(f"RR {round(rr, 2)}")
                positives.append(f"想定ロット {position_size}株")

                if rr >= CONFIG["min_rr"] and position_size > 0:
                    order_ready = True
                    positives.append("夜仕込み可能")
                else:
                    if rr < CONFIG["min_rr"]:
                        negatives.append(f"RR不足 ({round(rr, 2)})")
                    if position_size <= 0:
                        negatives.append("資金または上限不足で発注不可")
            else:
                negatives.append(f"損切り幅が広すぎる ({round(stop_pct * 100, 2)}%)")

        if not order_ready and entry_price <= 0:
            negatives.append("注文価格未確定")

        return PreScenario(
            symbol=snapshot.symbol,
            name=snapshot.name,
            status=status,
            scenario_type=scenario_type,
            recent_high=round(recent_high, 2),
            recent_low=round(recent_low, 2),
            sma25=round(sma25_now, 2),
            sma75=round(sma75_now, 2),
            entry_condition=entry_condition,
            invalid_condition=invalid_condition,
            comment=comment,
            dynamic_score=round(dynamic_score, 2),
            volume_ratio=volume_ratio,
            range_pct=range_pct,
            order_type=order_type,
            entry_price=entry_price,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            rr=round(rr, 2),
            position_size=position_size,
            risk_amount=round(risk_amount, 2),
            order_ready=order_ready,
            reasons_positive=positives,
            reasons_negative=negatives,
        )


# ---------- レポート整形 ----------
class ReportFormatter:
    def format_line_message(self, snapshot: SymbolSnapshot, result: RuleResult) -> str:
        positives = format_reason_lines(result.reasons_positive)
        negatives = format_reason_lines(result.reasons_negative)

        return (
            f"【米株トレードBOT通知】\n"
            f"銘柄: {snapshot.symbol} / {snapshot.name}\n"
            f"現在値: ${snapshot.current_price:.2f}\n"
            f"前日比: {snapshot.price_change_pct:.2f}%\n"
            f"出来高: {snapshot.volume:,}株\n\n"
            f"スコア: {result.score}\n"
            f"型: {result.setup_type}\n"
            f"判定: {result.verdict}\n\n"
            f"■売買プラン\n"
            f"エントリー: ${result.entry_price:.2f}\n"
            f"損切り: ${result.stop_price:.2f}\n"
            f"利確: ${result.take_profit_price:.2f}\n"
            f"RR: {result.rr:.2f}\n"
            f"ロット: {result.position_size}株\n"
            f"許容損失: ${result.risk_amount:.2f}\n\n"
            f"■プラス材料\n{positives}\n\n"
            f"■注意点\n{negatives}"
        )

    def format_signal_message(
        self,
        hints: List[SignalHint],
        top_n: int = 5,
    ) -> List[str]:
        ranked = sorted(hints, key=lambda x: x.dynamic_score, reverse=True)[:top_n]
        lines: List[str] = []
        lines.append(f"【米株 前兆シグナル TOP{len(ranked)}】")
        lines.append("")

        if not ranked:
            lines.append("前兆シグナルなし")
            return lines

        for i, h in enumerate(ranked, start=1):
            lines.append(f"{i}. {h.symbol} {h.name}")
            lines.append(f"状況：{h.status}")
            lines.append(f"型：{h.hint_type}")
            lines.append(f"トリガー：{h.trigger_text}")
            lines.append("根拠：")
            lines.append(format_reason_lines(h.reasons_positive, limit=4))
            if h.reasons_negative:
                lines.append("注意：")
                lines.append(format_reason_lines(h.reasons_negative, limit=2))
            lines.append("")

        return lines

    def format_pre_scenario_message(
        self,
        scenarios: List[PreScenario],
        hints: List[SignalHint],
        top_n: int = 5,
    ) -> str:
        lines: List[str] = []

        lines.extend(self.format_signal_message(hints, top_n=CONFIG["signal_top_n"]))
        lines.append("----------------------")
        lines.append("")

        if not scenarios:
            lines.append("【米株 監視候補 TOP0】")
            lines.append("")
            lines.append("候補なし")
            lines.append("")
            lines.append("----------------------")
            lines.append("")
            lines.append("【米株 夜仕込み候補 TOP0】")
            lines.append("")
            lines.append("注文できる候補なし")
            return "\n".join(lines)

        sorted_all = sorted(
            scenarios,
            key=lambda x: x.dynamic_score,
            reverse=True
        )[:CONFIG["monitor_top_n"]]

        orderable = [s for s in scenarios if s.order_ready]
        sorted_orderable = sorted(
            orderable,
            key=lambda x: x.dynamic_score,
            reverse=True
        )[:top_n]

        lines.append(f"【米株 監視候補 TOP{len(sorted_all)}】")
        lines.append("")

        for i, s in enumerate(sorted_all, start=1):
            lines.append(f"{i}. {s.symbol} {s.name}")
            lines.append(f"状況：{s.status}")
            lines.append(f"型：{s.scenario_type}")
            lines.append(
                f"監視：高値 {s.recent_high} / 安値 {s.recent_low} / 25日線 {s.sma25} / 75日線 {s.sma75}"
            )
            lines.append("監視理由：")
            lines.append(format_reason_lines(s.reasons_positive, limit=6))
            lines.append("注意：")
            lines.append(format_reason_lines(s.reasons_negative, limit=4))
            lines.append("")

        lines.append("----------------------")
        lines.append("")

        lines.append(f"【米株 夜仕込み候補 TOP{len(sorted_orderable)}】")
        lines.append("")

        if not sorted_orderable:
            lines.append("注文できる候補なし")
            return "\n".join(lines)

        for i, s in enumerate(sorted_orderable, start=1):
            if s.order_type == "逆指値":
                order_text = f"買い：逆指値 ${s.entry_price:.2f}"
            elif s.order_type == "指値":
                order_text = f"買い：指値 ${s.entry_price:.2f}"
            else:
                continue

            lines.append(f"{i}. {s.symbol} {s.name}")
            lines.append(f"状況：{s.status}")
            lines.append(f"型：{s.scenario_type}")
            lines.append("根拠：")
            lines.append(format_reason_lines(s.reasons_positive, limit=6))
            lines.append("")
            lines.append("■新規注文")
            lines.append(order_text)
            lines.append(f"株数：{s.position_size}株")
            lines.append("")
            lines.append("■決済注文（IFD-OCO）")
            lines.append(f"利確：指値 ${s.take_profit_price:.2f}")
            lines.append(f"損切：逆指値 ${s.stop_price:.2f}")
            lines.append("")
            lines.append(f"RR：{s.rr:.2f}")
            lines.append(f"無効条件：{s.invalid_condition}")
            lines.append("----------------------")

        return "\n".join(lines)

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
        if not self.channel_access_token or not self.user_id:
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
    def __init__(self, base_dir: str = "logs_us"):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def save_result(self, symbol: str, payload: Dict[str, Any]) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self.base_dir, f"{timestamp}_{symbol}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return path


# ---------- BOT本体 ----------
class TradeBot:
    def __init__(self):
        self.market_client = MarketDataClient()
        self.news_client = NewsClient()
        self.rule_engine = RuleEngine()
        self.signal_hint_engine = SignalHintEngine()
        self.pre_scenario_engine = PreScenarioEngine()
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

    def run_trade_mode(self) -> None:
        log("BOT開始: trade mode")
        universe = self.market_client.get_top_movers()
        log(f"候補数: {len(universe)}")

        go_match_count = 0

        for item in universe[:CONFIG["max_candidates"]]:
            try:
                snapshot = self.build_snapshot(item)
                result = self.rule_engine.evaluate(snapshot)

                payload = self.formatter.format_log_json(snapshot, result)
                saved_path = self.storage.save_result(snapshot.symbol, payload)
                log(f"保存: {saved_path}")

                if result.passed:
                    go_match_count += 1
                    log(
                        f"GO条件一致（LINE送信なし）: {snapshot.symbol} / "
                        f"score={result.score} / "
                        f"setup={result.setup_type} / "
                        f"entry={result.entry_price:.2f} / "
                        f"stop={result.stop_price:.2f} / "
                        f"take={result.take_profit_price:.2f} / "
                        f"rr={result.rr:.2f} / "
                        f"size={result.position_size}"
                    )
                else:
                    log(
                        f"通知見送り: {snapshot.symbol} / "
                        f"score={result.score} / "
                        f"setup={result.setup_type} / "
                        f"rr={result.rr:.2f} / "
                        f"size={result.position_size} / "
                        f"negatives={result.reasons_negative}"
                    )

            except Exception as e:
                log(f"エラー: {item.get('symbol', 'UNKNOWN')} / {e}")

        log(f"BOT終了 / GO一致数: {go_match_count}")

    def run_pre_scenario_mode(self) -> None:
        log("BOT開始: prescenario mode")
        universe = self.market_client.get_all_candidates()
        scenarios: List[PreScenario] = []
        hints: List[SignalHint] = []

        for item in universe:
            try:
                snapshot = self.build_snapshot(item)

                hint = self.signal_hint_engine.evaluate(snapshot)
                if hint is not None:
                    hints.append(hint)

                scenario = self.pre_scenario_engine.evaluate(snapshot)
                if scenario is not None:
                    scenarios.append(scenario)

            except Exception as e:
                log(f"事前シナリオスキップ: {item.get('symbol', 'UNKNOWN')} / {e}")

        message = self.formatter.format_pre_scenario_message(
            scenarios=scenarios,
            hints=hints,
            top_n=CONFIG["prescenario_top_n"],
        )
        self.notifier.send_text(message)
        log("事前シナリオ通知完了")

    def run(self) -> None:
        if RUN_MODE == "trade":
            self.run_trade_mode()
        elif RUN_MODE == "prescenario":
            self.run_pre_scenario_mode()
        else:
            raise ValueError(f"不明なRUN_MODE: {RUN_MODE}")


# ---------- 実行 ----------
if __name__ == "__main__":
    bot = TradeBot()
    bot.run()
