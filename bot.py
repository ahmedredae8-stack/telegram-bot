import asyncio
import datetime
import logging
import os
import sqlite3
from typing import Any, Dict, Tuple

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiohttp import web

# -----------------------------------------------------------------------------
# الإعدادات الأساسية
# -----------------------------------------------------------------------------
BOT_TOKEN = "8917050847:AAE3Ll5CIIv2o3FEXTudTJlnwhua3UOafc4"
ADMIN_IDS = [7449655663]
DB_FILE = "production_bot.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# -----------------------------------------------------------------------------
# إدارة قاعدة البيانات
# -----------------------------------------------------------------------------
def db_execute(sql: str, params: tuple = (), fetchone: bool = False, fetchall: bool = False, commit: bool = False) -> Any:
    """مُعالج آمن للتعامل مع قاعدة البيانات مع إغلاق الاتصال أوتوماتيكياً."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        if commit:
            conn.commit()
        if fetchone:
            return cursor.fetchone()
        if fetchall:
            return cursor.fetchall()
        return None

def init_db() -> None:
    """إنشاء الجداول الأساسية عند بدء التشغيل."""
    db_execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            is_banned INTEGER DEFAULT 0,
            last_sent_date TEXT
        )
    ''', commit=True)
    
    db_execute('''
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            category TEXT,
            admin_msg_id INTEGER,
            status TEXT DEFAULT 'OPEN',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''', commit=True)

init_db()

# -----------------------------------------------------------------------------
# حالات FSM
# -----------------------------------------------------------------------------
class UserStates(StatesGroup):
    writing_message = State()

class AdminStates(StatesGroup):
    replying_ticket = State()
    broadcasting = State()

# -----------------------------------------------------------------------------
# الواجهات المساعدة (Keyboards & UI)
# -----------------------------------------------------------------------------
def get_user_status(user_id: int) -> Dict[str, bool]:
    today = str(datetime.date.today())
    row = db_execute("SELECT is_banned, last_sent_date FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    if not row:
        return {"banned": False, "used_today": False}
    return {
        "banned": bool(row[0]),
        "used_today": (row[1] == today)
    }

def build_main_menu(user_name: str, user_id: int, used_today: bool, is_banned: bool) -> Tuple[str, InlineKeyboardMarkup]:
    status_text = "🚫 محظور" if is_banned else ("⚠️ استنفذت حد اليوم" if used_today else "نشط ✅")
    tickets_left = "0" if (used_today or is_banned) else "1"
    
    text = (
        f"⚡ <b>مركـز الـدعم والتـواصـل الـذكـي | ملوك القراصنة</b> 🏴‍☠️\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"🟢 <b>حالة الخدمة:</b> <code>متصل - Online</code>\n\n"
        f"👋 <b>مرحباً بك يا</b> <b>{user_name}</b>!\n\n"
        f"📌 <b>بطاقة حسابك اليومية:</b>\n"
        f"├ 🆔 <b>المعرّف:</b> <code>{user_id}</code>\n"
        f"├ 🎫 <b>الرصيد المتاح:</b> <code>{tickets_left} تذكرة</code>\n"
        f"└ 🛡 <b>حالة الحساب:</b> <code>{status_text}</code>\n\n"
        f"💡 <i>اختر القسم المناسب لإرسال تذكرتك للإدارة:</i>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💡 تقديم اقتراح", callback_data="cat_اقتراح"),
            InlineKeyboardButton(text="⚠️ إبلاغ عن مشكلة", callback_data="cat_مشكلة")
        ],
        [
            InlineKeyboardButton(text="❓ استفسار عام", callback_data="cat_استفسار")
        ],
        [
            InlineKeyboardButton(text="🔄 تحديث حالة البوت 🟢", callback_data="refresh_status")
        ]
    ])
    return text, kb

def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ إلغاء والعودة للقائمة الرئيسية")]],
        resize_keyboard=True
    )

def get_admin_ticket_keyboard(ticket_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💬 الرد على التذكرة", callback_data=f"reply_tkt_{ticket_id}_{user_id}"),
            InlineKeyboardButton(text="🚫 حظر المستخدم", callback_data=f"ban_usr_{user_id}")
        ]
    ])

# -----------------------------------------------------------------------------
# أحداث المستخدم (User Handlers)
# -----------------------------------------------------------------------------
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user = message.from_user
    
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    
    db_execute('''
        INSERT INTO users (user_id, username, full_name, last_sent_date) 
        VALUES (?, ?, ?, '') 
        ON CONFLICT(user_id) DO UPDATE SET username=?, full_name=?
    ''', (user.id, user.username, user.full_name, user.username, user.full_name), commit=True)

    status = get_user_status(user.id)
    text, kb = build_main_menu(user.first_name, user.id, status["used_today"], status["banned"])
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@router.callback_query(F.data == "refresh_status")
async def refresh_status_cb(call: CallbackQuery):
    user = call.from_user
    status = get_user_status(user.id)
    text, kb = build_main_menu(user.first_name, user.id, status["used_today"], status["banned"])
    try:
        await call.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        await call.answer("🟢 تم تحديث البيانات بنجاح!", show_alert=False)
    except Exception:
        await call.answer("🟢 البيانات محدثة بالفعل.", show_alert=False)

@router.message(F.text == "❌ إلغاء والعودة للقائمة الرئيسية")
async def cancel_handler(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🔄 تم إلغاء العملية الحاليّة.", reply_markup=ReplyKeyboardRemove())
    user = message.from_user
    status = get_user_status(user.id)
    text, kb = build_main_menu(user.first_name, user.id, status["used_today"], status["banned"])
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@router.callback_query(F.data.startswith("cat_"))
async def category_selected(call: CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    status = get_user_status(user_id)
    
    if status["banned"]:
        await call.answer("❌ حسابك محظور من استخدام النظام.", show_alert=True)
        return

    if status["used_today"]:
        await call.answer("❌ استنفذت حدك اليومي (تذكرة واحدة كل 24 ساعة).", show_alert=True)
        return

    category = call.data.split("_")[1]
    await state.update_data(chosen_category=category)
    await state.set_state(UserStates.writing_message)
    
    await call.answer()
    await call.message.delete()
    
    prompt = (
        f"📝 <b>قسم التذكرة: [{category}]</b>\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"يرجى كتابة رسالتك أو إرسال المرفق المطلوب (نص، صورة، أو ملف صوتي):"
    )
    await call.message.answer(prompt, reply_markup=get_cancel_keyboard(), parse_mode=ParseMode.HTML)

@router.message(UserStates.writing_message)
async def process_user_ticket(message: Message, state: FSMContext):
    user = message.from_user
    data = await state.get_data()
    category = data.get("chosen_category", "عام")
    
    db_execute("INSERT INTO tickets (user_id, category) VALUES (?, ?)", (user.id, category), commit=True)
    ticket_id = db_execute("SELECT last_insert_rowid()", fetchone=True)[0]
    
    today = str(datetime.date.today())
    db_execute("UPDATE users SET last_sent_date = ? WHERE user_id = ?", (today, user.id), commit=True)
    
    user_info = f"👤 <b>المُرسِل:</b> {user.full_name} (@{user.username or 'بدون معرف'})\n🆔 <b>ID:</b> <code>{user.id}</code>"
    header = f"🎫 <b>تذكرة جديدة رقم #{ticket_id}</b>\n🏷 <b>القسم:</b> {category}\n{user_info}\n\n💬 <b>المحتوى:</b>\n"

    for admin_id in ADMIN_IDS:
        try:
            if message.text:
                sent = await bot.send_message(admin_id, header + message.text, reply_markup=get_admin_ticket_keyboard(ticket_id, user.id), parse_mode=ParseMode.HTML)
            elif message.photo:
                sent = await bot.send_photo(admin_id, message.photo[-1].file_id, caption=header + (message.caption or ""), reply_markup=get_admin_ticket_keyboard(ticket_id, user.id), parse_mode=ParseMode.HTML)
            elif message.document:
                sent = await bot.send_document(admin_id, message.document.file_id, caption=header + (message.caption or ""), reply_markup=get_admin_ticket_keyboard(ticket_id, user.id), parse_mode=ParseMode.HTML)
            elif message.voice:
                sent = await bot.send_voice(admin_id, message.voice.file_id, caption=header + (message.caption or ""), reply_markup=get_admin_ticket_keyboard(ticket_id, user.id), parse_mode=ParseMode.HTML)
            
            if 'sent' in locals():
                db_execute("UPDATE tickets SET admin_msg_id = ? WHERE ticket_id = ?", (sent.message_id, ticket_id), commit=True)
        except Exception as err:
            logger.error(f"Failed to forward ticket #{ticket_id} to admin {admin_id}: {err}")

    await state.clear()
    
    success_text = (
        f"🚀 <b>تم إرسال التذكرة بنجاح!</b>\n"
        f"📌 <b>الرقم المرجعي:</b> <code>#{ticket_id}</code>\n\n"
        f"سيصلك إشعار بالرد هنا فور معالجة التذكرة من قبل الإدارة."
    )
    await message.answer(success_text, reply_markup=ReplyKeyboardRemove(), parse_mode=ParseMode.HTML)

# -----------------------------------------------------------------------------
# أحداث الإدارة (Admin Handlers)
# -----------------------------------------------------------------------------
@router.message(Command("admin"), F.from_user.id.in_(ADMIN_IDS))
async def cmd_admin_panel(message: Message):
    total_users = db_execute("SELECT COUNT(*) FROM users", fetchone=True)[0]
    total_tickets = db_execute("SELECT COUNT(*) FROM tickets", fetchone=True)[0]

    panel_text = (
        f"📊 <b>لوحة تحكم الإدارة</b>\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"👥 <b>إجمالي الأعضاء:</b> <code>{total_users}</code>\n"
        f"🎫 <b>إجمالي التذاكر:</b> <code>{total_tickets}</code>\n\n"
        f"📢 لإرسال إشعار جماعي، اكتب: <code>/broadcast</code>"
    )
    await message.answer(panel_text, parse_mode=ParseMode.HTML)

@router.callback_query(F.data.startswith("reply_tkt_"), F.from_user.id.in_(ADMIN_IDS))
async def prepare_reply(call: CallbackQuery, state: FSMContext):
    parts = call.data.split("_")
    ticket_id = parts[2]
    target_user_id = parts[3]
    
    await state.update_data(reply_ticket_id=ticket_id, reply_target_user=target_user_id)
    await state.set_state(AdminStates.replying_ticket)
    
    await call.answer()
    await call.message.answer(f"📝 <b>اكتب ردك الموجه صاحب التذكرة #{ticket_id}:</b>", parse_mode=ParseMode.HTML)

@router.message(AdminStates.replying_ticket, F.from_user.id.in_(ADMIN_IDS))
async def send_admin_reply(message: Message, state: FSMContext):
    data = await state.get_data()
    ticket_id = data.get("reply_ticket_id")
    target_user_id = int(data.get("reply_target_user"))
    
    try:
        reply_payload = (
            f"📩 <b>تم استلام رد من الإدارة على تذكرتك #{ticket_id}:</b>\n\n"
            f"{message.text}"
        )
        await bot.send_message(target_user_id, reply_payload, parse_mode=ParseMode.HTML)
        db_execute("UPDATE tickets SET status = 'CLOSED' WHERE ticket_id = ?", (ticket_id,), commit=True)
        await message.answer(f"✅ تم إرسال الرد وإغلاق التذكرة #{ticket_id} بنجاح.")
    except Exception as err:
        logger.error(f"Failed to reply to user {target_user_id}: {err}")
        await message.answer("❌ تعذر إرسال الرد للمستخدم (ربما قام بحظر البوت).")
        
    await state.clear()

@router.callback_query(F.data.startswith("ban_usr_"), F.from_user.id.in_(ADMIN_IDS))
async def ban_user_callback(call: CallbackQuery):
    target_user_id = call.data.split("_")[2]
    db_execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (target_user_id,), commit=True)
    await call.answer("🚫 تم حظر المستخدم بنجاح.", show_alert=True)

@router.message(Command("broadcast"), F.from_user.id.in_(ADMIN_IDS))
async def start_broadcast(message: Message, state: FSMContext):
    await state.set_state(AdminStates.broadcasting)
    await message.answer("📢 <b>اكتب الرسالة المراد إرسالها لجميع المشتركين:</b>", parse_mode=ParseMode.HTML)

@router.message(AdminStates.broadcasting, F.from_user.id.in_(ADMIN_IDS))
async def do_broadcast(message: Message, state: FSMContext):
    users = db_execute("SELECT user_id FROM users WHERE is_banned = 0", fetchall=True)
    success_count = 0
    
    for u in users:
        try:
            await bot.send_message(u[0], f"📢 <b>تنويه عام من الإدارة:</b>\n\n{message.text}", parse_mode=ParseMode.HTML)
            success_count += 1
            await asyncio.sleep(0.04)
        except Exception:
            pass

    await state.clear()
    await message.answer(f"✅ اكتملت الإذاعة. تم التسليم لـ <code>{success_count}</code> مستخدم.", parse_mode=ParseMode.HTML)

# -----------------------------------------------------------------------------
# خادم الويب الخاص بـ Render وتشغيل البوت
# -----------------------------------------------------------------------------
async def handle_health_check(request):
    return web.Response(text="Bot is running live on Render!")

async def main():
    logger.info("جاري بدء تشغيل البوت...")
    await bot.delete_webhook(drop_pending_updates=True)
    
    # تشغيل خادم الويب الوهمي لإرضاء Render Web Service
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"تم تشغيل خادم الصحة الويب على المنفذ {port}")
    
    # بدء استلام رسائل تيليجرام
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("تم إيقاف تشغيل البوت.")
