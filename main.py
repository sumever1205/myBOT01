import os
import json
import requests
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from dotenv import load_dotenv
from collections import defaultdict

# ✅ 台灣時區
TW = timezone(timedelta(hours=8))

# ✅ 使用環境變數 + Volume 資料路徑
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))
RECORD_FILE = "/app/data/records.json"

# ========= 紀錄處理 =========
def load_records():
    if not os.path.exists(RECORD_FILE):
        return []
    with open(RECORD_FILE, "r") as f:
        return json.load(f)

def save_records(records):
    with open(RECORD_FILE, "w") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

def append_record(source, symbol):
    records = load_records()
    now = datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S")
    records.append({"source": source, "symbol": symbol, "timestamp": now})
    save_records(records)

def get_last_symbols():
    records = load_records()
    return {(r["source"], r["symbol"]) for r in records}

def clean_symbol(symbol: str) -> str:
    if symbol.startswith("KRW-"):
        symbol = symbol.replace("KRW-", "")
    for suffix in ["-USDT", "-SWAP", "USDT"]:
        if symbol.endswith(suffix):
            symbol = symbol.replace(suffix, "")
    return symbol

# ========= 初始化 =========
def initialize_record_file():
    if os.path.exists(RECORD_FILE):
        print("📁 已存在 records.json，跳過初始化")
        return

    print("🔍 正在初始化交易對資料...")

    all_sources = {
        "Binance": fetch_binance(),
        "Bybit": fetch_bybit(),
        "OKX": fetch_okx(),
        "Upbit": fetch_upbit()
    }

    now = datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S")
    records = [
        {"source": source, "symbol": symbol, "timestamp": now}
        for source, symbols in all_sources.items()
        for symbol in symbols
    ]
    save_records(records)
    print(f"✅ 初始交易對紀錄已建立，共 {len(records)} 筆。")
# ========= 抓資料 =========
def fetch_binance():
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
    data = requests.get(url).json()
    symbols = [s["symbol"] for s in data["symbols"] if s["contractType"] == "PERPETUAL" and s["quoteAsset"] == "USDT"]
    print(f"🧪 Binance 抓到 {len(symbols)} 筆，其中前5筆：{symbols[:5]}")
    return symbols

def fetch_bybit():
    url = "https://api.bybit.com/v5/market/instruments-info?category=linear"
    data = requests.get(url).json()
    return [s["symbol"] for s in data["result"]["list"] if s["symbol"].endswith("USDT")]

def fetch_okx():
    url = "https://www.okx.com/api/v5/public/instruments?instType=SWAP"
    try:
        data = requests.get(url, timeout=10).json()
        raw = data.get("data", [])
        filtered = [s["instId"].replace("-SWAP", "") for s in raw if s.get("settleCcy") == "USDT"]
        print(f"🧪 OKX 原始：{len(raw)} 筆，符合 USDT 永續：{len(filtered)} 筆，已排除：{len(raw) - len(filtered)} 筆")
        return filtered
    except Exception as e:
        print(f"❌ OKX 抓取錯誤：{e}")
        return []

def fetch_upbit():
    url = "https://api.upbit.com/v1/market/all"
    data = requests.get(url).json()
    return [s["market"] for s in data if s["market"].startswith("KRW-")]

# ========= 比對邏輯 =========
async def check_all():
    all_sources = {
        "Binance": fetch_binance(),
        "Bybit": fetch_bybit(),
        "OKX": fetch_okx(),
        "Upbit": fetch_upbit()
    }

    last_symbols = get_last_symbols()
    notified = []

    for source, symbols in all_sources.items():
        for symbol in symbols:
            if (source, symbol) not in last_symbols:
                append_record(source, symbol)
                clean_name = clean_symbol(symbol)
                print(f"🟢 新上 {source}：{symbol}")
                notified.append(f"📢【{source}】新上：{clean_name}")
            else:
                if source == "Binance":
                    print(f"🔍 Binance 已存在：{symbol}")

    if notified:
        await notify("\n".join(notified))

async def notify(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    requests.post(url, data=payload)

# ========= 指令 =========
async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    records = load_records()
    sorted_records = sorted(records, key=lambda x: x["timestamp"], reverse=True)
    lines = [f"{r['timestamp']} - {r['source']}：{r['symbol']}" for r in sorted_records]
    text = "\n".join(lines[:50]) or "📭 尚無紀錄"
    await update.message.reply_text(text)

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    records = load_records()
    initial_time = records[0]["timestamp"]
    init_symbols = {(r["source"], r["symbol"]) for r in records if r["timestamp"] == initial_time}
    new_records = [r for r in records if (r["source"], r["symbol"]) not in init_symbols]

    grouped = defaultdict(list)
    for r in sorted(new_records, key=lambda x: x["timestamp"], reverse=True):
        grouped[r["source"]].append(r)

    output = []
    for source in ["Binance", "Bybit", "OKX", "Upbit"]:
        recent = grouped[source][:10]
        if recent:
            output.append(f"📊 【{source}】最新上幣：")
            for r in recent:
                time_str = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S").strftime("%m-%d")
                output.append(f"- {time_str} - {clean_symbol(r['symbol'])}")
            output.append("")

    await update.message.reply_text("\n".join(output) or "📭 無新增紀錄")

async def force_check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await check_all()
    await update.message.reply_text("✅ 已執行一次比對")

# ========= 啟動 =========
async def main():
    initialize_record_file()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("check", check_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("forcecheck", force_check_command))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_all, "interval", minutes=3)
    scheduler.start()

    print("✅ 機器人已啟動")
    await notify("✅ 監控機器人已啟動完成")
    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    import nest_asyncio

    nest_asyncio.apply()
    asyncio.run(main())
