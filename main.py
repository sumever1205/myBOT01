import os
import json
import requests
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from dotenv import load_dotenv
from collections import defaultdict
from pathlib import Path

TW = timezone(timedelta(hours=8))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))
DATA_DIR = Path("/app/data")
RECORD_FILE = DATA_DIR / "records.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)

def load_records():
    if not RECORD_FILE.exists():
        return []
    try:
        with open(RECORD_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ 讀取 records.json 失敗：{e}")
        return []

def save_records(records):
    try:
        with open(RECORD_FILE, "w") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        print(f"✅ 寫入成功，共 {len(records)} 筆")
        backup_file = DATA_DIR / f"records_{datetime.now(TW).strftime('%Y%m%d')}.json"
        with open(backup_file, "w") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"❌ 寫入 records.json 失敗：{e}")

def append_record(source, symbol):
    records = load_records()
    now = datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S")
    records.append({"source": source, "symbol": symbol, "timestamp": now})
    save_records(records)

def get_last_symbols():
    return {(r["source"], r["symbol"]) for r in load_records()}

def clean_symbol(symbol: str) -> str:
    if symbol.startswith("KRW-"):
        symbol = symbol.replace("KRW-", "")
    for suffix in ["-USDT", "-SWAP", "USDT"]:
        if symbol.endswith(suffix):
            symbol = symbol.replace(suffix, "")
    return symbol

def initialize_record_file():
    if RECORD_FILE.exists():
        print("📁 已存在紀錄檔，跳過初始化")
        return

    print("🆕 正在初始化紀錄資料...")
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
    print(f"✅ 初始化完成，共 {len(records)} 筆")

def fetch_binance():
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
    data = requests.get(url).json()
    symbols = [s["symbol"] for s in data["symbols"] if s["contractType"] == "PERPETUAL" and s["quoteAsset"] == "USDT"]
    print(f"🧪 Binance 抓到 {len(symbols)} 筆")
    return symbols

def fetch_bybit():
    url = "https://api.bybit.com/v5/market/instruments-info?category=linear"
    data = requests.get(url).json()
    symbols = [s["symbol"] for s in data["result"]["list"] if s["symbol"].endswith("USDT")]
    print(f"🧪 Bybit 抓到 {len(symbols)} 筆")
    return symbols

def fetch_okx():
    url = "https://www.okx.com/api/v5/public/instruments?instType=SWAP"
    try:
        data = requests.get(url).json()
        raw = data.get("data", [])
        filtered = [s["instId"].replace("-SWAP", "") for s in raw if s.get("settleCcy") == "USDT"]
        print(f"🧪 OKX 抓到 {len(filtered)} 筆")
        return filtered
    except Exception as e:
        print(f"❌ OKX 抓取錯誤：{e}")
        return []

def fetch_upbit():
    url = "https://api.upbit.com/v1/market/all"
    data = requests.get(url).json()
    symbols = [s["market"] for s in data if s["market"].startswith("KRW-")]
    print(f"🧪 Upbit 抓到 {len(symbols)} 筆")
    return symbols

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
                print(f"🟢 新上 {source}: {symbol}")
                append_record(source, symbol)
                notified.append(f"📢【{source}】新上：{clean_symbol(symbol)}")

    if notified:
        await notify("\n".join(notified))
    else:
        print("✅ 無新增項目")

async def notify(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    requests.post(url, data=payload)

# ✅ 修正版 /check
async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    records = load_records()
    grouped = defaultdict(list)
    for r in sorted(records, key=lambda x: x["timestamp"], reverse=True):
        grouped[r["source"]].append(r)

    output = []
    for source in ["Binance", "Bybit", "OKX", "Upbit"]:
        recent = grouped[source][:10]
        if recent:
            output.append(f"📊 【{source}】最新上幣：")
            for r in recent:
                dt = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S").astimezone(TW)
                time_str = dt.strftime("%m-%d %H:%M")
                output.append(f"- {time_str} - {clean_symbol(r['symbol'])}")
            output.append("")

    text = "\n".join([line for line in output if line.strip()])
    if not text:
        text = "📭 無新增紀錄"
    await update.message.reply_text(text)

async def force_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await check_all()
    await update.message.reply_text("✅ 手動比對完成")

async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    records = load_records()
    total = len(records)
    latest = defaultdict(lambda: None)
    for r in sorted(records, key=lambda x: x["timestamp"], reverse=True):
        if not latest[r["source"]]:
            latest[r["source"]] = r
    lines = [f"📦 總筆數：{total}"]
    for source in ["Binance", "Bybit", "OKX", "Upbit"]:
        r = latest.get(source)
        if r:
            lines.append(f"📌 {source} 最後：{r['timestamp']} - {clean_symbol(r['symbol'])}")
    await update.message.reply_text("\n".join(lines))

async def show_record(update: Update, context: ContextTypes.DEFAULT_TYPE):
    records = load_records()
    last = records[-5:]
    lines = [f"{r['timestamp']} - {r['source']} - {r['symbol']}" for r in last]
    await update.message.reply_text("🧾 最後 5 筆紀錄：\n" + "\n".join(lines) if lines else "📭 沒有資料")

async def main():
    initialize_record_file()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("check", check_command))
    app.add_handler(CommandHandler("forcecheck", force_check))
    app.add_handler(CommandHandler("debug", debug_command))
    app.add_handler(CommandHandler("showrecord", show_record))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_all, "interval", minutes=3)
    scheduler.start()

    print("✅ 監控機器人已啟動")
    await notify("✅ 監控機器人 v4.1.1 已啟動完成")
    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())
