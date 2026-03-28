import pandas as pd
import requests
import os
import glob
from datetime import datetime

DATA_PATH = "data/*.csv"

LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
USER_ID = os.environ["LINE_USER_ID"]

TARGET_DEFENSE = 2000000

LIMITS = {
    "コンビニ": 20000,
    "外食": 10000,
    "雑費": 10000,
    "サブスク": 3000
}

# ===== CSV =====
files = glob.glob(DATA_PATH)
if len(files) == 0:
    raise ValueError("CSVがない")

df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
df.columns = [str(c).strip() for c in df.columns]

# ===== 列判定 =====
amount_col = None
text_col = None
date_col = None

for c in df.columns:
    col = str(c).lower()

    if "金額" in col or "amount" in col:
        amount_col = c
    if "利用先" in col or "摘要" in col or "内容" in col:
        text_col = c
    if "日付" in col or "date" in col:
        date_col = c

if amount_col is None or text_col is None or date_col is None:
    raise ValueError(f"列が見つからん: {df.columns}")

# ===== 型 =====
df[amount_col] = pd.to_numeric(df[amount_col], errors="coerce")
df[date_col] = pd.to_datetime(df[date_col], errors="coerce")

# ===== 重複削除 =====
df["key"] = df[date_col].astype(str) + "_" + df[amount_col].astype(str) + "_" + df[text_col].astype(str)
df = df.drop_duplicates(subset="key")

# ===== 分類 =====
def classify(text):
    text = str(text)

    if "セブン" in text or "ファミマ" in text or "ローソン" in text:
        return "コンビニ"
    elif "AMZN DIGITAL" in text or "プライム" in text:
        return "サブスク"
    elif "AMAZON" in text:
        return "雑費"
    elif "スーパー" in text:
        return "食費"
    elif "保険" in text or "家賃" in text:
        return "固定費"
    elif "電気" in text:
        return "光熱費"
    elif "ガソリン" in text:
        return "交通費"
    elif "SBI" in text:
        return "投資"
    else:
        return "その他"

df["カテゴリ"] = df[text_col].apply(classify)

# ===== 今月（テストは全体）=====
df_month = df

# ===== 集計 =====
income = df_month[df_month[amount_col] > 0][amount_col].sum()
expense = abs(df_month[df_month[amount_col] < 0][amount_col].sum())
balance = income - expense
rate = (balance / income * 100) if income > 0 else 0

# ===== カテゴリ =====
by_cat = df_month.groupby("カテゴリ")[amount_col].sum().abs().sort_values(ascending=False)

# ===== 無駄 =====
waste = 0
waste_detail = []

for k, v in by_cat.items():
    if k in LIMITS:
        over = max(0, v - LIMITS[k])
        waste += over
        if over > 0:
            waste_detail.append(f"{k}超過 +¥{int(over):,}")

# ===== 改善案 =====
improve_text = "\n".join(waste_detail) if waste_detail else "大きな無駄なし"

# ===== 配分 =====
if balance > 0:
    invest = int(balance * 0.3)
    cash = int(balance * 0.7)
else:
    invest = 0
    cash = 0

# ===== 評価 =====
if balance < 0:
    grade = "D"
elif rate >= 30:
    grade = "S"
elif rate >= 20:
    grade = "A"
elif rate >= 10:
    grade = "B"
else:
    grade = "C"

# ===== TOP支出 =====
top_expense = by_cat.head(5).to_string()

# ===== メッセージ =====
message = f"""
【収支BOT】

■総合
評価：{grade}
収支：¥{int(balance):,}
貯蓄率：{rate:.1f}%
改善余地：¥{int(waste):,}

■今月
収入：¥{int(income):,}
支出：¥{int(expense):,}

■支出TOP
{top_expense}

■改善ポイント
{improve_text}

■配分
投資：¥{invest:,}
現金：¥{cash:,}
"""

# ===== LINE =====
url = "https://api.line.me/v2/bot/message/push"

headers = {
    "Authorization": f"Bearer {LINE_TOKEN}",
    "Content-Type": "application/json"
}

data = {
    "to": USER_ID,
    "messages": [{"type": "text", "text": message[:5000]}]
}

res = requests.post(url, headers=headers, json=data)
print(res.text)
res.raise_for_status()
