import asyncio
import datetime
import logging
import os
import csv
import io
from typing import Any, Dict, Tuple

import psycopg2
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
    BufferedInputFile
)
from aiohttp import web

# -----------------------------------------------------------------------------
# الإعدادات الأساسية (تمت إضافة المديرين الجدد)
# -----------------------------------------------------------------------------
BOT_TOKEN = "8917050847:AAE3Ll5CIIv2o3FEXTudTJlnwhua3UOafc4"
ADMIN_IDS = [7449655663, 8309675653, 7753151510]

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres.xdapebqfdxnmzuuvziqg:Ahmed2010***Reda@aws-0-eu-central-1.pooler.supabase.com:6543/postgres"
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# -----------------------------------------------------------------------------
# إدارة قاعدة البيانات
# -----------------------------------------------------------------------------
def db_execute(sql: str, params: tuple = (), fetchone: bool = False, fetchall: bool = False, commit: bool = False) -> Any:
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            if commit: conn.commit()
            if fetchone: return cursor.fetchone()
            if fetchall: return cursor.fetchall()
            return None

def init_db() -> None:
    db_execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            is_banned INTEGER DEFAULT 0,
            last_sent_date TEXT
        );
    ''', commit=True)
    
    db_execute('''
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id SERIAL PRIMARY KEY,
            user_id BIGINT,
            category TEXT,
            admin_msg_id BIGINT,
            status TEXT DEFAULT 'OPEN',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
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
# الواجهات المساعدة
# -----------------------------------------------------------------------------
def get_user_status(user_id: int) -> Dict[str, bool]:
    today = str(datetime.date.today())
    row = db_execute("SELECT is_banned, last_sent_date FROM users WHERE user_id = %s", (user_id,), fetchone=True)
    if not row: return {"banned": False, "used_today": False}
    return {"banned": bool(row[0]), "used_today": (row[1] == today)}

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
        [InlineKeyboardButton(text="💡 تقديم اقتراح", callback_data="cat_اقتراح"), InlineKeyboardButton(text="⚠️ إبلاغ عن مشكلة", callback_data="cat_مشكلة")],
        [InlineKeyboardButton(text="❓ استفسار عام", callback_data="cat_استفسار")],
        [InlineKeyboardButton(text="🔄 تحديث حالة البوت 🟢", callback_data="refresh_status")]
    ])
    return text, kb

def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ إلغاء والعودة للقائمة الرئيسية")]], resize_keyboard=True)

def get_admin_ticket_keyboard(ticket_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 الرد (نص/وسائط)", callback_data=f"reply_tkt_{ticket_id}_{user_id}"), InlineKeyboardButton(text="🔒 إغلاق بدون رد", callback_data=f"close_tkt_{ticket_id}")],
        [InlineKeyboardButton(text="🚫 حظر", callback_data=f"ban_usr_{user_id}"), InlineKeyboardButton(text="✅ فك حظر", callback_data=f"unban_usr_{user_id}")],
        [InlineKeyboardButton(text="🔄 تصفير الحد اليومي", callback_data=f"reset_usr_{user_id}")]
    ])

# -----------------------------------------------------------------------------
# أوامر المشتركين
# -----------------------------------------------------------------------------
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user = message.from_user
    db_execute('''
        INSERT INTO users (user_id, username, full_name, last_sent_date) 
        VALUES (%s, %s, %s, '') 
        ON CONFLICT(user_id) DO UPDATE SET username = EXCLUDED.username, full_name = EXCLUDED.full_name
    ''', (user.id, user.username, user.full_name), commit=True)
    status = get_user_status(user.id)
    text, kb = build_main_menu(user.first_name, user.id, status["used_today"], status["banned"])
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@router.message(Command("mytickets"))
async def cmd_my_tickets(message: Message):
    rows = db_execute("SELECT ticket_id, category, status FROM tickets WHERE user_id = %s ORDER BY ticket_id DESC LIMIT 5", (message.from_user.id,), fetchall=True)
    if not rows:
        await message.answer("📭 لا يوجد تذاكر سابقة لك.")
        return
    text = "🎫 <b>سجل آخر 5 تذاكر لك:</b>\n\n"
    for r in rows:
        status = "مفتوحة ⏳" if r[2] == "OPEN" else "مغلقة ✅"
        text += f"▪️ تذكرة <code>#{r[0]}</code> | القسم: {r[1]} | الحالة: {status}\n"
    await message.answer(text, parse_mode=ParseMode.HTML)

@router.callback_query(F.data == "refresh_status")
async def refresh_status_cb(call: CallbackQuery):
    user = call.from_user
    status = get_user_status(user.id)
    text, kb = build_main_menu(user.first_name, user.id, status["used_today"], status["banned"])
    try:
        await call.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        await call.answer("🟢 تم تحديث البيانات بنجاح!", show_alert=False)
    except:
        await call.answer("🟢 البيانات محدثة بالفعل.", show_alert=False)

@router.message(F.text == "❌ إلغاء والعودة للقائمة الرئيسية")
async def cancel_handler(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🔄 تم إلغاء العملية.", reply_markup=ReplyKeyboardRemove())
    await cmd_start(message, state)

@router.callback_query(F.data.startswith("cat_"))
async def category_selected(call: CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    status = get_user_status(user_id)
    if status["banned"]: return await call.answer("❌ حسابك محظور من استخدام النظام.", show_alert=True)
    if status["used_today"]: return await call.answer("❌ استنفذت حدك اليومي (تذكرة واحدة كل 24 ساعة).", show_alert=True)

    category = call.data.split("_")[1]
    await state.update_data(chosen_category=category)
    await state.set_state(UserStates.writing_message)
    await call.message.delete()
    await call.message.answer(f"📝 <b>قسم: [{category}]</b>\nأرسل رسالتك الآن (نص، صورة، أو ملف):", reply_markup=get_cancel_keyboard(), parse_mode=ParseMode.HTML)

@router.message(UserStates.writing_message)
async def process_user_ticket(message: Message, state: FSMContext):
    user = message.from_user
    data = await state.get_data()
    category = data.get("chosen_category", "عام")
    
    ticket_id = db_execute("INSERT INTO tickets (user_id, category) VALUES (%s, %s) RETURNING ticket_id", (user.id, category), fetchone=True, commit=True)[0]
    db_execute("UPDATE users SET last_sent_date = %s WHERE user_id = %s", (str(datetime.date.today()), user.id), commit=True)
    
    user_info = f"👤 <b>المُرسِل:</b> {user.full_name} (@{user.username or 'لا يوجد'})\n🆔 <b>ID:</b> <code>{user.id}</code>"
    header = f"🎫 <b>تذكرة جديدة #{ticket_id}</b>\n🏷 <b>القسم:</b> {category}\n{user_info}\n\n💬 <b>المحتوى:</b>\n"

    for admin_id in ADMIN_IDS:
        try:
            kb = get_admin_ticket_keyboard(ticket_id, user.id)
            if message.text: sent = await bot.send_message(admin_id, header + message.text, reply_markup=kb, parse_mode=ParseMode.HTML)
            elif message.photo: sent = await bot.send_photo(admin_id, message.photo[-1].file_id, caption=header + (message.caption or ""), reply_markup=kb, parse_mode=ParseMode.HTML)
            elif message.document: sent = await bot.send_document(admin_id, message.document.file_id, caption=header + (message.caption or ""), reply_markup=kb, parse_mode=ParseMode.HTML)
            elif message.voice: sent = await bot.send_voice(admin_id, message.voice.file_id, caption=header + (message.caption or ""), reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception: pass

    await state.clear()
    await message.answer(f"🚀 <b>تم الإرسال بنجاح!</b>\n📌 <b>الرقم المرجعي:</b> <code>#{ticket_id}</code>\nسيصلك إشعار بالرد هنا.", reply_markup=ReplyKeyboardRemove(), parse_mode=ParseMode.HTML)

# -----------------------------------------------------------------------------
# أوامر الإدارة المتقدمة
# -----------------------------------------------------------------------------
@router.message(Command("admin"), F.from_user.id.in_(ADMIN_IDS))
async def cmd_admin_panel(message: Message):
    t_users = db_execute("SELECT COUNT(*) FROM users", fetchone=True)[0]
    t_banned = db_execute("SELECT COUNT(*) FROM users WHERE is_banned = 1", fetchone=True)[0]
    t_tickets = db_execute("SELECT COUNT(*) FROM tickets", fetchone=True)[0]
    t_open = db_execute("SELECT COUNT(*) FROM tickets WHERE status = 'OPEN'", fetchone=True)[0]

    panel = (
        f"📊 <b>لوحة تحكم الإدارة (Pro)</b>\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"👥 <b>إجمالي الأعضاء:</b> <code>{t_users}</code>\n"
        f"🚫 <b>المحظورين:</b> <code>{t_banned}</code>\n"
        f"🎫 <b>التذاكر الكلية:</b> <code>{t_tickets}</code> (المفتوحة: <code>{t_open}</code>)\n\n"
        f"🛠 <b>أوامر سريعة:</b>\n"
        f"<code>/ban ID</code> - لحظر شخص\n"
        f"<code>/unban ID</code> - لفك الحظر\n"
        f"<code>/export</code> - استخراج قاعدة البيانات CSV\n"
        f"<code>/broadcast</code> - إرسال رسالة للجميع"
    )
    await message.answer(panel, parse_mode=ParseMode.HTML)

@router.message(Command("ban"), F.from_user.id.in_(ADMIN_IDS))
async def cmd_ban(message: Message):
    args = message.text.split()
    if len(args) == 2 and args[1].isdigit():
        db_execute("UPDATE users SET is_banned = 1 WHERE user_id = %s", (int(args[1]),), commit=True)
        await message.answer(f"🚫 تم حظر المستخدم <code>{args[1]}</code> بنجاح.", parse_mode=ParseMode.HTML)

@router.message(Command("unban"), F.from_user.id.in_(ADMIN_IDS))
async def cmd_unban(message: Message):
    args = message.text.split()
    if len(args) == 2 and args[1].isdigit():
        db_execute("UPDATE users SET is_banned = 0 WHERE user_id = %s", (int(args[1]),), commit=True)
        await message.answer(f"✅ تم فك الحظر عن المستخدم <code>{args[1]}</code> بنجاح.", parse_mode=ParseMode.HTML)

@router.message(Command("export"), F.from_user.id.in_(ADMIN_IDS))
async def cmd_export(message: Message):
    rows = db_execute("SELECT user_id, username, full_name, is_banned, last_sent_date FROM users", fetchall=True)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["User ID", "Username", "Full Name", "Is Banned", "Last Sent Date"])
    writer.writerows(rows)
    file = BufferedInputFile(output.getvalue().encode('utf-8'), filename="users_backup.csv")
    await message.answer_document(file, caption="📊 نسخة احتياطية لجميع المشتركين (Excel/CSV).")

# -----------------------------------------------------------------------------
# تفاعلات الأزرار للإدارة (الرد، فك الحظر، إغلاق، تصفير)
# -----------------------------------------------------------------------------
@router.callback_query(F.data.startswith("reply_tkt_"), F.from_user.id.in_(ADMIN_IDS))
async def prepare_reply(call: CallbackQuery, state: FSMContext):
    parts = call.data.split("_")
    await state.update_data(reply_ticket_id=parts[2], reply_target_user=parts[3])
    await state.set_state(AdminStates.replying_ticket)
    await call.answer()
    await call.message.answer(f"📝 <b>اكتب ردك (أو أرسل صورة/ملف) للتذكرة #{parts[2]}:</b>", parse_mode=ParseMode.HTML)

@router.message(AdminStates.replying_ticket, F.from_user.id.in_(ADMIN_IDS))
async def send_admin_reply(message: Message, state: FSMContext):
    data = await state.get_data()
    ticket_id, target_user = data.get("reply_ticket_id"), int(data.get("reply_target_user"))
    reply_head = f"📩 <b>رد الإدارة على تذكرتك #{ticket_id}:</b>\n\n"
    
    try:
        if message.text: await bot.send_message(target_user, reply_head + message.text, parse_mode=ParseMode.HTML)
        elif message.photo: await bot.send_photo(target_user, message.photo[-1].file_id, caption=reply_head + (message.caption or ""), parse_mode=ParseMode.HTML)
        elif message.document: await bot.send_document(target_user, message.document.file_id, caption=reply_head + (message.caption or ""), parse_mode=ParseMode.HTML)
        elif message.voice: await bot.send_voice(target_user, message.voice.file_id, caption=reply_head + (message.caption or ""), parse_mode=ParseMode.HTML)
        
        db_execute("UPDATE tickets SET status = 'CLOSED' WHERE ticket_id = %s", (ticket_id,), commit=True)
        await message.answer(f"✅ تم إرسال الرد وإغلاق التذكرة #{ticket_id}.")
    except Exception:
        await message.answer("❌ تعذر إرسال الرد (ربما قام بحظر البوت).")
    await state.clear()

@router.callback_query(F.data.startswith("close_tkt_"), F.from_user.id.in_(ADMIN_IDS))
async def close_tkt_cb(call: CallbackQuery):
    t_id = call.data.split("_")[2]
    db_execute("UPDATE tickets SET status = 'CLOSED' WHERE ticket_id = %s", (t_id,), commit=True)
    await call.message.edit_text(call.message.html_text + "\n\n<b>[🔒 تم إغلاق التذكرة بدون رد]</b>", reply_markup=None)
    await call.answer("تم إغلاق التذكرة.", show_alert=True)

@router.callback_query(F.data.startswith("ban_usr_"), F.from_user.id.in_(ADMIN_IDS))
async def ban_usr_cb(call: CallbackQuery):
    u_id = call.data.split("_")[2]
    db_execute("UPDATE users SET is_banned = 1 WHERE user_id = %s", (u_id,), commit=True)
    await call.answer("🚫 تم حظر المستخدم بنجاح.", show_alert=True)

@router.callback_query(F.data.startswith("unban_usr_"), F.from_user.id.in_(ADMIN_IDS))
async def unban_usr_cb(call: CallbackQuery):
    u_id = call.data.split("_")[2]
    db_execute("UPDATE users SET is_banned = 0 WHERE user_id = %s", (u_id,), commit=True)
    await call.answer("✅ تم فك الحظر عن المستخدم بنجاح.", show_alert=True)

@router.callback_query(F.data.startswith("reset_usr_"), F.from_user.id.in_(ADMIN_IDS))
async def reset_usr_cb(call: CallbackQuery):
    u_id = call.data.split("_")[2]
    db_execute("UPDATE users SET last_sent_date = '' WHERE user_id = %s", (u_id,), commit=True)
    await call.answer("🔄 تم تصفير حد التذاكر! يمكنه إرسال تذكرة الآن.", show_alert=True)

# -----------------------------------------------------------------------------
# الإذاعة المتقدمة (مع شريط التقدم)
# -----------------------------------------------------------------------------
@router.message(Command("broadcast"), F.from_user.id.in_(ADMIN_IDS))
async def start_broadcast(message: Message, state: FSMContext):
    await state.set_state(AdminStates.broadcasting)
    await message.answer("📢 <b>أرسل الرسالة (نص، صورة، أو ملف) لإرسالها للجميع:</b>", parse_mode=ParseMode.HTML)

@router.message(AdminStates.broadcasting, F.from_user.id.in_(ADMIN_IDS))
async def do_broadcast(message: Message, state: FSMContext):
    users = db_execute("SELECT user_id FROM users WHERE is_banned = 0", fetchall=True)
    total = len(users)
    success = 0
    
    status_msg = await message.answer(f"⏳ <b>جاري الإذاعة...</b>\nالعدد المستهدف: {total}\nالناجح: 0", parse_mode=ParseMode.HTML)
    
    for idx, u in enumerate(users):
        try:
            if message.text: await bot.send_message(u[0], f"📢 <b>تنويه عام:</b>\n\n{message.text}", parse_mode=ParseMode.HTML)
            elif message.photo: await bot.send_photo(u[0], message.photo[-1].file_id, caption=message.caption, parse_mode=ParseMode.HTML)
            success += 1
        except Exception: pass
        
        if idx % 15 == 0 and idx > 0:
            await status_msg.edit_text(f"⏳ <b>جاري الإذاعة...</b>\nالعدد المستهدف: {total}\nتم الإرسال لـ: {success}", parse_mode=ParseMode.HTML)
        await asyncio.sleep(0.05) # حماية من حظر تيليجرام

    await state.clear()
    await status_msg.edit_text(f"✅ <b>اكتملت الإذاعة بنجاح!</b>\nوصلت الرسالة لـ: <code>{success}</code> من أصل <code>{total}</code>.", parse_mode=ParseMode.HTML)

# -----------------------------------------------------------------------------
# خادم الويب
# -----------------------------------------------------------------------------
async def handle_health_check(request):
    return web.Response(text="Bot is running smoothly!")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
