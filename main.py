import asyncio
import logging
import sqlite3
import datetime
import re
import os
import aiohttp
from dotenv import load_dotenv
import time

from aiogram import Bot, Dispatcher, Router, F
from aiogram.enums import ParseMode
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.client.default import DefaultBotProperties

# ==========================================
# কনফিগারেশন
# ==========================================
load_dotenv()
BOT_TOKEN = os.getenv("8895921858:AAHxdCGm8t8mNtK-wK9WHIWxDS15y-19WIA")
ADMIN_ID = int(os.getenv("6820798198"))
LAMIX_TOKEN = os.getenv("PDRCif2kg-Bdrlo1rWVouEeiEliVVb2HP_SBfOnHX10")
LAMIX_BASE_URL = os.getenv("https://panel.lamix.org/api/v1/")
FORCE_JOIN_CHANNEL = os.getenv("FORCE_JOIN_CHANNEL")

logging.basicConfig(level=logging.INFO)

# ==========================================
# ডাটাবেস ডিজাইন
# ==========================================
def init_db():
    conn = sqlite3.connect("sky_sms.db")
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, balance REAL DEFAULT 0.0, last_order_time REAL DEFAULT 0.0)")
    # numbers টেবিলে currency বাদ দিয়ে শুধু USD রাখা হলো
    cursor.execute("""CREATE TABLE IF NOT EXISTS numbers (
        id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT UNIQUE, 
        service_category TEXT, country_name TEXT, country_flag TEXT, 
        price REAL, status TEXT DEFAULT 'AVAILABLE')""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, number_id INTEGER, 
        status TEXT DEFAULT 'PENDING', created_at TIMESTAMP, otp_code TEXT)""")
    cursor.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS categories (name TEXT PRIMARY KEY, icon TEXT)")
    cursor.execute("""CREATE TABLE IF NOT EXISTS deposits (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, method TEXT, 
        amount REAL, trx_id TEXT, status TEXT DEFAULT 'PENDING', created_at TIMESTAMP)""")
    conn.commit()
    conn.close()

init_db()

def get_setting(key, default=None):
    conn = sqlite3.connect("sky_sms.db")
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    res = cur.fetchone()
    conn.close()
    return res[0] if res else default

def set_setting(key, value):
    conn = sqlite3.connect("sky_sms.db")
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

# ==========================================
# ইনিশিয়ালাইজেশন
# ==========================================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

class AdminStates(StatesGroup):
    waiting_for_channel = State()
    waiting_for_cooldown_time = State()
    waiting_for_bal_user = State()
    waiting_for_bal_amount = State()
    waiting_for_txt_file = State()
    waiting_for_country_name = State()
    waiting_for_assign_user = State()
    waiting_for_assign_number = State()
    waiting_for_category_name = State()
    waiting_for_category_icon = State()
    waiting_for_broadcast = State()
    waiting_for_bkash_num = State()
    waiting_for_nagad_num = State()
    waiting_for_binance_id = State()
    waiting_for_min_dep = State()
    waiting_for_dollar_rate = State() # নতুন

class UserStates(StatesGroup):
    waiting_for_usd_amount = State() # নতুন: ডলার অ্যামাউন্ট
    waiting_for_trx_id = State()
    waiting_for_screenshot = State()

# ==========================================
# কীবোর্ড ডিজাইন (সব বোল্ড ও ইংরেজিতে)
# ==========================================
def user_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 GET NUMBER", callback_data="get_service", style="success")],
        [InlineKeyboardButton(text="💰 BALANCE & DEPOSIT", callback_data="balance", style="primary")],
        [InlineKeyboardButton(text="🔍 SERVICE CHECK", callback_data="service_check", style="primary")],
        [InlineKeyboardButton(text="🛠 ADMIN PANEL", callback_data="admin_panel", style="danger")]
    ])

def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 SET FORCE JOIN", callback_data="admin_set_channel", style="primary"),
         InlineKeyboardButton(text="⏱ SET COOLDOWN", callback_data="admin_set_cooldown", style="primary")],
        [InlineKeyboardButton(text="🏦 SET BKASH NUMBER", callback_data="admin_set_bkash", style="success"),
         InlineKeyboardButton(text="🏦 SET NAGAD NUMBER", callback_data="admin_set_nagad", style="success")],
        [InlineKeyboardButton(text="🪙 SET BINANCE ID", callback_data="admin_set_binance", style="success"),
         InlineKeyboardButton(text="💵 SET MIN DEPOSIT", callback_data="admin_set_min_dep", style="primary")],
        [InlineKeyboardButton(text="💱 SET DOLLAR RATE (BDT)", callback_data="admin_set_rate", style="primary"),
         InlineKeyboardButton(text="💰 MANAGE BALANCE", callback_data="admin_manage_balance", style="success")],
        [InlineKeyboardButton(text="📥 UPLOAD NUMBERS", callback_data="admin_upload_start", style="success"),
         InlineKeyboardButton(text="🎯 ASSIGN NUMBER", callback_data="admin_assign_number", style="primary")],
        [InlineKeyboardButton(text="➕ ADD NEW CATEGORY", callback_data="admin_add_category", style="primary"),
         InlineKeyboardButton(text="📣 BROADCAST", callback_data="admin_broadcast", style="danger")],
        [InlineKeyboardButton(text="🔙 BACK TO MENU", callback_data="main_menu", style="danger")]
    ])

def category_selection_menu(action_prefix):
    conn = sqlite3.connect("sky_sms.db")
    cur = conn.cursor()
    cur.execute("SELECT name, icon FROM categories")
    rows = cur.fetchall()
    conn.close()
    kb = []
    if not rows:
        kb.append([InlineKeyboardButton(text="🟢 WHATSAPP", callback_data=f"{action_prefix}_WHATSAPP", style="success")])
    else:
        for row in rows:
            kb.append([InlineKeyboardButton(text=f"{row[1]} {row[0].upper()}", callback_data=f"{action_prefix}_{row[0]}", style="success")])
    kb.append([InlineKeyboardButton(text="🔙 BACK", callback_data="admin_panel" if action_prefix == "upload_cat" else "main_menu", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def country_selection_menu(countries, service):
    kb = []
    for c in countries:
        btn_text = f"{c['flag']} {c['country']}"
        kb.append([InlineKeyboardButton(text=btn_text, callback_data=f"view_country_{service}_{c['country']}", style="success")])
    kb.append([InlineKeyboardButton(text="🔙 BACK", callback_data="get_service", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

# নাম্বার বাটনে "1 / $0.04" ফরম্যাটে দেখাবে
def get_number_keyboard(numbers, service):
    kb = []
    for num in numbers:
        # আপনার চাহিদা অনুযায়ী ফরম্যাট: index / $price (সিম্পল রাখার জন্য শুধু price দেখানো হলো)
        btn_text = f"{num['country_flag']} +{num['number']} — ${num['price']:.2f}"
        kb.append([InlineKeyboardButton(text=btn_text, callback_data=f"buy_{num['id']}", style="success")])
    kb.append([InlineKeyboardButton(text="🔙 BACK", callback_data=f"view_cat_{service}", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def order_menu(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 CHANGE NUMBER", callback_data=f"change_{order_id}", style="primary")],
        [InlineKeyboardButton(text="🔑 CHECK OTP", callback_data=f"check_otp_{order_id}", style="success")],
        [InlineKeyboardButton(text="📛 REFUND", callback_data=f"refund_{order_id}", style="danger")]
    ])

def force_join_keyboard():
    channel = get_setting("force_join_channel", FORCE_JOIN_CHANNEL)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 JOIN CHANNEL", url=f"https://t.me/{channel.replace('@', '')}", style="primary")],
        [InlineKeyboardButton(text="✅ VERIFY", callback_data="verify_join", style="success")]
    ])

def deposit_methods_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 BKASH", callback_data="dep_bkash", style="success"),
         InlineKeyboardButton(text="💰 NAGAD", callback_data="dep_nagad", style="success")],
        [InlineKeyboardButton(text="🪙 BINANCE PAY", callback_data="dep_binance", style="primary")],
        [InlineKeyboardButton(text="🔙 BACK", callback_data="balance", style="danger")]
    ])

def admin_deposit_approval_keyboard(deposit_id, user_id, amount):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ APPROVE", callback_data=f"appr_{deposit_id}_{user_id}_{amount}", style="success")],
        [InlineKeyboardButton(text="❌ REJECT", callback_data=f"rej_{deposit_id}", style="danger")]
    ])

# ==========================================
# হেল্পার ফাংশন
# ==========================================
def check_cooldown(user_id):
    cooldown_seconds = float(get_setting("cooldown_seconds", 60))
    conn = sqlite3.connect("sky_sms.db")
    cur = conn.cursor()
    cur.execute("SELECT last_order_time FROM users WHERE user_id=?", (user_id,))
    last_time = cur.fetchone()[0]
    conn.close()
    time_passed = time.time() - last_time
    if time_passed < cooldown_seconds:
        return False, int(cooldown_seconds - time_passed)
    return True, 0

def update_cooldown(user_id):
    conn = sqlite3.connect("sky_sms.db")
    conn.execute("UPDATE users SET last_order_time=? WHERE user_id=?", (time.time(), user_id))
    conn.commit()
    conn.close()

async def is_user_joined(user_id):
    channel = get_setting("force_join_channel", FORCE_JOIN_CHANNEL)
    if not channel: return True
    try:
        member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
        return member.status not in ["left", "kicked"]
    except: return True

# ==========================================
# ইউজার মেনু ও ডিপোজিট সিস্টেম
# ==========================================
@router.message(CommandStart())
async def start_cmd(message: Message):
    user_id = message.from_user.id
    conn = sqlite3.connect("sky_sms.db")
    conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

    if not await is_user_joined(user_id):
        await message.answer("⚠️ <b>PLEASE JOIN OUR CHANNEL TO USE SKY SMS!</b>", reply_markup=force_join_keyboard())
        return

    await message.answer(f"👋 <b>WELCOME TO SKY SMS, {message.from_user.first_name.upper()}!</b>\n\n<b>SELECT AN OPTION BELOW:</b>", reply_markup=user_main_menu())

@router.callback_query(F.data == "verify_join")
async def verify_join(callback: CallbackQuery):
    if await is_user_joined(callback.from_user.id):
        await callback.message.edit_text("✅ <b>VERIFICATION SUCCESSFUL!</b>", reply_markup=user_main_menu())
    else:
        await callback.answer("❌ YOU HAVE NOT JOINED YET!", show_alert=True)

@router.callback_query(F.data == "main_menu")
async def back_to_main(callback: CallbackQuery):
    await callback.message.edit_text("<b>SELECT AN OPTION BELOW:</b>", reply_markup=user_main_menu())

@router.callback_query(F.data == "balance")
async def show_user_balance(callback: CallbackQuery):
    conn = sqlite3.connect("sky_sms.db")
    cur = conn.cursor()
    cur.execute("SELECT balance FROM users WHERE user_id=?", (callback.from_user.id,))
    bal = cur.fetchone()[0]
    conn.close()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 DEPOSIT NOW", callback_data="deposit_menu", style="success")],
        [InlineKeyboardButton(text="🔙 BACK", callback_data="main_menu", style="danger")]
    ])
    await callback.message.edit_text(f"💰 <b>YOUR CURRENT BALANCE: ${bal:.4f}</b>", reply_markup=kb)

@router.callback_query(F.data == "deposit_menu")
async def deposit_menu(callback: CallbackQuery):
    await callback.message.edit_text("<b>SELECT DEPOSIT METHOD:</b>", reply_markup=deposit_methods_keyboard())

# বিকাশ, নগদ, বাইন্যান্স ডিপোজিট (ডলার রেট অনুযায়ী)
@router.callback_query(F.data.startswith("dep_"))
async def process_deposit_method(callback: CallbackQuery, state: FSMContext):
    method = callback.data.split("_")[1].upper()
    min_dep = float(get_setting("min_deposit", "1.00"))
    dollar_rate = float(get_setting("dollar_rate", "130.0"))
    
    if method == "BKASH":
        num = get_setting("bkash_number", "NOT SET")
        copy_btn = InlineKeyboardButton(text="📋 COPY BKASH NUMBER", copy_text=types.CopyTextButton(text=num), style="primary")
        display_text = f"📱 <b>BKASH DEPOSIT</b>\n\n<b>PLEASE SEND MONEY TO THIS NUMBER:</b>\n<code>{num}</code>\n\n<b>MINIMUM DEPOSIT: ${min_dep}</b>\n<b>1 USD = {dollar_rate} BDT</b>\n\n<b>HOW MANY USD DO YOU WANT TO DEPOSIT?</b>"
    elif method == "NAGAD":
        num = get_setting("nagad_number", "NOT SET")
        copy_btn = InlineKeyboardButton(text="📋 COPY NAGAD NUMBER", copy_text=types.CopyTextButton(text=num), style="primary")
        display_text = f"📱 <b>NAGAD DEPOSIT</b>\n\n<b>PLEASE SEND MONEY TO THIS NUMBER:</b>\n<code>{num}</code>\n\n<b>MINIMUM DEPOSIT: ${min_dep}</b>\n<b>1 USD = {dollar_rate} BDT</b>\n\n<b>HOW MANY USD DO YOU WANT TO DEPOSIT?</b>"
    elif method == "BINANCE":
        num = get_setting("binance_id", "NOT SET")
        copy_btn = InlineKeyboardButton(text="📋 COPY BINANCE PAY ID", copy_text=types.CopyTextButton(text=num), style="primary")
        display_text = f"🪙 <b>BINANCE PAY DEPOSIT</b>\n\n<b>PLEASE SEND MONEY TO THIS BINANCE ID:</b>\n<code>{num}</code>\n\n<b>MINIMUM DEPOSIT: ${min_dep}</b>\n\n<b>HOW MANY USD DO YOU WANT TO DEPOSIT?</b>"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [copy_btn],
        [InlineKeyboardButton(text="🔙 BACK", callback_data="deposit_menu", style="danger")]
    ])
    await state.update_data(method=method)
    await state.set_state(UserStates.waiting_for_usd_amount)
    await callback.message.edit_text(display_text, reply_markup=kb)

@router.message(UserStates.waiting_for_usd_amount)
async def process_usd_amount(message: Message, state: FSMContext):
    try:
        usd_amount = float(message.text)
        min_dep = float(get_setting("min_deposit", "1.0"))
        if usd_amount < min_dep:
            return await message.answer(f"❌ <b>MINIMUM DEPOSIT IS ${min_dep:.2f} USD. TRY AGAIN:</b>")
            
        data = await state.get_data()
        method = data.get('method')
        dollar_rate = float(get_setting("dollar_rate", "130.0"))
        
        # বিকাশ/নগদ এর জন্য টাকার হিসাব
        if method in ["BKASH", "NAGAD"]:
            bdt_amount = usd_amount * dollar_rate
            await state.update_data(usd_amount=usd_amount, bdt_amount=bdt_amount)
            await message.answer(f"💵 <b>YOU NEED TO SEND {bdt_amount:.2f} BDT TO THE {method} NUMBER.</b>\n\n<b>AFTER SENDING, ENTER YOUR TRANSACTION ID (e.g., jjdbdjjhs):</b>")
        else:
            await state.update_data(usd_amount=usd_amount, bdt_amount=0)
            await message.answer(f"🪙 <b>PLEASE SEND {usd_amount:.2f} USDT TO THE BINANCE ID.</b>\n\n<b>AFTER SENDING, ENTER YOUR ORDER ID (e.g., 123456789):</b>")
            
        await state.set_state(UserStates.waiting_for_trx_id)
    except:
        await message.answer("❌ <b>INVALID AMOUNT! PLEASE ENTER A NUMBER.</b>")

@router.message(UserStates.waiting_for_trx_id)
async def process_trx_id(message: Message, state: FSMContext):
    await state.update_data(trx_id=message.text)
    await state.set_state(UserStates.waiting_for_screenshot)
    await message.answer("📸 <b>PLEASE SEND THE SCREENSHOT OF YOUR PAYMENT:</b>")

@router.message(UserStates.waiting_for_screenshot, F.photo)
async def process_screenshot(message: Message, state: FSMContext):
    data = await state.get_data()
    method = data['method']
    usd_amount = data['usd_amount']
    bdt_amount = data.get('bdt_amount', 0)
    trx_id = data['trx_id']
    user_id = message.from_user.id
    screenshot_id = message.photo[-1].file_id

    conn = sqlite3.connect("sky_sms.db")
    cur = conn.cursor()
    cur.execute("INSERT INTO deposits (user_id, method, amount, trx_id, status, created_at) VALUES (?, ?, ?, ?, 'PENDING', ?)",
                (user_id, method, usd_amount, trx_id, datetime.datetime.now()))
    dep_id = cur.lastrowid
    conn.commit()
    conn.close()

    await state.clear()
    await message.answer("✅ <b>YOUR DEPOSIT REQUEST HAS BEEN SENT TO ADMIN FOR APPROVAL. PLEASE WAIT.</b>", reply_markup=user_main_menu())

    admin_text = (
        f"💰 <b>NEW DEPOSIT REQUEST!</b>\n\n"
        f"<b>USER ID:</b> <code>{user_id}</code>\n"
        f"<b>METHOD:</b> {method}\n"
        f"<b>USD AMOUNT:</b> ${usd_amount:.2f}\n"
    )
    if method in ["BKASH", "NAGAD"]:
        admin_text += f"<b>BDT EQUIVALENT:</b> {bdt_amount:.2f} BDT\n"
    admin_text += f"<b>TRX ID:</b> <code>{trx_id}</code>"
    
    await bot.send_photo(ADMIN_ID, photo=screenshot_id, caption=admin_text, reply_markup=admin_deposit_approval_keyboard(dep_id, user_id, usd_amount))

@router.callback_query(F.data.startswith("appr_"))
async def approve_deposit(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    _, dep_id, user_id, amount = callback.data.split("_")
    amount = float(amount)
    conn = sqlite3.connect("sky_sms.db")
    cur = conn.cursor()
    cur.execute("SELECT status FROM deposits WHERE id=?", (dep_id,))
    if cur.fetchone()[0] != 'PENDING':
        conn.close()
        return await callback.answer("❌ ALREADY PROCESSED!", show_alert=True)
    cur.execute("UPDATE deposits SET status='APPROVED' WHERE id=?", (dep_id,))
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    cur.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    new_bal = cur.fetchone()[0]
    conn.close()
    await callback.message.edit_caption(caption=f"✅ <b>APPROVED!</b>\n<b>USER:</b> {user_id}\n<b>AMOUNT:</b> ${amount:.2f}\n<b>NEW BALANCE:</b> ${new_bal:.2f}")
    try:
        await bot.send_message(user_id, f"🎉 <b>YOUR DEPOSIT OF ${amount:.2f} HAS BEEN APPROVED!</b>\n💰 <b>NEW BALANCE: ${new_bal:.2f}</b>")
    except: pass

@router.callback_query(F.data.startswith("rej_"))
async def reject_deposit(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    dep_id = callback.data.split("_")[1]
    conn = sqlite3.connect("sky_sms.db")
    cur = conn.cursor()
    cur.execute("SELECT user_id, amount, status FROM deposits WHERE id=?", (dep_id,))
    dep = cur.fetchone()
    if dep[2] != 'PENDING':
        conn.close()
        return await callback.answer("❌ ALREADY PROCESSED!", show_alert=True)
    cur.execute("UPDATE deposits SET status='REJECTED' WHERE id=?", (dep_id,))
    conn.commit()
    conn.close()
    await callback.message.edit_caption(caption=f"❌ <b>REJECTED!</b>\n<b>USER:</b> {dep[0]}\n<b>AMOUNT:</b> ${dep[1]:.2f}")
    try:
        await bot.send_message(dep[0], f"❌ <b>YOUR DEPOSIT REQUEST OF ${dep[1]:.2f} HAS BEEN REJECTED.</b>")
    except: pass

# ==========================================
# নাম্বার কেনা, OTP চেক ও রিফান্ড
# ==========================================
@router.callback_query(F.data == "get_service")
async def show_services(callback: CallbackQuery):
    await callback.message.edit_text("<b>SELECT SERVICE CATEGORY:</b>", reply_markup=category_selection_menu("view_cat"))

@router.callback_query(F.data.startswith("view_cat_"))
async def show_countries(callback: CallbackQuery):
    service = callback.data.split("_")[2]
    conn = sqlite3.connect("sky_sms.db")
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT country_name, country_flag FROM numbers WHERE status = 'AVAILABLE' AND service_category = ?", (service,))
    rows = cur.fetchall()
    conn.close()
    if not rows: return await callback.answer(f"❌ NO {service} STOCK AVAILABLE!", show_alert=True)
    countries = [{"country": r[0], "flag": r[1]} for r in rows]
    text = f"🌍 <b>AVAILABLE COUNTRIES FOR {service}:</b>\n\n👇 <b>SELECT A COUNTRY:</b>"
    await callback.message.edit_text(text, reply_markup=country_selection_menu(countries, service))

@router.callback_query(F.data.startswith("view_country_"))
async def show_numbers_by_country(callback: CallbackQuery):
    _, _, service, country = callback.data.split("_", 3)
    conn = sqlite3.connect("sky_sms.db")
    cur = conn.cursor()
    cur.execute("SELECT id, number, country_flag, price FROM numbers WHERE status = 'AVAILABLE' AND service_category = ? AND country_name = ? LIMIT 10", (service, country))
    rows = cur.fetchall()
    conn.close()
    if not rows: return await callback.answer("❌ NO NUMBERS LEFT IN THIS COUNTRY!", show_alert=True)
    numbers = [{"id": r[0], "number": r[1], "country_flag": r[2], "price": r[3]} for r in rows]
    text = f"📦 <b>{country} {service} NUMBERS</b>\n\n👇 <b>SELECT A NUMBER:</b>"
    await callback.message.edit_text(text, reply_markup=get_number_keyboard(numbers, service))

@router.callback_query(F.data.startswith("buy_"))
async def buy_number(callback: CallbackQuery):
    num_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    can_order, remaining = check_cooldown(user_id)
    if not can_order: return await callback.answer(f"⏱ PLEASE WAIT {remaining} SECONDS!", show_alert=True)

    conn = sqlite3.connect("sky_sms.db")
    cur = conn.cursor()
    cur.execute("SELECT number, price, country_flag FROM numbers WHERE id=? AND status='AVAILABLE'", (num_id,))
    data = cur.fetchone()
    if not data:
        conn.close()
        return await callback.answer("❌ NUMBER ALREADY SOLD!", show_alert=True)
    number, price, flag = data
    cur.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    balance = cur.fetchone()[0]
    if balance < price:
        conn.close()
        return await callback.answer("❌ INSUFFICIENT BALANCE!", show_alert=True)
    cur.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (price, user_id))
    cur.execute("UPDATE numbers SET status='IN_USE' WHERE id=?", (num_id,))
    cur.execute("INSERT INTO orders (user_id, number_id, status, created_at) VALUES (?, ?, 'PENDING', ?)", (user_id, num_id, datetime.datetime.now()))
    order_id = cur.lastrowid
    conn.commit()
    conn.close()
    update_cooldown(user_id)
    text = f"{flag} <code>+{number}</code>\n\n⏳ <b>WAITING FOR OTP... (5 MIN TIMER)</b>"
    await callback.message.edit_text(text, reply_markup=order_menu(order_id))
    asyncio.create_task(auto_refund_timer(order_id, user_id, number, price))

@router.callback_query(F.data.startswith("check_otp_"))
async def check_otp(callback: CallbackQuery):
    order_id = int(callback.data.split("_")[2])
    conn = sqlite3.connect("sky_sms.db")
    cur = conn.cursor()
    cur.execute("SELECT o.status, n.number FROM orders o JOIN numbers n ON o.number_id = n.id WHERE o.id=?", (order_id,))
    order_data = cur.fetchone()
    if not order_data:
        conn.close()
        return await callback.answer("❌ ORDER NOT FOUND!", show_alert=True)
    order_status, number = order_data
    if order_status == 'SUCCESS':
        conn.close()
        return await callback.answer("❌ OTP ALREADY RECEIVED! NO REFUND.", show_alert=True)
    await callback.answer("🔍 SEARCHING FOR OTP...", show_alert=False)
    headers = {"Authorization": f"Bearer {LAMIX_TOKEN}"}
    params = {"number": number, "limit": 5}
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{LAMIX_BASE_URL}/messages", headers=headers, params=params) as res:
            if res.status == 200:
                data = await res.json()
                if data.get("records"):
                    content = data["records"][0]["content"]
                    match = re.search(r'\d{4,6}', content)
                    if match:
                        otp = match.group(0)
                        cur.execute("UPDATE orders SET status='SUCCESS', otp_code=? WHERE id=?", (otp, order_id))
                        cur.execute("DELETE FROM numbers WHERE number=?", (number,))
                        conn.commit()
                        conn.close()
                        text = callback.message.text.split("\n\n")[0] + f"\n\n🔑 <b>YOUR OTP:</b> <code>{otp}</code>\n\n✅ <b>ORDER COMPLETED!</b>"
                        return await callback.message.edit_text(text)
    conn.close()
    await callback.answer("❌ OTP NOT FOUND YET. PLEASE WAIT.", show_alert=True)

@router.callback_query(F.data.startswith("refund_"))
async def manual_refund(callback: CallbackQuery):
    order_id = int(callback.data.split("_")[1])
    conn = sqlite3.connect("sky_sms.db")
    cur = conn.cursor()
    cur.execute("SELECT user_id, number_id, status FROM orders WHERE id=?", (order_id,))
    order = cur.fetchone()
    if not order:
        conn.close()
        return await callback.answer("❌ ORDER NOT FOUND!", show_alert=True)
    if order[2] == 'SUCCESS':
        conn.close()
        return await callback.answer("❌ NO REFUND! OTP ALREADY RECEIVED.", show_alert=True)
    if order[2] != 'PENDING':
        conn.close()
        return await callback.answer("❌ ALREADY PROCESSED!", show_alert=True)
    user_id, num_id = order[0], order[1]
    cur.execute("SELECT price, number FROM numbers WHERE id=?", (num_id,))
    price, number = cur.fetchone()
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (price, user_id))
    cur.execute("UPDATE orders SET status='REFUNDED' WHERE id=?", (order_id,))
    cur.execute("DELETE FROM numbers WHERE number=?", (number,))
    conn.commit()
    conn.close()
    await callback.message.edit_text(f"💸 <b>REFUNDED ${price}!</b>", reply_markup=user_main_menu())

async def auto_refund_timer(order_id, user_id, number, price):
    await asyncio.sleep(300)
    conn = sqlite3.connect("sky_sms.db")
    cur = conn.cursor()
    cur.execute("SELECT status FROM orders WHERE id=?", (order_id,))
    if cur.fetchone()[0] == 'PENDING':
        cur.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (price, user_id))
        cur.execute("UPDATE orders SET status='REFUNDED' WHERE id=?", (order_id,))
        cur.execute("DELETE FROM numbers WHERE number=?", (number,))
        conn.commit()
        try:
            await bot.send_message(user_id, f"⏰ <b>5 MIN PASSED. NO OTP.</b>\n💸 <b>REFUNDED ${price}.</b>", reply_markup=user_main_menu())
        except: pass
    conn.close()

# ==========================================
# অ্যাডমিন প্যানেল
# ==========================================
@router.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return await callback.answer("⛔ ACCESS DENIED.", show_alert=True)
    await callback.message.edit_text("<b>🛠 ADMIN PANEL</b>\n\n<b>SELECT AN ACTION:</b>", reply_markup=admin_menu())

@router.callback_query(F.data == "admin_set_bkash")
async def ask_bkash_num(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_for_bkash_num)
    await callback.message.edit_text("<b>ENTER YOUR BKASH NUMBER:</b>")

@router.message(AdminStates.waiting_for_bkash_num)
async def save_bkash_num(message: Message, state: FSMContext):
    set_setting("bkash_number", message.text)
    await state.clear()
    await message.answer(f"✅ <b>BKASH NUMBER SET TO: {message.text}</b>", reply_markup=admin_menu())

@router.callback_query(F.data == "admin_set_nagad")
async def ask_nagad_num(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_for_nagad_num)
    await callback.message.edit_text("<b>ENTER YOUR NAGAD NUMBER:</b>")

@router.message(AdminStates.waiting_for_nagad_num)
async def save_nagad_num(message: Message, state: FSMContext):
    set_setting("nagad_number", message.text)
    await state.clear()
    await message.answer(f"✅ <b>NAGAD NUMBER SET TO: {message.text}</b>", reply_markup=admin_menu())

@router.callback_query(F.data == "admin_set_binance")
async def ask_binance_id(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_for_binance_id)
    await callback.message.edit_text("<b>ENTER YOUR BINANCE PAY ID (e.g., 123456789):</b>")

@router.message(AdminStates.waiting_for_binance_id)
async def save_binance_id(message: Message, state: FSMContext):
    set_setting("binance_id", message.text)
    await state.clear()
    await message.answer(f"✅ <b>BINANCE ID SET TO: {message.text}</b>", reply_markup=admin_menu())

@router.callback_query(F.data == "admin_set_min_dep")
async def ask_min_dep(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_for_min_dep)
    await callback.message.edit_text("<b>ENTER MINIMUM DEPOSIT AMOUNT (e.g., 1.00):</b>")

@router.message(AdminStates.waiting_for_min_dep)
async def save_min_dep(message: Message, state: FSMContext):
    try:
        amount = float(message.text)
        set_setting("min_deposit", str(amount))
        await state.clear()
        await message.answer(f"✅ <b>MINIMUM DEPOSIT SET TO: ${amount:.2f}</b>", reply_markup=admin_menu())
    except:
        await message.answer("<b>❌ INVALID AMOUNT.</b>")

# ডলার রেট সেট
@router.callback_query(F.data == "admin_set_rate")
async def ask_dollar_rate(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_for_dollar_rate)
    await callback.message.edit_text("<b>ENTER DOLLAR RATE IN BDT (e.g., 130):</b>")

@router.message(AdminStates.waiting_for_dollar_rate)
async def save_dollar_rate(message: Message, state: FSMContext):
    try:
        rate = float(message.text)
        set_setting("dollar_rate", str(rate))
        await state.clear()
        await message.answer(f"✅ <b>DOLLAR RATE SET TO: 1 USD = {rate} BDT</b>", reply_markup=admin_menu())
    except:
        await message.answer("<b>❌ INVALID RATE.</b>")

# --- ক্যাটাগরি অ্যাড ---
@router.callback_query(F.data == "admin_add_category")
async def ask_category_name(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_for_category_name)
    await callback.message.edit_text("<b>ENTER THE CATEGORY NAME (e.g., FACEBOOK):</b>")

@router.message(AdminStates.waiting_for_category_name)
async def ask_category_icon(message: Message, state: FSMContext):
    await state.update_data(cat_name=message.text.upper())
    await state.set_state(AdminStates.waiting_for_category_icon)
    await message.answer("<b>NOW SEND AN EMOJI/LOGO FOR THIS CATEGORY (e.g., 🔵 or 📘):</b>")

@router.message(AdminStates.waiting_for_category_icon)
async def save_category(message: Message, state: FSMContext):
    data = await state.get_data()
    cat_name = data['cat_name']
    cat_icon = message.text.strip()
    conn = sqlite3.connect("sky_sms.db")
    conn.execute("INSERT OR REPLACE INTO categories (name, icon) VALUES (?, ?)", (cat_name, cat_icon))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer(f"✅ <b>CATEGORY '{cat_name}' ADDED WITH ICON {cat_icon}!</b>", reply_markup=admin_menu())

# --- ব্রডকাস্ট ---
@router.callback_query(F.data == "admin_broadcast")
async def ask_broadcast_msg(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_for_broadcast)
    await callback.message.edit_text("<b>📣 SEND THE MESSAGE OR MEDIA YOU WANT TO BROADCAST:</b>")

@router.message(AdminStates.waiting_for_broadcast)
async def send_broadcast(message: Message, state: FSMContext):
    conn = sqlite3.connect("sky_sms.db")
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    users = cur.fetchall()
    conn.close()
    success, failed = 0, 0
    await message.answer(f"⏳ <b>BROADCASTING TO {len(users)} USERS...</b>")
    for user in users:
        try:
            await message.copy_to(chat_id=user[0])
            success += 1
            await asyncio.sleep(0.05)
        except: failed += 1
    await state.clear()
    await message.answer(f"✅ <b>BROADCAST COMPLETE!</b>\n\n<b>SUCCESS: {success}</b>\n<b>FAILED: {failed}</b>", reply_markup=admin_menu())

# --- অ্যাডমিন আগের ফিচার (চ্যানেল, কুলডাউন, আপলোড, অ্যাসাইন) ---
@router.callback_query(F.data == "admin_set_channel")
async def ask_channel(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_for_channel)
    await callback.message.edit_text("<b>SEND THE CHANNEL USERNAME (e.g., @mychannel):</b>")

@router.message(AdminStates.waiting_for_channel)
async def save_channel(message: Message, state: FSMContext):
    set_setting("force_join_channel", message.text)
    await state.clear()
    await message.answer(f"✅ <b>CHANNEL SET TO: {message.text}</b>", reply_markup=admin_menu())

@router.callback_query(F.data == "admin_set_cooldown")
async def ask_cooldown(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_for_cooldown_time)
    await callback.message.edit_text("<b>ENTER COOLDOWN TIME IN SECONDS:</b>")

@router.message(AdminStates.waiting_for_cooldown_time)
async def save_cooldown(message: Message, state: FSMContext):
    if message.text.isdigit():
        set_setting("cooldown_seconds", message.text)
        await state.clear()
        await message.answer(f"✅ <b>COOLDOWN SET TO {message.text} SECONDS.</b>", reply_markup=admin_menu())

@router.callback_query(F.data == "admin_upload_start")
async def admin_upload_start(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.edit_text("<b>SELECT CATEGORY FOR UPLOAD:</b>", reply_markup=category_selection_menu("upload_cat"))

@router.callback_query(F.data.startswith("upload_cat_"))
async def ask_for_txt(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    category = callback.data.split("_")[2]
    await state.update_data(upload_category=category)
    await state.set_state(AdminStates.waiting_for_txt_file)
    await callback.message.edit_text(f"<b>📥 PLEASE SEND A .TXT FILE FOR {category}.</b>\n\n<b>(ONE NUMBER PER LINE)</b>")

@router.message(AdminStates.waiting_for_txt_file, F.document)
async def handle_txt_upload(message: Message, state: FSMContext):
    doc = message.document
    if not doc.file_name.endswith('.txt'):
        return await message.answer("❌ <b>ONLY .TXT FILES ALLOWED!</b>")
    file_path = f"downloads/{doc.file_id}.txt"
    os.makedirs("downloads", exist_ok=True)
    await message.bot.download(doc, destination=file_path)
    await state.update_data(file_path=file_path)
    await state.set_state(AdminStates.waiting_for_country_name)
    await message.answer("✅ <b>FILE RECEIVED!</b>\n\n<b>NOW PLEASE TYPE THE COUNTRY NAME WITH FLAG (e.g., 🇻🇳 VIETNAM):</b>")

@router.message(AdminStates.waiting_for_country_name)
async def process_country_and_save(message: Message, state: FSMContext):
    country_input = message.text.strip()
    if len(country_input.split()) > 1:
        flag = country_input.split()[0]
        country_name = " ".join(country_input.split()[1:]).upper()
    else:
        flag = "🌍"
        country_name = country_input.upper()
    data = await state.get_data()
    file_path = data['file_path']
    category = data['upload_category']
    
    default_price = float(get_setting("default_price", "0.0050"))
    added = 0
    conn = sqlite3.connect("sky_sms.db")
    cur = conn.cursor()
    with open(file_path, 'r') as f:
        for line in f:
            num_str = line.strip().replace("+", "")
            if not num_str: continue
            cur.execute("""INSERT OR IGNORE INTO numbers 
                (number, service_category, country_name, country_flag, price, status) 
                VALUES (?, ?, ?, ?, ?, 'AVAILABLE')""",
                (num_str, category, country_name, flag, default_price))
            if cur.rowcount > 0: added += 1
    conn.commit()
    conn.close()
    os.remove(file_path)
    await state.clear()
    await message.answer(f"✅ <b>SUCCESS! ADDED {added} NUMBERS TO {country_name} ({category}).</b>", reply_markup=admin_menu())

# --- ম্যানুয়াল অ্যাসাইন ও ব্যালেন্স ম্যানেজমেন্ট ---
@router.callback_query(F.data == "admin_assign_number")
async def ask_assign_user(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_for_assign_user)
    await callback.message.edit_text("<b>SEND THE USER ID TO ASSIGN A NUMBER:</b>")

@router.message(AdminStates.waiting_for_assign_user)
async def ask_assign_number(message: Message, state: FSMContext):
    await state.update_data(target_user=message.text)
    await state.set_state(AdminStates.waiting_for_assign_number)
    await message.answer("<b>SEND THE NUMBER (WITH OR WITHOUT '+'):</b>")

@router.message(AdminStates.waiting_for_assign_number)
async def save_assigned_number(message: Message, state: FSMContext):
    data = await state.get_data()
    target_user = data['target_user']
    raw_number = message.text.strip().replace("+", "")
    try:
        conn = sqlite3.connect("sky_sms.db")
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO numbers (number, service_category, country_name, country_flag, price, status) VALUES (?, 'CUSTOM', 'CUSTOM', '🌍', 0.0, 'IN_USE')", (raw_number,))
        num_id = cur.lastrowid
        cur.execute("INSERT INTO orders (user_id, number_id, status, created_at) VALUES (?, ?, 'PENDING', ?)", (target_user, num_id, datetime.datetime.now()))
        order_id = cur.lastrowid
        conn.commit()
        conn.close()
        await state.clear()
        await message.answer(f"✅ <b>NUMBER +{raw_number} ASSIGNED TO USER {target_user}.</b>", reply_markup=admin_menu())
        try:
            await bot.send_message(target_user, f"🎁 <b>ADMIN ASSIGNED A NUMBER TO YOU:</b>\n\n🌍 <code>+{raw_number}</code>\n\n⏳ <b>WAITING FOR OTP...</b>", reply_markup=order_menu(order_id))
        except: pass
    except Exception as e:
        await message.answer(f"<b>❌ ERROR: {e}</b>")

@router.callback_query(F.data == "admin_manage_balance")
async def ask_bal_user(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_for_bal_user)
    await callback.message.edit_text("<b>💰 ENTER THE USER ID TO MANAGE BALANCE:</b>")

@router.message(AdminStates.waiting_for_bal_user)
async def show_user_info_for_balance(message: Message, state: FSMContext):
    target_user = message.text.strip()
    conn = sqlite3.connect("sky_sms.db")
    cur = conn.cursor()
    cur.execute("SELECT balance FROM users WHERE user_id=?", (target_user,))
    user_data = cur.fetchone()
    conn.close()
    if not user_data:
        return await message.answer("<b>❌ USER NOT FOUND! TRY AGAIN:</b>")
    current_balance = user_data[0]
    await state.update_data(target_user=target_user)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ ADD MONEY", callback_data=f"bal_add_{target_user}", style="success"),
         InlineKeyboardButton(text="➖ SUBTRACT MONEY", callback_data=f"bal_sub_{target_user}", style="danger")],
        [InlineKeyboardButton(text="🔙 CANCEL", callback_data="admin_panel", style="danger")]
    ])
    await message.answer(
        f"👤 <b>USER ID: {target_user}</b>\n"
        f"💰 <b>CURRENT BALANCE: ${current_balance:.4f}</b>\n\n"
        f"<b>SELECT ACTION:</b>",
        reply_markup=kb
    )

@router.callback_query(F.data.startswith("bal_add_"))
async def ask_add_amount(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    user_id = callback.data.split("_")[2]
    await state.update_data(target_user=user_id, action="ADD")
    await state.set_state(AdminStates.waiting_for_bal_amount)
    await callback.message.edit_text(f"➕ <b>ENTER AMOUNT TO ADD TO USER {user_id}:</b>")

@router.callback_query(F.data.startswith("bal_sub_"))
async def ask_sub_amount(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    user_id = callback.data.split("_")[2]
    await state.update_data(target_user=user_id, action="SUB")
    await state.set_state(AdminStates.waiting_for_bal_amount)
    await callback.message.edit_text(f"➖ <b>ENTER AMOUNT TO SUBTRACT FROM USER {user_id}:</b>")

@router.message(AdminStates.waiting_for_bal_amount)
async def save_balance_change(message: Message, state: FSMContext):
    data = await state.get_data()
    target_user = data['target_user']
    action = data['action']
    try:
        amount = float(message.text)
        if amount <= 0: raise ValueError
        conn = sqlite3.connect("sky_sms.db")
        cur = conn.cursor()
        if action == "ADD":
            cur.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, target_user))
        else:
            cur.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (amount, target_user))
        conn.commit()
        cur.execute("SELECT balance FROM users WHERE user_id=?", (target_user,))
        new_balance = cur.fetchone()[0]
        conn.close()
        await state.clear()
        action_text = "ADDED TO" if action == "ADD" else "SUBTRACTED FROM"
        await message.answer(f"✅ <b>SUCCESSFULLY {action_text} USER {target_user}!</b>\n💰 <b>NEW BALANCE: ${new_balance:.4f}</b>", reply_markup=admin_menu())
        try:
            if action == "ADD":
                await bot.send_message(target_user, f"🎉 <b>ADMIN ADDED ${amount:.4f} TO YOUR WALLET!</b>\n💰 <b>NEW BALANCE: ${new_balance:.4f}</b>")
            else:
                await bot.send_message(target_user, f"⚠️ <b>ADMIN SUBTRACTED ${amount:.4f} FROM YOUR WALLET!</b>\n💰 <b>NEW BALANCE: ${new_balance:.4f}</b>")
        except: pass
    except:
        await message.answer("❌ <b>INVALID AMOUNT.</b>")

# ==========================================
# বট রান
# ==========================================
async def main():
    logging.info("SKY SMS BOT IS RUNNING...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
