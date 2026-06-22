#!/usr/bin/env python3
"""
Guild Glory Credit Shop - Telegram Bot
Full version with mandatory channel join (@DGDRIFT and @DRIFTARMYFF)
Bot Token and Admin ID are preset.
"""

import asyncio
import logging
import os
import re
import random
import string
from datetime import datetime
from typing import Dict, Optional, List

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode

import aiosqlite

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================= Configuration =================
BOT_TOKEN = "8624279554:AAGhy5cv3daqHHuXKEBzz9F3B2In6hzW13g"
ADMIN_IDS = [7514425221]

# Channel to join (without @)
REQUIRED_CHANNELS = ["FLASHFF_07"]

# Conversation States
(
    ADD_BALANCE_AMOUNT,
    ADD_BALANCE_SCREENSHOT,
    ADD_BALANCE_UTR,
    BUY_CREDIT_SCREENSHOT,
    BUY_CREDIT_UTR,
    ADMIN_SET_UPI,
    ADMIN_SET_QR,
    ADMIN_BROADCAST,
    ADMIN_MANUAL_BALANCE_USER,
    ADMIN_MANUAL_BALANCE_AMOUNT,
    ADMIN_ADD_CODE,
    ADMIN_DELETE_CODE,
) = range(12)

DB_PATH = "guild_glory.db"

# ================= Database Setup =================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                full_name TEXT,
                username TEXT,
                join_date TIMESTAMP,
                referrer_id INTEGER,
                wallet_balance REAL DEFAULT 0,
                credit_balance INTEGER DEFAULT 0,
                total_referrals INTEGER DEFAULT 0,
                referral_earnings REAL DEFAULT 0,
                total_credits_purchased INTEGER DEFAULT 0,
                is_banned BOOLEAN DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS payment_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                type TEXT,
                amount REAL,
                credits INTEGER,
                screenshot_file_id TEXT,
                utr TEXT,
                status TEXT,
                created_at TIMESTAMP,
                processed_at TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS referral_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referred_user_id INTEGER,
                amount REAL,
                created_at TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                type TEXT,
                amount REAL,
                credits INTEGER,
                description TEXT,
                timestamp TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                reward_credits INTEGER DEFAULT 1,
                used_by_user_id INTEGER,
                used_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                code TEXT UNIQUE NOT NULL,
                generated_at TIMESTAMP,
                used_on_site BOOLEAN DEFAULT 0
            )
        """)
        await db.execute(
            "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
            ("upi_id", "admin@upi")
        )
        await db.execute(
            "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
            ("qr_file_id", "")
        )
        await db.commit()

# ================= Helper Functions =================
async def check_channels_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user has joined all required channels"""
    for channel in REQUIRED_CHANNELS:
        try:
            chat_member = await context.bot.get_chat_member(chat_id=f"@{channel}", user_id=user_id)
            if chat_member.status in ["left", "kicked"]:
                return False
        except Exception as e:
            logger.error(f"Could not check channel {channel}: {e}")
            return False
    return True

async def get_or_create_user(telegram_id: int, full_name: str, username: str, referrer_id: int = None) -> Dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)) as cursor:
            user = await cursor.fetchone()
        if user:
            columns = [desc[0] for desc in cursor.description]
            return dict(zip(columns, user))
        join_date = datetime.now()
        await db.execute("""
            INSERT INTO users (telegram_id, full_name, username, join_date, referrer_id)
            VALUES (?, ?, ?, ?, ?)
        """, (telegram_id, full_name, username, join_date, referrer_id))
        await db.commit()
        if referrer_id and referrer_id != telegram_id:
            async with db.execute("SELECT * FROM users WHERE telegram_id = ?", (referrer_id,)) as cursor:
                referrer = await cursor.fetchone()
            if referrer:
                await db.execute("""
                    UPDATE users 
                    SET wallet_balance = wallet_balance + 1,
                        total_referrals = total_referrals + 1,
                        referral_earnings = referral_earnings + 1
                    WHERE telegram_id = ?
                """, (referrer_id,))
                await db.execute("""
                    INSERT INTO referral_history (referrer_id, referred_user_id, amount, created_at)
                    VALUES (?, ?, ?, ?)
                """, (referrer_id, telegram_id, 1, datetime.now()))
                await db.execute("""
                    INSERT INTO transactions (user_id, type, amount, description, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                """, (referrer_id, "referral_earning", 1, f"Referral reward for user {telegram_id}", datetime.now()))
                await db.commit()
        async with db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)) as cursor:
            user = await cursor.fetchone()
            columns = [desc[0] for desc in cursor.description]
            return dict(zip(columns, user))

async def update_user_balance(telegram_id: int, amount: float, operation: str = "add"):
    async with aiosqlite.connect(DB_PATH) as db:
        if operation == "add":
            await db.execute("UPDATE users SET wallet_balance = wallet_balance + ? WHERE telegram_id = ?", (amount, telegram_id))
        else:
            await db.execute("UPDATE users SET wallet_balance = wallet_balance - ? WHERE telegram_id = ?", (amount, telegram_id))
        await db.commit()

async def update_user_credits(telegram_id: int, credits: int, operation: str = "add"):
    async with aiosqlite.connect(DB_PATH) as db:
        if operation == "add":
            await db.execute("UPDATE users SET credit_balance = credit_balance + ?, total_credits_purchased = total_credits_purchased + ? WHERE telegram_id = ?", 
                           (credits, credits, telegram_id))
        else:
            await db.execute("UPDATE users SET credit_balance = credit_balance - ? WHERE telegram_id = ?", (credits, telegram_id))
        await db.commit()

async def get_config(key: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM config WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def set_config(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))
        await db.commit()

async def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

async def is_banned(telegram_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT is_banned FROM users WHERE telegram_id = ?", (telegram_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] == 1 if row else False

def generate_unique_code() -> str:
    random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"GLORY-{random_part}"

async def is_code_unique(code: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM user_codes WHERE code = ?", (code,)) as cursor:
            return await cursor.fetchone() is None

# ================= Keyboards =================
async def get_main_keyboard(telegram_id: int):
    buttons = [
        [InlineKeyboardButton("💰 Add Balance", callback_data="add_balance")],
        [InlineKeyboardButton("🎮 Buy Credit", callback_data="buy_credit")],
        [InlineKeyboardButton("🎁 Get Code", callback_data="get_code")],
        [InlineKeyboardButton("👥 My Referral", callback_data="my_referral")],
        [InlineKeyboardButton("👤 Profile", callback_data="profile")],
        [InlineKeyboardButton("📞 Contact Admin", callback_data="contact_admin")],
    ]
    if await is_admin(telegram_id):
        buttons.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
    return InlineKeyboardMarkup(buttons)

def get_admin_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("💳 Payment Settings", callback_data="admin_payment_settings")],
        [InlineKeyboardButton("📝 Payment Requests", callback_data="admin_payment_requests")],
        [InlineKeyboardButton("👥 User Management", callback_data="admin_user_management")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("💰 Manual Balance", callback_data="admin_manual_balance")],
        [InlineKeyboardButton("🎫 Code Management", callback_data="admin_code_management")],
        [InlineKeyboardButton("📊 Analytics", callback_data="admin_analytics")],
        [InlineKeyboardButton("🔨 Ban/Unban User", callback_data="admin_ban_user")],
        [InlineKeyboardButton("📜 Transaction History", callback_data="admin_transactions")],
        [InlineKeyboardButton("🔙 Back to User Menu", callback_data="back_to_main")],
    ]
    return InlineKeyboardMarkup(keyboard)

# ================= Channel join message =================
async def send_join_channels_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🌟 <b>Guild Glory Credit Shop</b>\n\n"
    text += "To use this bot, you must join our official channels:\n\n"
    for ch in REQUIRED_CHANNELS:
        text += f"👉 <a href='https://t.me/{ch}'>@{ch}</a>\n"
    text += "\nAfter joining, click the button below to start."
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Check Membership", callback_data="check_join")]
    ])
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard, disable_web_page_preview=True)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard, disable_web_page_preview=True)

# ================= Start =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    telegram_id = user.id
    full_name = user.full_name
    username = user.username or ""
    
    # Check for referral parameter
    referrer_id = None
    if context.args and len(context.args) > 0:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                referrer_id = int(arg.split("_")[1])
            except ValueError:
                pass
    
    # Create user if not exists (so referral works even before joining channels)
    await get_or_create_user(telegram_id, full_name, username, referrer_id)
    
    # Check channel membership
    if not await check_channels_membership(telegram_id, context):
        await send_join_channels_message(update, context)
        return
    
    # User already joined channels, proceed to main menu
    await show_main_menu(update, context, telegram_id)

async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback when user clicks 'Check Membership'"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if await check_channels_membership(user_id, context):
        # User has joined all channels
        await show_main_menu(update, context, user_id)
    else:
        # Still not joined
        await send_join_channels_message(update, context)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Display the main menu after successful join"""
    user_data = await get_or_create_user(user_id, "", "", None)
    if user_data["is_banned"]:
        await context.bot.send_message(chat_id=user_id, text="🚫 You are banned from using this bot. Contact admin for support.")
        return
    
    bot_info = await context.bot.get_me()
    bot_username = bot_info.username
    referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    
    welcome_text = (
        f"╔══════════════════════╗\n"
        f"🎮 Guild Glory Credit Shop\n"
        f"╚══════════════════════╝\n\n"
        f"👋 Welcome, {user_data['full_name']}!\n\n"
        f"💵 Wallet Balance: ₹{user_data['wallet_balance']:.0f}\n"
        f"🎮 Credits: {user_data['credit_balance']}\n\n"
        f"🔗 Referral Link: {referral_link}\n\n"
        f"💡 Earn ₹1 for every friend who joins!\n\n"
        f"MADE BY- @DG_DRIFT\n"
        f"Credit use - http://ffglory.pro"
    )
    
    if update.callback_query:
        await update.callback_query.edit_message_text(welcome_text, reply_markup=await get_main_keyboard(user_id), parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(welcome_text, reply_markup=await get_main_keyboard(user_id), parse_mode=ParseMode.HTML)

# ================= Main Menu Callback =================
async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    # Check ban
    if await is_banned(user_id):
        await query.edit_message_text("🚫 You are banned from using this bot.")
        return
    
    # Check channel membership before allowing any action
    if not await check_channels_membership(user_id, context):
        await send_join_channels_message(update, context)
        return
    
    data = query.data
    
    if data == "add_balance":
        await query.edit_message_text(
            "💰 <b>Add Balance</b>\n\nPlease enter the amount (Min ₹10):\nSend /cancel to cancel.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])
        )
        return ADD_BALANCE_AMOUNT
        
    elif data == "buy_credit":
        upi_id = await get_config("upi_id")
        qr_file_id = await get_config("qr_file_id")
        message = (
            "🎮 <b>Buy Credit</b>\n\n1 Credit = ₹100\n\n"
            f"🏦 UPI ID: <code>{upi_id}</code>\n\n"
            "After payment, send screenshot and UTR."
        )
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
        if qr_file_id:
            await query.message.reply_photo(qr_file_id, caption=message, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
            await query.delete_message()
        else:
            await query.edit_message_text(message, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
        return BUY_CREDIT_SCREENSHOT
        
    elif data == "get_code":
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT wallet_balance, credit_balance FROM users WHERE telegram_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
        if not row:
            return
        wallet, credits = row
        if wallet < 100 and credits < 1:
            await query.edit_message_text(
                "❌ Not eligible. Need ₹100 in wallet OR at least 1 credit.\n\nAdd balance or buy credit first.",
                reply_markup=await get_main_keyboard(user_id)
            )
            return
        for _ in range(5):
            code = generate_unique_code()
            if await is_code_unique(code):
                break
        else:
            await query.edit_message_text("Error generating code. Try again later.")
            return
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO user_codes (user_id, code, generated_at) VALUES (?, ?, ?)",
                           (user_id, code, datetime.now()))
            await db.commit()
        await query.edit_message_text(
            f"🎁 <b>Your Exclusive Code</b>\n\n<code>{code}</code>\n\n"
            "Use it on our website:\n👉 <b>ffglory.pro</b>\n\n(One‑time use, valid 7 days)",
            parse_mode=ParseMode.HTML,
            reply_markup=await get_main_keyboard(user_id)
        )
        
    elif data == "my_referral":
        await show_referral_info(update, context, user_id)
        await query.delete_message()
        
    elif data == "profile":
        await show_profile(update, context, user_id)
        await query.delete_message()
        
    elif data == "contact_admin":
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📞 Contact Admin", url="https://t.me/dg_drift")]])
        await query.edit_message_text("Click below to contact admin:", reply_markup=keyboard)
        
    elif data == "admin_panel":
        if not await is_admin(user_id):
            await query.edit_message_text("Access denied.")
            return
        await query.edit_message_text(
            "🔐 <b>Admin Control Panel</b>\n\nSelect an option:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_admin_main_keyboard()
        )
        
    elif data == "back_to_main":
        user_data = await get_or_create_user(user_id, "", "", None)
        bot_info = await context.bot.get_me()
        referral_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"
        welcome_text = (
            f"╔══════════════════════╗\n🎮 Guild Glory Credit Shop\n╚══════════════════════╝\n\n"
            f"👋 Welcome back!\n\n💵 Balance: ₹{user_data['wallet_balance']:.0f}\n"
            f"🎮 Credits: {user_data['credit_balance']}\n\n🔗 Referral: {referral_link}\n\n"
            f"💡 Earn ₹1 per referral!\n\nMADE BY- @DG_DRIFT\nCredit use - http://ffglory.pro"
        )
        await query.edit_message_text(welcome_text, reply_markup=await get_main_keyboard(user_id), parse_mode=ParseMode.HTML)

# ================= Referral & Profile =================
async def show_referral_info(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT total_referrals, referral_earnings FROM users WHERE telegram_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
        async with db.execute("SELECT referred_user_id, amount, created_at FROM referral_history WHERE referrer_id = ? ORDER BY created_at DESC LIMIT 10", (user_id,)) as cursor:
            history = await cursor.fetchall()
    total_ref = user[0] if user else 0
    earnings = user[1] if user else 0
    bot_info = await context.bot.get_me()
    referral_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"
    history_text = "\n".join([f"• User {h[0]} - ₹{h[1]} - {h[2][:10]}" for h in history]) or "No referrals yet."
    message = (
        f"👥 <b>My Referral Stats</b>\n\n🔗 Your Link:\n<code>{referral_link}</code>\n\n"
        f"📊 Total Referrals: {total_ref}\n💰 Earnings: ₹{earnings}\n\n<b>Recent:</b>\n{history_text}"
    )
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]])
    await context.bot.send_message(chat_id=user_id, text=message, parse_mode=ParseMode.HTML, reply_markup=keyboard)

async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT full_name, username, telegram_id, wallet_balance, total_referrals,
                   referral_earnings, credit_balance, total_credits_purchased, join_date
            FROM users WHERE telegram_id = ?
        """, (user_id,)) as cursor:
            user = await cursor.fetchone()
    if user:
        message = (
            f"👤 <b>User Profile</b>\n\n📛 Name: {user[0]}\n🔖 @{user[1] if user[1] else 'N/A'}\n"
            f"🆔 ID: {user[2]}\n💵 Wallet: ₹{user[3]:.0f}\n🎮 Credits: {user[6]}\n"
            f"👥 Referrals: {user[4]}\n💰 Referral Earnings: ₹{user[5]}\n"
            f"🎫 Credits Purchased: {user[7]}\n📅 Joined: {user[8][:19]}"
        )
    else:
        message = "Profile not found."
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]])
    await context.bot.send_message(chat_id=user_id, text=message, parse_mode=ParseMode.HTML, reply_markup=keyboard)

# ================= Add Balance Conversations =================
async def add_balance_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if text == "/cancel":
        await update.message.reply_text("Cancelled.", reply_markup=await get_main_keyboard(user_id))
        return ConversationHandler.END
    try:
        amount = float(text)
        if amount < 10:
            await update.message.reply_text("Minimum ₹10. Enter amount:")
            return ADD_BALANCE_AMOUNT
    except ValueError:
        await update.message.reply_text("Enter a valid number.")
        return ADD_BALANCE_AMOUNT
    context.user_data["add_balance_amount"] = amount
    upi_id = await get_config("upi_id")
    qr_file_id = await get_config("qr_file_id")
    message = f"💰 Pay ₹{amount} to {upi_id}\nSend payment screenshot."
    if qr_file_id:
        await update.message.reply_photo(qr_file_id, caption=message, parse_mode=ParseMode.HTML,
                                         reply_markup=ReplyKeyboardMarkup([["/cancel"]], resize_keyboard=True))
    else:
        await update.message.reply_text(message, parse_mode=ParseMode.HTML,
                                        reply_markup=ReplyKeyboardMarkup([["/cancel"]], resize_keyboard=True))
    return ADD_BALANCE_SCREENSHOT

async def add_balance_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if update.message.text and update.message.text == "/cancel":
        await update.message.reply_text("Cancelled.", reply_markup=await get_main_keyboard(user_id))
        return ConversationHandler.END
    if not update.message.photo:
        await update.message.reply_text("Please send a screenshot image.")
        return ADD_BALANCE_SCREENSHOT
    photo_id = update.message.photo[-1].file_id
    context.user_data["add_balance_screenshot"] = photo_id
    await update.message.reply_text("Send UTR/Transaction ID:", reply_markup=ReplyKeyboardMarkup([["/cancel"]], resize_keyboard=True))
    return ADD_BALANCE_UTR

async def add_balance_utr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if text == "/cancel":
        await update.message.reply_text("Cancelled.", reply_markup=await get_main_keyboard(user_id))
        return ConversationHandler.END
    amount = context.user_data.get("add_balance_amount")
    screenshot = context.user_data.get("add_balance_screenshot")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO payment_requests (user_id, type, amount, screenshot_file_id, utr, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, "add_balance", amount, screenshot, text, "pending", datetime.now()))
        await db.commit()
    for admin_id in ADMIN_IDS:
        await context.bot.send_message(admin_id, f"💰 New Deposit Request\nUser: {user_id}\nAmount: ₹{amount}\nUTR: {text}\nUse /admin to approve.")
    await update.message.reply_text("Request sent! Admin will approve soon.", reply_markup=await get_main_keyboard(user_id))
    context.user_data.clear()
    return ConversationHandler.END

# ================= Buy Credit Conversations =================
async def buy_credit_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if update.message.text and update.message.text == "/cancel":
        await update.message.reply_text("Cancelled.", reply_markup=await get_main_keyboard(user_id))
        return ConversationHandler.END
    if not update.message.photo:
        await update.message.reply_text("Please send a screenshot.")
        return BUY_CREDIT_SCREENSHOT
    photo_id = update.message.photo[-1].file_id
    context.user_data["buy_credit_screenshot"] = photo_id
    await update.message.reply_text("Send UTR:", reply_markup=ReplyKeyboardMarkup([["/cancel"]], resize_keyboard=True))
    return BUY_CREDIT_UTR

async def buy_credit_utr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if text == "/cancel":
        await update.message.reply_text("Cancelled.", reply_markup=await get_main_keyboard(user_id))
        return ConversationHandler.END
    screenshot = context.user_data.get("buy_credit_screenshot")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO payment_requests (user_id, type, amount, credits, screenshot_file_id, utr, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, "buy_credit", 100, 1, screenshot, text, "pending", datetime.now()))
        await db.commit()
    for admin_id in ADMIN_IDS:
        await context.bot.send_message(admin_id, f"🎮 Credit Purchase\nUser: {user_id}\nAmount: ₹100\nUTR: {text}")
    await update.message.reply_text("Purchase request sent.", reply_markup=await get_main_keyboard(user_id))
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text("Cancelled.", reply_markup=await get_main_keyboard(user_id))
    return ConversationHandler.END

# ================= Admin Handlers =================
async def show_payment_requests(query, context):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, user_id, type, amount, credits, screenshot_file_id, utr, created_at FROM payment_requests WHERE status='pending' ORDER BY created_at DESC") as cursor:
            requests = await cursor.fetchall()
    if not requests:
        await query.edit_message_text("No pending requests.", reply_markup=get_admin_main_keyboard())
        return
    for req in requests:
        req_id, user_id, typ, amount, credits, screenshot, utr, created = req
        msg = f"Request #{req_id}\nUser: {user_id}\nType: {typ}\nAmount: ₹{amount}\nUTR: {utr}"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{req_id}"),
             InlineKeyboardButton("❌ Reject", callback_data=f"reject_{req_id}")]
        ])
        if screenshot:
            await context.bot.send_photo(query.message.chat_id, screenshot, caption=msg, reply_markup=keyboard)
        else:
            await context.bot.send_message(query.message.chat_id, msg, reply_markup=keyboard)
        await query.delete_message()

async def process_payment_approval(query, context, request_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, type, amount, credits FROM payment_requests WHERE id=?", (request_id,)) as cursor:
            req = await cursor.fetchone()
        if not req:
            await query.edit_message_text("Request not found.")
            return
        user_id, typ, amount, credits = req
        await db.execute("UPDATE payment_requests SET status='approved', processed_at=? WHERE id=?", (datetime.now(), request_id))
        if typ == "add_balance":
            await update_user_balance(user_id, amount, "add")
            desc = f"Deposit ₹{amount} approved"
        else:
            await update_user_credits(user_id, credits or 1, "add")
            desc = f"Purchase of {credits or 1} credit(s) approved"
        await db.execute("INSERT INTO transactions (user_id, type, amount, credits, description, timestamp) VALUES (?,?,?,?,?,?)",
                         (user_id, typ, amount, credits or 0, desc, datetime.now()))
        await db.commit()
    await query.edit_message_text(f"✅ Request #{request_id} approved.")
    await context.bot.send_message(user_id, f"✅ Your payment has been approved!\n{desc}")

async def process_payment_rejection(query, context, request_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM payment_requests WHERE id=?", (request_id,)) as cursor:
            req = await cursor.fetchone()
        if not req:
            await query.edit_message_text("Request not found.")
            return
        user_id = req[0]
        await db.execute("UPDATE payment_requests SET status='rejected', processed_at=? WHERE id=?", (datetime.now(), request_id))
        await db.commit()
    await query.edit_message_text(f"❌ Request #{request_id} rejected.")
    await context.bot.send_message(user_id, "❌ Your payment request was rejected. Contact admin.")

async def show_user_list(query, context):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT telegram_id, full_name, wallet_balance, credit_balance FROM users LIMIT 20") as cursor:
            users = await cursor.fetchall()
    msg = "👥 Users:\n"
    for u in users:
        msg += f"{u[0]} | {u[1]} | ₹{u[2]} | {u[3]} credits\n"
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]]))

async def show_analytics(query, context):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        total_users = (await cursor.fetchone())[0]
        cursor = await db.execute("SELECT SUM(wallet_balance) FROM users")
        total_balance = (await cursor.fetchone())[0] or 0
        cursor = await db.execute("SELECT SUM(total_credits_purchased) FROM users")
        total_credits = (await cursor.fetchone())[0] or 0
        cursor = await db.execute("SELECT SUM(amount) FROM payment_requests WHERE type='add_balance' AND status='approved'")
        total_deposits = (await cursor.fetchone())[0] or 0
        cursor = await db.execute("SELECT SUM(amount) FROM payment_requests WHERE type='buy_credit' AND status='approved'")
        total_purchases = (await cursor.fetchone())[0] or 0
        cursor = await db.execute("SELECT SUM(referral_earnings) FROM users")
        total_referral_paid = (await cursor.fetchone())[0] or 0
        cursor = await db.execute("SELECT COUNT(*) FROM user_codes WHERE used_on_site=1")
        codes_used = (await cursor.fetchone())[0] or 0
        cursor = await db.execute("SELECT COUNT(*) FROM user_codes")
        total_codes = (await cursor.fetchone())[0] or 0
    msg = (
        f"📊 <b>Analytics</b>\n\n"
        f"👥 Total Users: {total_users}\n"
        f"💰 Total Wallet: ₹{total_balance:.0f}\n"
        f"🎮 Credits Sold: {total_credits}\n"
        f"💵 Deposits: ₹{total_deposits:.0f}\n"
        f"🛒 Purchases: ₹{total_purchases:.0f}\n"
        f"👥 Referral Paid: ₹{total_referral_paid:.0f}\n"
        f"🎫 Codes: {codes_used}/{total_codes} used"
    )
    await query.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]]))

async def show_all_transactions(query, context):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, type, amount, credits, description, timestamp FROM transactions ORDER BY timestamp DESC LIMIT 20") as cursor:
            txs = await cursor.fetchall()
    msg = "📜 Recent Transactions:\n"
    for t in txs:
        msg += f"{t[5][:16]} | User {t[0]} | {t[1]} | ₹{t[2]} | {t[3]} credits\n{t[4]}\n"
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]]))

async def admin_set_upi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_admin(user_id):
        return ConversationHandler.END
    text = update.message.text.strip()
    if text == "/cancel":
        await update.message.reply_text("Cancelled.", reply_markup=get_admin_main_keyboard())
        return ConversationHandler.END
    await set_config("upi_id", text)
    await update.message.reply_text(f"✅ UPI ID updated to: <code>{text}</code>", parse_mode=ParseMode.HTML, reply_markup=get_admin_main_keyboard())
    return ConversationHandler.END

async def admin_set_qr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_admin(user_id):
        return ConversationHandler.END
    if update.message.text and update.message.text == "/cancel":
        await update.message.reply_text("Cancelled.", reply_markup=get_admin_main_keyboard())
        return ConversationHandler.END
    if not update.message.photo:
        await update.message.reply_text("Please send a photo for QR code.")
        return ADMIN_SET_QR
    file_id = update.message.photo[-1].file_id
    await set_config("qr_file_id", file_id)
    await update.message.reply_text("✅ QR Code updated successfully!", reply_markup=get_admin_main_keyboard())
    return ConversationHandler.END

async def admin_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_admin(user_id):
        return ConversationHandler.END
    text = update.message.text
    if text == "/cancel":
        await update.message.reply_text("Cancelled.", reply_markup=get_admin_main_keyboard())
        return ConversationHandler.END
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT telegram_id FROM users") as cursor:
            users = await cursor.fetchall()
    success = 0
    for u in users:
        try:
            await context.bot.send_message(u[0], f"📢 Announcement\n{text}")
            success += 1
        except:
            pass
    await update.message.reply_text(f"Broadcast sent to {success} users.", reply_markup=get_admin_main_keyboard())
    return ConversationHandler.END

async def admin_manual_balance_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_admin(user_id):
        return ConversationHandler.END
    text = update.message.text.strip()
    if text == "/cancel":
        await update.message.reply_text("Cancelled.", reply_markup=get_admin_main_keyboard())
        return ConversationHandler.END
    try:
        target = int(text)
        context.user_data["manual_target"] = target
        await update.message.reply_text(f"User {target}\nSend amount (+ for add, - for subtract):")
        return ADMIN_MANUAL_BALANCE_AMOUNT
    except:
        await update.message.reply_text("Invalid ID.")
        return ADMIN_MANUAL_BALANCE_USER

async def admin_manual_balance_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_admin(user_id):
        return ConversationHandler.END
    text = update.message.text.strip()
    if text == "/cancel":
        await update.message.reply_text("Cancelled.", reply_markup=get_admin_main_keyboard())
        return ConversationHandler.END
    try:
        amount = float(text)
        target = context.user_data.get("manual_target")
        if amount > 0:
            await update_user_balance(target, amount, "add")
            desc = f"Admin added ₹{amount}"
        else:
            await update_user_balance(target, abs(amount), "subtract")
            desc = f"Admin subtracted ₹{abs(amount)}"
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO transactions (user_id, type, amount, description, timestamp) VALUES (?,?,?,?,?)",
                             (target, "manual_balance", abs(amount), desc, datetime.now()))
            await db.commit()
        await update.message.reply_text(f"✅ Balance updated for {target}.", reply_markup=get_admin_main_keyboard())
        await context.bot.send_message(target, f"💰 Your balance has been {'increased' if amount>0 else 'decreased'} by ₹{abs(amount)}.")
    except:
        await update.message.reply_text("Invalid amount.")
        return ADMIN_MANUAL_BALANCE_AMOUNT
    context.user_data.clear()
    return ConversationHandler.END

async def admin_ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_admin(user_id):
        return
    text = update.message.text.strip()
    if text == "/cancel":
        await update.message.reply_text("Cancelled.", reply_markup=get_admin_main_keyboard())
        return
    try:
        target = int(text)
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT is_banned FROM users WHERE telegram_id=?", (target,)) as cursor:
                row = await cursor.fetchone()
            if not row:
                await update.message.reply_text("User not found.")
                return
            new_status = not row[0]
            await db.execute("UPDATE users SET is_banned=? WHERE telegram_id=?", (new_status, target))
            await db.commit()
        await update.message.reply_text(f"User {target} {'banned' if new_status else 'unbanned'}.", reply_markup=get_admin_main_keyboard())
        await context.bot.send_message(target, f"You have been {'banned' if new_status else 'unbanned'}.")
    except:
        await update.message.reply_text("Invalid ID.")

# ================= Admin Code Management =================
async def admin_add_internal_code_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_admin(user_id):
        return ConversationHandler.END
    text = update.message.text.strip()
    if text == "/cancel":
        await update.message.reply_text("Cancelled.", reply_markup=get_admin_main_keyboard())
        return ConversationHandler.END
    
    if "=" in text:
        last_eq = text.rfind("=")
        code_part = text[:last_eq]
        reward_part = text[last_eq+1:]
        if reward_part.isdigit():
            reward = int(reward_part)
            code = code_part.strip()
        else:
            reward = 1
            code = text
    else:
        reward = 1
        code = text
    
    if not code:
        await update.message.reply_text("❌ Code cannot be empty.")
        return ADMIN_ADD_CODE
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM codes WHERE code = ?", (code,)) as cursor:
            existing = await cursor.fetchone()
        if existing:
            await update.message.reply_text("❌ Code already exists.")
            return ADMIN_ADD_CODE
        await db.execute("INSERT INTO codes (code, reward_credits) VALUES (?, ?)", (code, reward))
        await db.commit()
    await update.message.reply_text(f"✅ Code added!\nCode: <code>{code}</code>\nReward: {reward} credit(s)", parse_mode=ParseMode.HTML, reply_markup=get_admin_main_keyboard())
    return ConversationHandler.END

async def admin_delete_internal_code_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await is_admin(query.from_user.id):
        await query.edit_message_text("Access denied.")
        return ConversationHandler.END
    data = query.data
    if data.startswith("del_internal_"):
        code_id = int(data.split("_")[2])
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM codes WHERE id=?", (code_id,))
            await db.commit()
        await query.edit_message_text("✅ Code deleted.", reply_markup=get_admin_main_keyboard())
    else:
        await query.edit_message_text("Cancelled.", reply_markup=get_admin_main_keyboard())
    return ConversationHandler.END

async def admin_list_internal_codes(query, context):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT code, reward_credits, used_by_user_id FROM codes ORDER BY id DESC") as cursor:
            codes = await cursor.fetchall()
    if not codes:
        await query.edit_message_text("No internal codes.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_code_management")]]))
        return
    msg = "🎫 Internal Codes:\n"
    for c in codes:
        status = "✅ Unused" if c[2] is None else f"❌ Used by {c[2]}"
        msg += f"<code>{c[0]}</code> | +{c[1]} credit | {status}\n"
    await query.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_code_management")]]))

async def admin_view_user_codes(query, context):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT code, user_id, generated_at, used_on_site FROM user_codes ORDER BY generated_at DESC LIMIT 50") as cursor:
            codes = await cursor.fetchall()
    if not codes:
        await query.edit_message_text("No user codes.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_code_management")]]))
        return
    msg = "👥 User Codes:\n"
    for c in codes:
        used = "✅ Used" if c[3] else "⏳ Unused"
        msg += f"<code>{c[0]}</code> | User {c[1]} | {c[2][:16]} | {used}\n"
    await query.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_code_management")]]))

# ================= Admin Callback Router =================
async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not await is_admin(user_id):
        await query.edit_message_text("Access denied.")
        return
    data = query.data

    if data == "admin_payment_settings":
        upi = await get_config("upi_id")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏦 Update UPI", callback_data="admin_set_upi")],
            [InlineKeyboardButton("🖼️ Update QR", callback_data="admin_set_qr")],
            [InlineKeyboardButton("🔙 Back", callback_data="admin_back")]
        ])
        await query.edit_message_text(f"Current UPI: <code>{upi}</code>", parse_mode=ParseMode.HTML, reply_markup=kb)
    elif data == "admin_payment_requests":
        await show_payment_requests(query, context)
    elif data == "admin_user_management":
        await show_user_list(query, context)
    elif data == "admin_broadcast":
        await query.edit_message_text("Send broadcast message:")
        return ADMIN_BROADCAST
    elif data == "admin_manual_balance":
        await query.edit_message_text("Send user Telegram ID:")
        return ADMIN_MANUAL_BALANCE_USER
    elif data == "admin_code_management":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Internal Code", callback_data="admin_add_internal_code")],
            [InlineKeyboardButton("📋 List Internal Codes", callback_data="admin_list_internal_codes")],
            [InlineKeyboardButton("🗑 Delete Internal Code", callback_data="admin_delete_internal_code")],
            [InlineKeyboardButton("📋 View User Codes", callback_data="admin_view_user_codes")],
            [InlineKeyboardButton("🔙 Back", callback_data="admin_back")]
        ])
        await query.edit_message_text("Code Management", reply_markup=kb)
    elif data == "admin_analytics":
        await show_analytics(query, context)
    elif data == "admin_ban_user":
        await query.edit_message_text("Send user ID to ban/unban:")
        return "awaiting_ban_user_id"
    elif data == "admin_transactions":
        await show_all_transactions(query, context)
    elif data == "admin_back":
        await query.edit_message_text("Admin Panel", reply_markup=get_admin_main_keyboard(), parse_mode=ParseMode.HTML)
    elif data == "admin_set_upi":
        await query.edit_message_text("Send new UPI ID (any text):", parse_mode=ParseMode.HTML)
        return ADMIN_SET_UPI
    elif data == "admin_set_qr":
        await query.edit_message_text("Send new QR code image:", parse_mode=ParseMode.HTML)
        return ADMIN_SET_QR
    elif data == "admin_add_internal_code":
        await query.edit_message_text("Send your code")
        return ADMIN_ADD_CODE
    elif data == "admin_list_internal_codes":
        await admin_list_internal_codes(query, context)
    elif data == "admin_delete_internal_code":
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT id, code FROM codes") as cursor:
                codes = await cursor.fetchall()
        if not codes:
            await query.edit_message_text("No codes to delete.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_code_management")]]))
            return ConversationHandler.END
        kb = []
        for cid, code in codes:
            kb.append([InlineKeyboardButton(f"❌ {code}", callback_data=f"del_internal_{cid}")])
        kb.append([InlineKeyboardButton("🔙 Cancel", callback_data="admin_code_management")])
        await query.edit_message_text("Select code to delete:", reply_markup=InlineKeyboardMarkup(kb))
        return ADMIN_DELETE_CODE
    elif data == "admin_view_user_codes":
        await admin_view_user_codes(query, context)
    elif data.startswith("approve_"):
        await process_payment_approval(query, context, int(data.split("_")[1]))
    elif data.startswith("reject_"):
        await process_payment_rejection(query, context, int(data.split("_")[1]))

async def cancel_admin_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if await is_admin(user_id):
        await update.message.reply_text("Cancelled.", reply_markup=get_admin_main_keyboard())
    return ConversationHandler.END

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")
    if update and update.effective_chat:
        await context.bot.send_message(update.effective_chat.id, "⚠️ An error occurred. Please try again later.")

# ================= Main =================
def main():
    asyncio.run(init_db())
    app = Application.builder().token(BOT_TOKEN).build()

    # User conversations
    add_balance_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(main_menu_callback, pattern="^add_balance$")],
        states={
            ADD_BALANCE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_balance_amount)],
            ADD_BALANCE_SCREENSHOT: [MessageHandler(filters.PHOTO, add_balance_screenshot)],
            ADD_BALANCE_UTR: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_balance_utr)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )
    buy_credit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(main_menu_callback, pattern="^buy_credit$")],
        states={
            BUY_CREDIT_SCREENSHOT: [MessageHandler(filters.PHOTO, buy_credit_screenshot)],
            BUY_CREDIT_UTR: [MessageHandler(filters.TEXT & ~filters.COMMAND, buy_credit_utr)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )

    # Admin conversations
    admin_set_upi_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^admin_set_upi$")],
        states={ADMIN_SET_UPI: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_upi)]},
        fallbacks=[CommandHandler("cancel", cancel_admin_conversation)],
    )
    admin_set_qr_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^admin_set_qr$")],
        states={ADMIN_SET_QR: [MessageHandler(filters.PHOTO, admin_set_qr)]},
        fallbacks=[CommandHandler("cancel", cancel_admin_conversation)],
    )
    admin_broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^admin_broadcast$")],
        states={ADMIN_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_message)]},
        fallbacks=[CommandHandler("cancel", cancel_admin_conversation)],
    )
    admin_manual_balance_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^admin_manual_balance$")],
        states={
            ADMIN_MANUAL_BALANCE_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_manual_balance_user)],
            ADMIN_MANUAL_BALANCE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_manual_balance_amount)],
        },
        fallbacks=[CommandHandler("cancel", cancel_admin_conversation)],
    )
    admin_ban_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^admin_ban_user$")],
        states={"awaiting_ban_user_id": [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_ban_user)]},
        fallbacks=[CommandHandler("cancel", cancel_admin_conversation)],
    )
    admin_add_code_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^admin_add_internal_code$")],
        states={ADMIN_ADD_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_internal_code_receive)]},
        fallbacks=[CommandHandler("cancel", cancel_admin_conversation)],
    )
    admin_delete_code_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^admin_delete_internal_code$")],
        states={ADMIN_DELETE_CODE: [CallbackQueryHandler(admin_delete_internal_code_confirm, pattern="^del_internal_")]},
        fallbacks=[CommandHandler("cancel", cancel_admin_conversation)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))
    app.add_handler(add_balance_conv)
    app.add_handler(buy_credit_conv)
    app.add_handler(admin_set_upi_conv)
    app.add_handler(admin_set_qr_conv)
    app.add_handler(admin_broadcast_conv)
    app.add_handler(admin_manual_balance_conv)
    app.add_handler(admin_ban_conv)
    app.add_handler(admin_add_code_conv)
    app.add_handler(admin_delete_code_conv)
    app.add_handler(CallbackQueryHandler(main_menu_callback, pattern="^(my_referral|profile|contact_admin|back_to_main|get_code|admin_panel)$"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))
    app.add_error_handler(error_handler)

    print("🤖 Guild Glory Credit Shop Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
