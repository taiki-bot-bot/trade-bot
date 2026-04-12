from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests
import yfinance as yf


LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.getenv("LINE_USER_ID", "")
ANALYZE_TARGET = os.getenv("ANALYZE_TARGET", "jp").lower()

CONFIG = {
    "jp": {
        "title": "日本株ログ分析",
        "log_dir": "logs",
        "signal_dir": "logs_signals",
        "holding_days": 10,
        "min_logs_to_analyze": 5,
        "top_n_groups": 5,
        "top_n_examples": 3,
    },
    "us": {
        "title": "米株ログ分析",
        "log_dir": "logs_us",
        "signal_dir": "logs_us_signals",
        "holding_days": 10,
        "min_logs_to_analyze": 5,
        "top_n_groups": 5,
        "top_n_examples": 3,
    },
}


def log(message: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}")


def safe_div(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return a / b


@dataclass
class TradeSignal:
    timestamp: str
    symbol: str
    name: str
    current_price: float
    price_change_pct: float
    volume: int
    score: int
    setup_type: str
    entry_price: float
    stop_price: float
    take_profit_price: float
    rr: float
    position_size: int
    passed: bool


@dataclass
class SignalHintLog:
    timestamp: str
    symbol: str
    name: str
    hint_type: str
    status: str
    trigger_text: str
    dynamic_score: float


@dataclass
class TradeOutcome:
    symbol: str
    name: str
    setup_type: str
    score: int
    rr: float
    entry_price: float
    stop_price: float
    take_profit_price: float
    result: str
    exit_price: float
    return_pct: float
    max_return_pct: float
    min_return_pct: float
    bars_to_exit: int


class LineNotifier:
    PUSH_URL = "https://api.line.me/v2/bot/message/push"

    def __init__(self, token: str, user_id: str):
        self.token = token
        self.user_id = user_id

    def send_text(self, text: str) -> None:
        if not self.token or not self.user_id:
            log("LINE設定未入力のため通知スキップ")
            print("\n===== LINE送信プレビュー =====")
            print(text)
            print("============================\n")
            return

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        body = {
            "to": self.user_id,
            "messages": [{"type": "text", "text": text[:5000]}],
        }
        response = requests.post(self.PUSH_URL, headers=headers, json=body, timeout=20)
        response.raise_for_status()
        log("LINE通知送信完了")


class LogLoader:
    def __init__(self, log_dir: str, signal_dir: str):
        self.log_dir = log_dir
        self.signal_dir = signal_dir

    def load_signals(self) -> List[TradeSignal]:
        if not os.path.exists(self.log_dir):
            log(f"ログフォルダなし: {self.log_dir}")
            return []

        signals: List[TradeSignal] = []

        for filename in sorted(os.listdir(self.log_dir)):
            if not filename.endswith(".json"):
                continue

            path = os.path.join(self.log_dir, filename)

            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)

                snapshot = payload.get("snapshot", {})
                rule_result = payload.get("rule_result", {})

                signals.append(
                    TradeSignal(
                        timestamp=str(payload.get("timestamp", "")),
                        symbol=str(snapshot.get("symbol", "")),
                        name=str(snapshot.get("name", "")),
                        current_price=float(snapshot.get("current_price", 0)),
                        price_change_pct=float(snapshot.get("price_change_pct", 0)),
                        volume=int(snapshot.get("volume", 0)),
                        score=int(rule_result.get("score", 0)),
                        setup_type=str(rule_result.get("setup_type", "none")),
                        entry_price=float(rule_result.get("entry_price", 0)),
                        stop_price=float(rule_result.get("stop_price", 0)),
                        take_profit_price=float(rule_result.get("take_profit_price", 0)),
                        rr=float(rule_result.get("rr", 0)),
                        position_size=int(rule_result.get("position_size", 0)),
                        passed=bool(rule_result.get("passed", False)),
                    )
                )
            except Exception as e:
                log(f"ログ読み込み失敗: {filename} / {e}")

        return signals

    def load_signal_hints(self) -> List[SignalHintLog]:
        if not os.path.exists(self.signal_dir):
            log(f"前兆ログフォルダなし: {self.signal_dir}")
            return []

        hints: List[SignalHintLog] = []

        for filename in sorted(os.listdir(self.signal_dir)):
            if not filename.endswith(".json"):
                continue

            path = os.path.join(self.signal_dir, filename)

            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)

                hint = payload.get("signal_hint", {})
                snapshot = payload.get("snapshot", {})

                hints.append(
                    SignalHintLog(
                        timestamp=str(payload.get("timestamp", "")),
                        symbol=str(snapshot.get("symbol", "")),
                        name=str(snapshot.get("name", "")),
                        hint_type=str(hint.get("hint_type", "")),
                        status=str(hint.get("status", "")),
                        trigger_text=str(hint.get("trigger_text", "")),
                        dynamic_score=float(hint.get("dynamic_score", 0)),
                    )
                )
            except Exception as e:
                log(f"前兆ログ読み込み失敗: {filename} / {e}")

        return hints


class OutcomeAnalyzer:
    def __init__(self, holding_days: int):
        self.holding_days = holding_days

    def _parse_timestamp(self, ts: str) -> Optional[datetime]:
        if not ts:
            return None
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return None

    def _download_future_bars(self, symbol: str, start_dt: datetime) -> List[Dict[str, float]]:
        start_date = (start_dt.date() + timedelta(days=1)).isoformat()
        end_date = (start_dt.date() + timedelta(days=30)).isoformat()

        stock = yf.Ticker(symbol)
        hist = stock.history(start=start_date, end=end_date, auto_adjust=False)

        if hist.empty:
            return []

        bars: List[Dict[str, float]] = []
        for _, row in hist.iterrows():
            try:
                o = float(row["Open"])
                h = float(row["High"])
                l = float(row["Low"])
                c = float(row["Close"])
            except Exception:
                continue

            vals = [o, h, l, c]
            if any(math.isnan(x) for x in vals):
                continue

            bars.append({"open": o, "high": h, "low": l, "close": c})

        return bars[: self.holding_days]

    def analyze_one(self, signal: TradeSignal) -> TradeOutcome:
        if (
            signal.entry_price <= 0
            or signal.stop_price <= 0
            or signal.take_profit_price <= 0
            or signal.setup_type not in ["pullback", "breakout"]
        ):
            return TradeOutcome(
                symbol=signal.symbol,
                name=signal.name,
                setup_type=signal.setup_type,
                score=signal.score,
                rr=signal.rr,
                entry_price=signal.entry_price,
                stop_price=signal.stop_price,
                take_profit_price=signal.take_profit_price,
                result="invalid",
                exit_price=0.0,
                return_pct=0.0,
                max_return_pct=0.0,
                min_return_pct=0.0,
                bars_to_exit=0,
            )

        ts = self._parse_timestamp(signal.timestamp)
        if ts is None:
            return TradeOutcome(
                symbol=signal.symbol,
                name=signal.name,
                setup_type=signal.setup_type,
                score=signal.score,
                rr=signal.rr,
                entry_price=signal.entry_price,
                stop_price=signal.stop_price,
                take_profit_price=signal.take_profit_price,
                result="invalid",
                exit_price=0.0,
                return_pct=0.0,
                max_return_pct=0.0,
                min_return_pct=0.0,
                bars_to_exit=0,
            )

        future_bars = self._download_future_bars(signal.symbol, ts)
        if not future_bars:
            return TradeOutcome(
                symbol=signal.symbol,
                name=signal.name,
                setup_type=signal.setup_type,
                score=signal.score,
                rr=signal.rr,
                entry_price=signal.entry_price,
                stop_price=signal.stop_price,
                take_profit_price=signal.take_profit_price,
                result="timeout",
                exit_price=0.0,
                return_pct=0.0,
                max_return_pct=0.0,
                min_return_pct=0.0,
                bars_to_exit=0,
            )

        max_return_pct = -999.0
        min_return_pct = 999.0

        for i, bar in enumerate(future_bars, start=1):
            high = bar["high"]
            low = bar["low"]

            high_ret = safe_div((high - signal.entry_price), signal.entry_price) * 100
            low_ret = safe_div((low - signal.entry_price), signal.entry_price) * 100

            max_return_pct = max(max_return_pct, high_ret)
            min_return_pct = min(min_return_pct, low_ret)

            if low <= signal.stop_price and high >= signal.take_profit_price:
                exit_price = signal.stop_price
                return_pct = safe_div((exit_price - signal.entry_price), signal.entry_price) * 100
                return TradeOutcome(
                    symbol=signal.symbol,
                    name=signal.name,
                    setup_type=signal.setup_type,
                    score=signal.score,
                    rr=signal.rr,
                    entry_price=signal.entry_price,
                    stop_price=signal.stop_price,
                    take_profit_price=signal.take_profit_price,
                    result="loss",
                    exit_price=exit_price,
                    return_pct=round(return_pct, 2),
                    max_return_pct=round(max_return_pct, 2),
                    min_return_pct=round(min_return_pct, 2),
                    bars_to_exit=i,
                )

            if low <= signal.stop_price:
                exit_price = signal.stop_price
                return_pct = safe_div((exit_price - signal.entry_price), signal.entry_price) * 100
                return TradeOutcome(
                    symbol=signal.symbol,
                    name=signal.name,
                    setup_type=signal.setup_type,
                    score=signal.score,
                    rr=signal.rr,
                    entry_price=signal.entry_price,
                    stop_price=signal.stop_price,
                    take_profit_price=signal.take_profit_price,
                    result="loss",
                    exit_price=exit_price,
                    return_pct=round(return_pct, 2),
                    max_return_pct=round(max_return_pct, 2),
                    min_return_pct=round(min_return_pct, 2),
                    bars_to_exit=i,
                )

            if high >= signal.take_profit_price:
                exit_price = signal.take_profit_price
                return_pct = safe_div((exit_price - signal.entry_price), signal.entry_price) * 100
                return TradeOutcome(
                    symbol=signal.symbol,
                    name=signal.name,
                    setup_type=signal.setup_type,
                    score=signal.score,
                    rr=signal.rr,
                    entry_price=signal.entry_price,
                    stop_price=signal.stop_price,
                    take_profit_price=signal.take_profit_price,
                    result="win",
                    exit_price=exit_price,
                    return_pct=round(return_pct, 2),
                    max_return_pct=round(max_return_pct, 2),
                    min_return_pct=round(min_return_pct, 2),
                    bars_to_exit=i,
                )

        last_close = future_bars[-1]["close"]
        return_pct = safe_div((last_close - signal.entry_price), signal.entry_price) * 100

        return TradeOutcome(
            symbol=signal.symbol,
            name=signal.name,
            setup_type=signal.setup_type,
            score=signal.score,
            rr=signal.rr,
            entry_price=signal.entry_price,
            stop_price=signal.stop_price,
            take_profit_price=signal.take_profit_price,
            result="timeout",
            exit_price=last_close,
            return_pct=round(return_pct, 2),
            max_return_pct=round(max_return_pct, 2),
            min_return_pct=round(min_return_pct, 2),
            bars_to_exit=len(future_bars),
        )


class SummaryBuilder:
    def __init__(self, title: str, top_n_groups: int, top_n_examples: int):
        self.title = title
        self.top_n_groups = top_n_groups
        self.top_n_examples = top_n_examples

    def _valid_decisive(self, outcomes: List[TradeOutcome]) -> List[TradeOutcome]:
        return [x for x in outcomes if x.result in ["win", "loss"]]

    def _valid_all(self, outcomes: List[TradeOutcome]) -> List[TradeOutcome]:
        return [x for x in outcomes if x.result in ["win", "loss", "timeout"]]

    def _win_rate(self, outcomes: List[TradeOutcome]) -> float:
        valid = self._valid_decisive(outcomes)
        if not valid:
            return 0.0
        wins = sum(1 for x in valid if x.result == "win")
        return safe_div(wins, len(valid)) * 100

    def _avg_rr(self, outcomes: List[TradeOutcome]) -> float:
        valid = self._valid_all(outcomes)
        if not valid:
            return 0.0
        return sum(x.rr for x in valid) / len(valid)

    def _avg_return(self, outcomes: List[TradeOutcome]) -> float:
        valid = self._valid_all(outcomes)
        if not valid:
            return 0.0
        return sum(x.return_pct for x in valid) / len(valid)

    def _group_lines(self, outcomes: List[TradeOutcome], key_name: str, key_fn) -> List[str]:
        groups: Dict[str, List[TradeOutcome]] = {}

        for o in self._valid_all(outcomes):
            key = key_fn(o)
            groups.setdefault(key, []).append(o)

        ranked = sorted(
            groups.items(),
            key=lambda kv: (self._win_rate(kv[1]), len(self._valid_decisive(kv[1]))),
            reverse=True,
        )

        lines: List[str] = []
        for key, vals in ranked[: self.top_n_groups]:
            wr = self._win_rate(vals)
            avg_ret = self._avg_return(vals)
            decisive_cnt = len(self._valid_decisive(vals))
            lines.append(
                f"・{key_name}={key} / 件数{len(vals)} / 決着{decisive_cnt} / 勝率{wr:.1f}% / 平均{avg_ret:.2f}%"
            )
        return lines

    def _example_lines(self, outcomes: List[TradeOutcome], result_type: str) -> List[str]:
        target = [x for x in outcomes if x.result == result_type]
        ranked = sorted(target, key=lambda x: x.return_pct, reverse=(result_type == "win"))

        lines: List[str] = []
        for o in ranked[: self.top_n_examples]:
            lines.append(
                f"・{o.symbol} {o.name} / {o.setup_type} / score{o.score} / RR{o.rr:.2f} / {o.return_pct:.2f}%"
            )
        return lines or ["・なし"]

    def build_message(
        self,
        signals: List[TradeSignal],
        outcomes: List[TradeOutcome],
        hint_logs: List[SignalHintLog],
    ) -> str:
        all_valid = self._valid_all(outcomes)
        decisive = self._valid_decisive(outcomes)

        wins = sum(1 for x in decisive if x.result == "win")
        losses = sum(1 for x in decisive if x.result == "loss")
        timeouts = sum(1 for x in all_valid if x.result == "timeout")

        win_rate = self._win_rate(outcomes)
        avg_rr = self._avg_rr(outcomes)
        avg_return = self._avg_return(outcomes)

        avg_max = sum(x.max_return_pct for x in all_valid) / len(all_valid) if all_valid else 0.0
        avg_min = sum(x.min_return_pct for x in all_valid) / len(all_valid) if all_valid else 0.0

        setup_lines = self._group_lines(outcomes, "型", lambda x: x.setup_type)
        score_lines = self._group_lines(
            outcomes,
            "score帯",
            lambda x: (
                "80+" if x.score >= 80 else
                "70s" if x.score >= 70 else
                "60s" if x.score >= 60 else
                "50s" if x.score >= 50 else
                "49以下"
            ),
        )
        rr_lines = self._group_lines(
            outcomes,
            "RR帯",
            lambda x: (
                "2.0+" if x.rr >= 2.0 else
                "1.7+" if x.rr >= 1.7 else
                "1.5+" if x.rr >= 1.5 else
                "1.3+" if x.rr >= 1.3 else
                "1.29以下"
            ),
        )

        hint_type_count: Dict[str, int] = {}
        for h in hint_logs:
            hint_type_count[h.hint_type] = hint_type_count.get(h.hint_type, 0) + 1

        hint_lines = [
            f"・{k} : {v}件"
            for k, v in sorted(hint_type_count.items(), key=lambda kv: kv[1], reverse=True)[:5]
        ] or ["・前兆ログなし"]

        best_lines = self._example_lines(outcomes, "win")
        worst_lines = self._example_lines(outcomes, "loss")

        comment = "まだデータ不足"
        if len(decisive) >= 20:
            if win_rate >= 60:
                comment = "今の条件は悪くない。大きく崩さず継続でOK"
            elif win_rate >= 50:
                comment = "中立。型かscore帯で絞ると改善余地あり"
            else:
                comment = "見直し候補。弱い型を削る余地がある"

        lines: List[str] = []
        lines.append(f"【{self.title}】")
        lines.append(f"総ログ数：{len(signals)}")
        lines.append(f"前兆ログ数：{len(hint_logs)}")
        lines.append(f"分析対象：{len(all_valid)}")
        lines.append(f"決着数：{len(decisive)}")
        lines.append(f"勝ち：{wins}")
        lines.append(f"負け：{losses}")
        lines.append(f"未決着：{timeouts}")
        lines.append(f"勝率：{win_rate:.1f}%")
        lines.append(f"平均RR：{avg_rr:.2f}")
        lines.append(f"平均損益：{avg_return:.2f}%")
        lines.append(f"平均最大伸び：{avg_max:.2f}%")
        lines.append(f"平均最大逆行：{avg_min:.2f}%")
        lines.append("")

        lines.append("■ 前兆発生状況")
        lines.extend(hint_lines)
        lines.append("")

        lines.append("■ 強い傾向（型）")
        lines.extend(setup_lines or ["・データ不足"])
        lines.append("")

        lines.append("■ 強い傾向（score帯）")
        lines.extend(score_lines or ["・データ不足"])
        lines.append("")

        lines.append("■ 強い傾向（RR帯）")
        lines.extend(rr_lines or ["・データ不足"])
        lines.append("")

        lines.append("■ 勝ちサンプル")
        lines.extend(best_lines)
        lines.append("")

        lines.append("■ 負けサンプル")
        lines.extend(worst_lines)
        lines.append("")

        lines.append("■ 総評")
        lines.append(f"・{comment}")
        lines.append("・これは実約定ではなく、ログ後の値動きによる擬似検証")

        return "\n".join(lines)


def main() -> None:
    target_conf = CONFIG.get(ANALYZE_TARGET, CONFIG["jp"])

    loader = LogLoader(
        log_dir=target_conf["log_dir"],
        signal_dir=target_conf["signal_dir"],
    )
    signals = loader.load_signals()
    hint_logs = loader.load_signal_hints()

    if len(signals) < target_conf["min_logs_to_analyze"]:
        msg = (
            f"【{target_conf['title']}】\n"
            f"ログ数不足：{len(signals)}件\n"
            f"前兆ログ数：{len(hint_logs)}件\n"
            f"分析は最低 {target_conf['min_logs_to_analyze']}件 から推奨"
        )
        LineNotifier(LINE_CHANNEL_ACCESS_TOKEN, LINE_USER_ID).send_text(msg)
        return

    analyzer = OutcomeAnalyzer(holding_days=target_conf["holding_days"])
    outcomes = [analyzer.analyze_one(s) for s in signals]

    builder = SummaryBuilder(
        title=target_conf["title"],
        top_n_groups=target_conf["top_n_groups"],
        top_n_examples=target_conf["top_n_examples"],
    )
    message = builder.build_message(signals, outcomes, hint_logs)

    LineNotifier(LINE_CHANNEL_ACCESS_TOKEN, LINE_USER_ID).send_text(message)


if __name__ == "__main__":
    main()
