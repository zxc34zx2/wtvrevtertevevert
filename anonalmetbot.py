import logging
import sqlite3
import sys
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters, PreCheckoutQueryHandler
from telegram.constants import ParseMode


BOT_TOKEN = "8310201354:AAH_MIyv9q_YRpPbCoAbkS39oCb8UGRyzNg"
CHANNEL_ID = "@anonalmet" 
ADMIN_IDS = [6970104969]  

SPAM_COOLDOWN = 60  
PREMIUM_PRICE = 25  # 25 Stars за 1 месяц премиума


logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

user_cooldowns: Dict[int, datetime] = {}
pending_replies: Dict[int, tuple] = {}

# Список популярных премиум-эмодзи Telegram (расширенный)
PREMIUM_EMOJIS = [
    "🔥", "✨", "🌟", "💎", "🚀", "🎯", "🏆", "🎨", "🦄", "🌈",
    "⭐", "💫", "☄️", "🎭", "🎪", "🎮", "🎲", "🎵", "🎶", "🎸",
    "🏅", "🎖️", "🥇", "🥈", "🥉", "⚡", "💥", "🌠", "🌌", "🌙",
    "☀️", "🌞", "🪐", "🌊", "🌸", "🌺", "🌹", "🍀", "🎄", "🎁",
    "🎀", "🎊", "🎉", "🕹️", "🎬", "🎥", "📽️", "🎞️", "🎤", "🎧",
    "🐲", "🦁", "🐯", "🦊", "🐺", "🦋", "🐢", "🦉", "🦚", "🦜",
    "⚓", "⛵", "🚁", "🚂", "🚲", "🛵", "🏍️", "🚗", "🚕", "🚙",
    "🏠", "🏰", "🎡", "🎢", "🚧", "🛤️", "🗼", "🗽", "⛲", "🏟️",
    "🛒", "🛍️", "🎈", "🎏", "🎀", "🧸", "🪀", "🪁", "🧩", "♟️",
    "🎼", "🎹", "🥁", "🎷", "🎺", "🪕", "🎸", "🎤", "🎧", "📻"
]

class Database:
    def __init__(self):
        self.conn = sqlite3.connect('anonymous_bot.db', check_same_thread=False)
        self.upgrade_database()
    
    def upgrade_database(self):
        """Обновление структуры базы данных"""
        cursor = self.conn.cursor()
        
        # Таблица users с ВСЕМИ колонками
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                is_banned INTEGER DEFAULT 0,
                registration_date TEXT,
                is_premium INTEGER DEFAULT 0,
                custom_emoji TEXT DEFAULT "📨",
                premium_until TEXT DEFAULT NULL,
                emoji_type TEXT DEFAULT "standard",
                payment_history TEXT DEFAULT NULL,
                emoji_unique INTEGER DEFAULT 1,
                emoji_lock INTEGER DEFAULT 0,
                nickname TEXT DEFAULT NULL,
                message_count INTEGER DEFAULT 0
            )
        ''')
        
        # Таблица emoji_reservations (для уникальных эмодзи)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS emoji_reservations (
                emoji TEXT PRIMARY KEY,
                user_id INTEGER UNIQUE,
                reserved_at TEXT,
                is_premium INTEGER DEFAULT 1,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        ''')
        
        # Таблица messages
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_message_id INTEGER NOT NULL,
                text TEXT,
                timestamp TEXT NOT NULL,
                reply_to INTEGER DEFAULT NULL,
                is_reply INTEGER DEFAULT 0,
                emoji_used TEXT
            )
        ''')
        
        # Таблица replies
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS replies (
                reply_id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_message_id INTEGER,
                reply_message_id INTEGER,
                user_id INTEGER,
                timestamp TEXT
            )
        ''')
        
        # Таблица payments
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS payments (
                payment_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                currency TEXT DEFAULT "XTR",
                status TEXT DEFAULT "pending",
                timestamp TEXT NOT NULL,
                product TEXT,
                payload TEXT
            )
        ''')
        
        # Таблица used_emojis (история использованных эмодзи)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS used_emojis (
                emoji TEXT PRIMARY KEY,
                user_id INTEGER,
                last_used TEXT,
                use_count INTEGER DEFAULT 1
            )
        ''')
        
        self.conn.commit()
    
    def register_user(self, user_id: int, username: str, first_name: str, last_name: str):
        cursor = self.conn.cursor()
        cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
        if cursor.fetchone():
            cursor.execute('''
                UPDATE users 
                SET username = ?, first_name = ?, last_name = ?
                WHERE user_id = ?
            ''', (username, first_name, last_name, user_id))
        else:
            cursor.execute('''
                INSERT INTO users 
                (user_id, username, first_name, last_name, registration_date, custom_emoji, emoji_type)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, username, first_name, last_name, datetime.now().isoformat(), "📨", "standard"))
        self.conn.commit()
    
    def get_user_info(self, user_id: int) -> Optional[tuple]:
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        return result
    
    def get_user_by_username(self, username: str) -> Optional[tuple]:
        cursor = self.conn.cursor()
        if username.startswith('@'):
            username = username[1:]
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        result = cursor.fetchone()
        return result
    
    def is_user_banned(self, user_id: int) -> bool:
        user = self.get_user_info(user_id)
        if not user:
            return False
        return user[4] == 1
    
    def is_user_premium(self, user_id: int) -> bool:
        user = self.get_user_info(user_id)
        if not user:
            return False
        
        if user[8]:
            try:
                premium_until = datetime.fromisoformat(user[8])
                if datetime.now() > premium_until:
                    cursor = self.conn.cursor()
                    cursor.execute('''
                        UPDATE users 
                        SET is_premium = 0, premium_until = NULL 
                        WHERE user_id = ?
                    ''', (user_id,))
                    self.conn.commit()
                    
                    # Освобождаем зарезервированный эмодзи при истечении премиума
                    cursor.execute('DELETE FROM emoji_reservations WHERE user_id = ?', (user_id,))
                    self.conn.commit()
                    return False
            except:
                pass
        
        return user[6] == 1
    
    def get_user_emoji(self, user_id: int) -> str:
        user = self.get_user_info(user_id)
        if not user:
            return "📨"
        
        if user[7]:
            return user[7]
        
        return "📨"
    
    def get_user_emoji_type(self, user_id: int) -> str:
        user = self.get_user_info(user_id)
        if not user:
            return "standard"
        
        return user[9] if len(user) > 9 else "standard"
    
    def get_reserved_emoji_owner(self, emoji: str) -> Optional[int]:
        """Получить владельца зарезервированного эмодзи"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT user_id FROM emoji_reservations WHERE emoji = ?', (emoji,))
        result = cursor.fetchone()
        return result[0] if result else None
    
    def is_emoji_reserved(self, emoji: str) -> bool:
        """Проверить, зарезервирован ли эмодзи"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT 1 FROM emoji_reservations WHERE emoji = ?', (emoji,))
        return cursor.fetchone() is not None
    
    def reserve_emoji(self, user_id: int, emoji: str) -> bool:
        """Зарезервировать эмодзи для пользователя"""
        cursor = self.conn.cursor()
        
        # Проверяем, занят ли эмодзи
        if self.is_emoji_reserved(emoji):
            return False
        
        # Освобождаем предыдущий эмодзи пользователя
        cursor.execute('DELETE FROM emoji_reservations WHERE user_id = ?', (user_id,))
        
        # Резервируем новый эмодзи
        cursor.execute('''
            INSERT OR REPLACE INTO emoji_reservations (emoji, user_id, reserved_at, is_premium)
            VALUES (?, ?, ?, 1)
        ''', (emoji, user_id, datetime.now().isoformat()))
        
        self.conn.commit()
        return True
    
    def get_reserved_emoji_for_user(self, user_id: int) -> Optional[str]:
        """Получить зарезервированный эмодзи пользователя"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT emoji FROM emoji_reservations WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        return result[0] if result else None
    
    def set_user_premium(self, user_id: int, months: int = 1, emoji_type: str = "premium"):
        cursor = self.conn.cursor()
        premium_until = datetime.now() + timedelta(days=30 * months)
        cursor.execute('''
            UPDATE users 
            SET is_premium = 1, premium_until = ?, emoji_type = ?, emoji_unique = 1
            WHERE user_id = ?
        ''', (premium_until.isoformat(), emoji_type, user_id))
        self.conn.commit()
    
    def set_user_emoji(self, user_id: int, emoji: str, emoji_type: str = None):
        cursor = self.conn.cursor()
        
        if emoji_type is None:
            emoji_type = self.detect_emoji_type(emoji)
        
        # Устанавливаем эмодзи в таблицу users
        cursor.execute('UPDATE users SET custom_emoji = ?, emoji_type = ? WHERE user_id = ?', 
                      (emoji, emoji_type, user_id))
        self.conn.commit()
        return True
    
    def set_user_emoji_with_reservation(self, user_id: int, emoji: str, emoji_type: str = None) -> bool:
        """Установить эмодзи с закреплением (только для премиум)"""
        cursor = self.conn.cursor()
        
        if emoji_type is None:
            emoji_type = self.detect_emoji_type(emoji)
        
        # Проверяем, является ли пользователь премиум
        if not self.is_user_premium(user_id):
            # Для не-премиум просто устанавливаем эмодзи
            return self.set_user_emoji(user_id, emoji, emoji_type)
        
        # Для премиум пользователей - закрепляем эмодзи
        if not self.reserve_emoji(user_id, emoji):
            return False
        
        # Устанавливаем эмодзи в таблицу users
        cursor.execute('UPDATE users SET custom_emoji = ?, emoji_type = ? WHERE user_id = ?', 
                      (emoji, emoji_type, user_id))
        self.conn.commit()
        return True
    
    def detect_emoji_type(self, emoji: str) -> str:
        if emoji in PREMIUM_EMOJIS:
            return "premium"
        
        if '\uFE0F' in emoji:
            return "premium"
        
        return "standard"
    
    def log_used_emoji(self, user_id: int, emoji: str):
        """Записать использование эмодзи в историю"""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO used_emojis (emoji, user_id, last_used, use_count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(emoji) 
            DO UPDATE SET 
                use_count = use_count + 1,
                last_used = ?
        ''', (emoji, user_id, datetime.now().isoformat(), datetime.now().isoformat()))
        self.conn.commit()
    
    def get_emoji_usage_count(self, emoji: str) -> int:
        """Получить количество использований эмодзи"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT use_count FROM used_emojis WHERE emoji = ?', (emoji,))
        result = cursor.fetchone()
        return result[0] if result else 0
    
    def get_all_reserved_emojis(self) -> List[tuple]:
        """Получить все зарезервированные эмодзи"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT e.emoji, u.user_id, u.username, u.first_name, e.reserved_at
            FROM emoji_reservations e
            JOIN users u ON e.user_id = u.user_id
            ORDER BY e.reserved_at DESC
        ''')
        return cursor.fetchall()
    
    def get_available_emojis(self) -> List[str]:
        """Получить список доступных (не занятых) премиум эмодзи"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT emoji FROM emoji_reservations')
        reserved_emojis = {row[0] for row in cursor.fetchall()}
        
        available_emojis = [emoji for emoji in PREMIUM_EMOJIS if emoji not in reserved_emojis]
        return available_emojis
    
    def get_user_nickname(self, user_id: int) -> Optional[str]:
        """Получить никнейм пользователя"""
        user = self.get_user_info(user_id)
        if not user or len(user) <= 13:
            return None
        return user[13]
    
    def set_user_nickname(self, user_id: int, nickname: str):
        """Установить никнейм пользователя"""
        cursor = self.conn.cursor()
        cursor.execute('UPDATE users SET nickname = ? WHERE user_id = ?', (nickname, user_id))
        self.conn.commit()
    
    def increment_message_count(self, user_id: int):
        """Увеличить счетчик сообщений пользователя"""
        cursor = self.conn.cursor()
        cursor.execute('UPDATE users SET message_count = message_count + 1 WHERE user_id = ?', (user_id,))
        self.conn.commit()
    
    # Методы для работы с платежами
    def log_payment(self, payment_id: str, user_id: int, amount: int, product: str, payload: str):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO payments (payment_id, user_id, amount, currency, status, timestamp, product, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (payment_id, user_id, amount, "XTR", "completed", datetime.now().isoformat(), product, payload))
        self.conn.commit()
    
    def get_user_payments(self, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM payments WHERE user_id = ? ORDER BY timestamp DESC', (user_id,))
        return cursor.fetchall()
    
    def log_message(self, user_id: int, channel_message_id: int, text: str, reply_to: int = None, emoji_used: str = None):
        cursor = self.conn.cursor()
        is_reply = 1 if reply_to is not None else 0
        timestamp = datetime.now().isoformat()
        
        try:
            cursor.execute('''
                INSERT INTO messages (user_id, channel_message_id, text, timestamp, reply_to, is_reply, emoji_used)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, channel_message_id, text or '', timestamp, reply_to, is_reply, emoji_used))
            
            # Увеличиваем счетчик сообщений
            cursor.execute('UPDATE users SET message_count = message_count + 1 WHERE user_id = ?', (user_id,))
            
            # Логируем использование эмодзи
            if emoji_used:
                self.log_used_emoji(user_id, emoji_used)
            
            self.conn.commit()
            
            if reply_to is not None:
                cursor.execute('''
                    INSERT INTO replies (original_message_id, reply_message_id, user_id, timestamp)
                    VALUES (?, ?, ?, ?)
                ''', (reply_to, channel_message_id, user_id, timestamp))
                self.conn.commit()
                
        except Exception as e:
            logger.error(f"Error logging message: {e}")
            self.conn.rollback()
            raise
    
    def get_message_sender(self, channel_message_id: int):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT users.* FROM messages
            JOIN users ON messages.user_id = users.user_id
            WHERE messages.channel_message_id = ?
        ''', (channel_message_id,))
        return cursor.fetchone()
    
    def get_user_from_message(self, message_id: int) -> Optional[tuple]:
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT users.* FROM messages
            JOIN users ON messages.user_id = users.user_id
            WHERE messages.channel_message_id = ?
        ''', (message_id,))
        return cursor.fetchone()
    
    def get_message_info(self, message_id: int):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM messages WHERE channel_message_id = ?', (message_id,))
        return cursor.fetchone()
    
    def get_replies_count(self, message_id: int) -> int:
        cursor = self.conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM replies WHERE original_message_id = ?', (message_id,))
        result = cursor.fetchone()
        return result[0] if result else 0
    
    def get_replies(self, message_id: int):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT replies.*, users.username, users.first_name, users.last_name 
            FROM replies
            JOIN users ON replies.user_id = users.user_id
            WHERE original_message_id = ?
            ORDER BY timestamp DESC
        ''', (message_id,))
        return cursor.fetchall()
    
    def ban_user(self, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute('UPDATE users SET is_banned = 1 WHERE user_id = ?', (user_id,))
        self.conn.commit()
    
    def ban_user_by_username(self, username: str):
        cursor = self.conn.cursor()
        if username.startswith('@'):
            username = username[1:]
        cursor.execute('UPDATE users SET is_banned = 1 WHERE username = ?', (username,))
        self.conn.commit()
    
    def unban_user(self, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute('UPDATE users SET is_banned = 0 WHERE user_id = ?', (user_id,))
        self.conn.commit()
    
    def unban_user_by_username(self, username: str):
        cursor = self.conn.cursor()
        if username.startswith('@'):
            username = username[1:]
        cursor.execute('UPDATE users SET is_banned = 0 WHERE username = ?', (username,))
        self.conn.commit()
    
    def get_all_users(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM users ORDER BY registration_date DESC')
        return cursor.fetchall()
    
    def get_premium_users(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM users WHERE is_premium = 1 ORDER BY premium_until DESC')
        return cursor.fetchall()

db = Database()

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def check_spam_cooldown(user_id: int) -> Optional[str]:
    now = datetime.now()
    
    if user_id in user_cooldowns:
        last_time = user_cooldowns[user_id]
        time_diff = (now - last_time).total_seconds()
        
        if time_diff < SPAM_COOLDOWN:
            wait_time = int(SPAM_COOLDOWN - time_diff)
            return f"⏳ Подождите {wait_time} секунд перед отправкой следующего сообщения."
    
    user_cooldowns[user_id] = now
    return None

def validate_emoji(emoji: str) -> bool:
    if not emoji or len(emoji.strip()) == 0:
        return False
    
    if len(emoji) > 4:
        return False
    
    return True

# ===================== СТАРТ КОМАНДА =====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Регистрируем пользователя
    db.register_user(
        user.id, 
        user.username if user.username else None, 
        user.first_name if user.first_name else None, 
        user.last_name if user.last_name else None
    )
    
    # Простое приветствие
    welcome_text = (
        "👋 *Анонимный бот*\n\n"
        "📢 Канал: @anonalmet\n\n"
        "Просто отправьте сообщение, фото или видео - оно будет в канале.\n"
        "✉️ Для ответа на сообщение перешлите его из канала\n\n"
        "Все сообщения отправляются анонимно! 👤"
    )
    
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)

# ===================== СТАТИСТИКА (ТОЛЬКО ДЛЯ АДМИНА) =====================

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика пользователя (только для админа)"""
    user = update.effective_user
    
    if not is_admin(user.id):
        await update.message.reply_text(
            "❌ Эта команда доступна только администраторам.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    user_info = db.get_user_info(user.id)
    if not user_info:
        await update.message.reply_text(
            "❌ Вы не зарегистрированы в системе.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    is_premium = db.is_user_premium(user.id)
    message_count = user_info[14] if len(user_info) > 14 else 0
    current_emoji = db.get_user_emoji(user.id)
    reserved_emoji = db.get_reserved_emoji_for_user(user.id)
    
    text = "📊 *Статистика (только для админа)*\n\n"
    
    text += f"👤 *Ваш профиль:*\n"
    text += f"ID: `{user.id}`\n"
    if user.username:
        text += f"Username: @{user.username}\n"
    if user.first_name:
        text += f"Имя: {user.first_name}\n"
    if user.last_name:
        text += f"Фамилия: {user.last_name}\n"
    
    text += f"\n📈 *Активность:*\n"
    text += f"Сообщений отправлено: {message_count}\n"
    
    text += f"\n🎨 *Эмодзи:*\n"
    text += f"Текущий эмодзи: {current_emoji}\n"
    if reserved_emoji:
        if reserved_emoji == current_emoji:
            text += f"Статус: 🔒 Уникальный закрепленный\n"
        else:
            text += f"Статус: ⚠️ Закреплен {reserved_emoji}\n"
    else:
        text += f"Статус: 📍 Не закреплен\n"
    
    text += f"\n✨ *Премиум статус:*\n"
    if is_premium:
        premium_until = "неизвестно"
        if user_info[8]:
            try:
                until_date = datetime.fromisoformat(user_info[8])
                days_left = (until_date - datetime.now()).days
                premium_until = until_date.strftime("%d.%m.%Y")
                text += f"✅ Активен (осталось {days_left} дней)\n"
                text += f"Действует до: {premium_until}\n"
            except:
                text += f"✅ Активен\n"
    else:
        text += f"❌ Не активен\n"
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ===================== АДМИН КОМАНДЫ =====================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /admin"""
    user = update.effective_user
    
    if not is_admin(user.id):
        await update.message.reply_text("❌ Нет прав доступа.")
        return
    
    keyboard = [
        [InlineKeyboardButton("👥 Список пользователей", callback_data='list_users')],
        [InlineKeyboardButton("✨ Premium пользователи", callback_data='list_premium_users')],
        [InlineKeyboardButton("🚫 Забанить", callback_data='ban_options')],
        [InlineKeyboardButton("✅ Разбанить", callback_data='unban_options')],
        [InlineKeyboardButton("🔍 Найти отправителя", callback_data='find_options')],
        [InlineKeyboardButton("🔒 Зарезервированные эмодзи", callback_data='admin_reserved')],
        [InlineKeyboardButton("📊 Статистика", callback_data='user_stats')],
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔐 *Админ-панель*\nВыберите действие:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup
    )

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Нет прав доступа.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "🚫 *Бан пользователя*\n\n"
            "Использование:\n"
            "`/ban @username` - забанить по username\n"
            "`/ban ID` - забанить по ID\n\n"
            "Пример:\n"
            "`/ban @spammer`\n"
            "`/ban 123456789`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    target = context.args[0]
    
    try:
        if target.startswith('@'):
            db.ban_user_by_username(target)
            await update.message.reply_text(f"✅ Пользователь `{target}` забанен.", parse_mode=ParseMode.MARKDOWN)
        else:
            user_id = int(target)
            db.ban_user(user_id)
            await update.message.reply_text(f"✅ Пользователь `{user_id}` забанен.", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await update.message.reply_text("❌ Неверный формат. Используйте ID или @username.")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Нет прав доступа.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "✅ *Разбан пользователя*\n\n"
            "Использование:\n"
            "`/unban @username` - разбанить по username\n"
            "`/unban ID` - разбанить по ID\n\n"
            "Пример:\n"
            "`/unban @user123`\n"
            "`/unban 123456789`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    target = context.args[0]
    
    try:
        if target.startswith('@'):
            db.unban_user_by_username(target)
            await update.message.reply_text(f"✅ Пользователь `{target}` разбанен.", parse_mode=ParseMode.MARKDOWN)
        else:
            user_id = int(target)
            db.unban_user(user_id)
            await update.message.reply_text(f"✅ Пользователь `{user_id}` разбанен.", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await update.message.reply_text("❌ Неверный формат. Используйте ID или @username.")

async def find_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Нет прав доступа.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "🔍 *Поиск отправителя*\n\n"
            "Использование:\n"
            "`/find ID_сообщения` - найти отправителя сообщения\n\n"
            "Пример:\n"
            "`/find 123`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    try:
        message_id = int(context.args[0])
        sender = db.get_message_sender(message_id)
        
        if sender:
            user_id = sender[0]
            username = f"@{sender[1]}" if sender[1] else "Нет username"
            first_name = sender[2] or "Не указано"
            last_name = sender[3] or "Не указано"
            is_banned = "Да" if sender[4] else "Нет"
            is_premium = "Да" if sender[6] else "Нет"
            
            await update.message.reply_text(
                f"👤 *Информация об отправителе*\n\n"
                f"🆔 ID: `{user_id}`\n"
                f"📛 Username: {username}\n"
                f"👤 Имя: {first_name}\n"
                f"👥 Фамилия: {last_name}\n"
                f"🚫 Забанен: {is_banned}\n"
                f"✨ Premium: {is_premium}\n"
                f"🎨 Эмодзи: {sender[7] if len(sender) > 7 else '📨'}",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text("❌ Сообщение не найдено.")
    except ValueError:
        await update.message.reply_text("❌ Неверный формат ID сообщения.")

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Нет прав доступа.")
        return
    
    all_users = db.get_all_users()
    
    if not all_users:
        await update.message.reply_text("📭 Нет зарегистрированных пользователей.")
        return
    
    text = f"👥 *Все пользователи: {len(all_users)}*\n\n"
    
    for i, user in enumerate(all_users[:50], 1):  # Показываем первые 50
        user_id = user[0]
        username = f"@{user[1]}" if user[1] else "Нет username"
        first_name = user[2] or ""
        last_name = user[3] or ""
        is_banned = "🚫" if user[4] else "✅"
        is_premium = "✨" if user[6] else "📱"
        
        text += f"{i}. {is_banned}{is_premium} `{user_id}` {username}\n"
        if first_name or last_name:
            text += f"   👤 {first_name} {last_name}\n"
    
    if len(all_users) > 50:
        text += f"\n... и еще {len(all_users) - 50} пользователей"
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def admin_reserved_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ: просмотр зарезервированных эмодзи"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Нет прав доступа.")
        return
    
    reserved_emojis = db.get_all_reserved_emojis()
    
    text = "🔒 *Зарезервированные эмодзи*\n\n"
    
    if reserved_emojis:
        text += f"Всего зарезервировано: {len(reserved_emojis)} эмодзи\n\n"
        
        for emoji, user_id, username, first_name, reserved_at in reserved_emojis:
            name = f"@{username}" if username else f"{first_name or f'ID {user_id}'}"
            try:
                reserved_date = datetime.fromisoformat(reserved_at)
                date_str = reserved_date.strftime("%d.%m.%Y %H:%M")
            except:
                date_str = reserved_at
            
            text += f"• {emoji} - {name} (ID: `{user_id}`)\n"
            text += f"  📅 Зарезервирован: {date_str}\n"
        
        text += f"\n*Всего доступно эмодзи:* {len(PREMIUM_EMOJIS)}\n"
        text += f"*Свободно:* {len(PREMIUM_EMOJIS) - len(reserved_emojis)}\n"
        text += f"*Занято:* {len(reserved_emojis)}\n"
    else:
        text += "Нет зарезервированных эмодзи\n"
        text += f"Все {len(PREMIUM_EMOJIS)} эмодзи доступны для выбора"
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def admin_free_emoji_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ: освобождение эмодзи"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Нет прав доступа.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "🗑️ *Освобождение эмодзи*\n\n"
            "Использование:\n"
            "`/freeemoji @username` - освободить эмодзи пользователя\n"
            "`/freeemoji ID` - освободить эмодзи по ID\n"
            "`/freeemoji 🔥` - освободить конкретный эмодзи\n\n"
            "Пример:\n"
            "`/freeemoji @user123`\n"
            "`/freeemoji 123456789`\n"
            "`/freeemoji 🔥`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    target = context.args[0]
    cursor = db.conn.cursor()
    
    try:
        # Если это эмодзи
        if validate_emoji(target):
            cursor.execute('DELETE FROM emoji_reservations WHERE emoji = ?', (target,))
            db.conn.commit()
            await update.message.reply_text(
                f"✅ Эмодзи {target} успешно освобожден!",
                parse_mode=ParseMode.MARKDOWN
            )
        # Если это username или ID
        else:
            if target.startswith('@'):
                user_info = db.get_user_by_username(target)
                if user_info:
                    cursor.execute('DELETE FROM emoji_reservations WHERE user_id = ?', (user_info[0],))
                    db.conn.commit()
                    await update.message.reply_text(
                        f"✅ Эмодзи пользователя `{target}` освобожден!",
                        parse_mode=ParseMode.MARKDOWN
                    )
                else:
                    await update.message.reply_text(f"❌ Пользователь `{target}` не найден.", parse_mode=ParseMode.MARKDOWN)
            else:
                user_id = int(target)
                user_info = db.get_user_info(user_id)
                
                if user_info:
                    cursor.execute('DELETE FROM emoji_reservations WHERE user_id = ?', (user_id,))
                    db.conn.commit()
                    await update.message.reply_text(
                        f"✅ Эмодзи пользователя `{user_id}` освобожден!",
                        parse_mode=ParseMode.MARKDOWN
                    )
                else:
                    await update.message.reply_text(f"❌ Пользователь `{user_id}` не найден.", parse_mode=ParseMode.MARKDOWN)
    
    except ValueError:
        await update.message.reply_text("❌ Неверный формат. Используйте @username, ID или эмодзи.")

# ===================== УНИКАЛЬНЫЕ ЭМОДЗИ =====================

async def emoji_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if not db.is_user_premium(user.id):
        await update.message.reply_text(
            "❌ Эта функция доступна только для премиум пользователей.\n\n"
            "Используйте /premium чтобы узнать больше или /buy_premium чтобы приобрести.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    if not context.args:
        current_emoji = db.get_user_emoji(user.id)
        emoji_type = db.get_user_emoji_type(user.id)
        type_text = "⭐ Премиум" if emoji_type == "premium" else "📱 Стандартный"
        
        # Проверяем, зарезервирован ли текущий эмодзи
        reserved_emoji = db.get_reserved_emoji_for_user(user.id)
        
        text = (
            f"🎨 *Смена эмодзи*\n\n"
            f"Текущий эмодзи: {current_emoji} ({type_text})\n"
        )
        
        if reserved_emoji:
            if reserved_emoji == current_emoji:
                text += f"🔒 *Зарезервирован за вами*\n\n"
            else:
                text += f"⚠️ *Закреплен другой эмодзи: {reserved_emoji}*\n\n"
        else:
            text += f"⚠️ *Не зарезервирован*\n\n"
        
        text += (
            f"*Использование:*\n"
            f"`/emoji [эмодзи]` - выбрать и закрепить эмодзи\n\n"
            f"*Примеры:*\n"
            f"`/emoji 🔥` - закрепить огонь за собой\n"
            f"`/emoji ✨` - закрепить искры за собой\n\n"
            f"*Посмотреть доступные эмодзи:*\n"
            f"`/availableemojis`"
        )
        
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        return
    
    emoji = context.args[0]
    
    if not validate_emoji(emoji):
        await update.message.reply_text(
            "❌ Пожалуйста, используйте валидный эмодзи.\n"
            "Например: `/emoji 🔥` или `/emoji ✨`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Проверяем, не занят ли эмодзи
    reserved_owner = db.get_reserved_emoji_owner(emoji)
    if reserved_owner and reserved_owner != user.id:
        # Проверяем, является ли пользователь админом
        if is_admin(user.id):
            # Админ видит реального владельца
            owner_info = db.get_user_info(reserved_owner)
            owner_name = f"@{owner_info[1]}" if owner_info and owner_info[1] else f"ID: {reserved_owner}"
            
            await update.message.reply_text(
                f"🔒 *Только для админа:*\n\n"
                f"❌ Эмодзи {emoji} уже закреплен за пользователем {owner_name}\n\n"
                f"Если нужно, освободите его командой:\n"
                f"`/freeemoji {emoji}`",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            # Обычные пользователи видят общее сообщение
            await update.message.reply_text(
                f"❌ Этот эмодзи уже занят.\n\n"
                f"Используйте команду `/availableemojis` чтобы увидеть свободные эмодзи.",
                parse_mode=ParseMode.MARKDOWN
            )
        return
    
    # Устанавливаем эмодзи с закреплением
    emoji_type = db.detect_emoji_type(emoji)
    success = db.set_user_emoji_with_reservation(user.id, emoji, emoji_type)
    
    if not success:
        await update.message.reply_text(
            "❌ Не удалось закрепить эмодзи. Попробуйте другой.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    type_text = "⭐ Премиум эмодзи" if emoji_type == "premium" else "📱 Стандартный эмодзи"
    
    await update.message.reply_text(
        f"✅ Эмодзи успешно закреплен за вами!\n\n"
        f"Новый эмодзи: {emoji}\n"
        f"Тип: {type_text}\n"
        f"Статус: 🔒 *Уникальный закрепленный эмодзи*\n\n"
        f"Теперь этот эмодзи закреплен только за вами!\n"
        f"Другие пользователи не смогут его использовать.",
        parse_mode=ParseMode.MARKDOWN
    )

async def availableemojis_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать доступные эмодзи для закрепления"""
    user = update.effective_user
    
    if not db.is_user_premium(user.id):
        await update.message.reply_text(
            "❌ Эта функция доступна только для премиум пользователей.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Получаем доступные и занятые эмодзи
    available_emojis = db.get_available_emojis()
    reserved_emojis = db.get_all_reserved_emojis()
    
    text = "📋 *Доступные эмодзи для закрепления*\n\n"
    
    if available_emojis:
        text += f"✅ *Свободно: {len(available_emojis)} эмодзи*\n\n"
        
        # Показываем доступные эмодзи группами
        for i in range(0, len(available_emojis), 10):
            group = available_emojis[i:i+10]
            text += " ".join(group) + "\n"
        
        text += f"\nИспользуйте `/emoji [эмодзи]` чтобы закрепить\n"
        text += f"Пример: `/emoji {available_emojis[0] if available_emojis else '🔥'}`\n\n"
    else:
        text += "😔 *Все эмодзи заняты*\n\n"
    if reserved_emojis:
        # Для админов показываем детали, для обычных пользователей - только количество
        if is_admin(user.id):
            for i, (emoji, user_id, username, first_name, reserved_at) in enumerate(reserved_emojis[:5], 1):
                name = f"@{username}" if username else f"{first_name or f'ID {user_id}'}"
                text += f"{i}. {emoji} - {name}\n"
            
            if len(reserved_emojis) > 5:
                text += f"... и еще {len(reserved_emojis) - 5} занятых эмодзи\n"
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def myreservations_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Мои зарезервированные эмодзи"""
    user = update.effective_user
    
    if not db.is_user_premium(user.id):
        await update.message.reply_text(
            "❌ Эта функция доступна только для премиум пользователей.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    current_emoji = db.get_user_emoji(user.id)
    reserved_emoji = db.get_reserved_emoji_for_user(user.id)
    
    text = "🔒 *Мои зарезервированные эмодзи*\n\n"
    
    if reserved_emoji:
        text += f"✅ Текущий закрепленный эмодзи: {reserved_emoji}\n"
        
        if current_emoji == reserved_emoji:
            text += f"📝 Используется в сообщениях: Да\n"
        else:
            text += f"⚠️ Внимание: В настройках установлен другой эмодзи\n"
            text += f"📝 Текущий эмодзи: {current_emoji}\n"
        
        # Информация о статусе премиума
        user_info = db.get_user_info(user.id)
        if user_info and user_info[8]:
            try:
                until_date = datetime.fromisoformat(user_info[8])
                days_left = (until_date - datetime.now()).days
                text += f"📅 Эмодзи закреплен до окончания премиума ({days_left} дней)\n"
            except:
                pass
        
        text += f"\n*Для смены эмодзи:*\n"
        text += f"Используйте `/emoji [новый_эмодзи]`\n"
        text += f"Старый эмодзи будет освобожден автоматически.\n"
    else:
        text += f"⚠️ У вас нет закрепленных эмодзи\n\n"
        text += f"*Как закрепить эмодзи:*\n"
        text += f"1. Используйте `/availableemojis` для просмотра доступных\n"
        text += f"2. Выберите понравившийся эмодзи\n"
        text += f"3. Используйте `/emoji [эмодзи]` для закрепления\n\n"
        text += f"*Текущий эмодзи:* {current_emoji}\n"
        text += f"⚠️ Этот эмодзи не закреплен и могут использовать другие"
    
    text += f"\n*Преимущества закрепления:*\n"
    text += f"• Уникальность - эмодзи только ваш\n"
    text += f"• Узнаваемость - другие видят ваш уникальный стиль\n"
    text += f"• Эксклюзивность - доступно только премиум пользователям"
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def myemoji_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /myemoji для просмотра текущего эмодзи"""
    user = update.effective_user
    
    current_emoji = db.get_user_emoji(user.id)
    is_premium = db.is_user_premium(user.id)
    emoji_type = db.get_user_emoji_type(user.id)
    reserved_emoji = db.get_reserved_emoji_for_user(user.id)
    
    if is_premium:
        type_text = "⭐ Премиум" if emoji_type == "premium" else "📱 Стандартный"
        
        text = (
            f"🎨 *Ваш эмодзи*\n\n"
            f"Текущий эмодзи: {current_emoji}\n"
            f"Тип: {type_text}\n"
            f"Статус: ✅ Premium активен\n"
        )
        
        if reserved_emoji:
            if reserved_emoji == current_emoji:
                text += f"🔒 *Эмодзи закреплен за вами*\n\n"
            else:
                text += f"⚠️ *Закреплен другой эмодзи: {reserved_emoji}*\n\n"
        else:
            text += f"⚠️ *Эмодзи не закреплен*\n\n"
        
        text += (
            f"*Изменить эмодзи:*\n"
            f"`/emoji [новый_эмодзи]`\n"
            f"Пример: `/emoji ✨`\n\n"
            f"*Посмотреть доступные эмодзи:*\n"
            f"`/availableemojis`\n\n"
            f"*Мои закрепленные эмодзи:*\n"
            f"`/myreservations`"
        )
    else:
        text = (
            f"🎨 *Ваш эмодзи*\n\n"
            f"Текущий эмодзи: {current_emoji}\n"
            f"Статус: ❌ Premium не активен\n\n"
            f"*Получить премиум:*\n"
            f"`/premium` - узнать о премиуме\n"
            f"`/buy_premium` - купить премиум за {PREMIUM_PRICE}⭐\n\n"
            f"С премиумом вы сможете:\n"
            f"• Закрепить уникальный эмодзи за собой 🔒\n"
            f"• Использовать премиум эмодзи Telegram ⭐\n\n"
            f"*Поддержка:* @anonaltshelper"
        )
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ===================== PREMIUM КОМАНДЫ =====================

async def buy_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Покупка премиум подписки через Telegram Stars"""
    user = update.effective_user
    
    if db.is_user_premium(user.id):
        await update.message.reply_text(
            "✅ У вас уже есть активная премиум подписка!\n"
            "Используйте /myemoji чтобы посмотреть ваш текущий эмодзи.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    text = (
        f"✨ *Anon Premium - 1 месяц*\n\n"
        f"*Стоимость:* {PREMIUM_PRICE} звезд Telegram ⭐\n\n"
        f"*Включает:*\n"
        f"✅ Уникальный закрепленный эмодзи 🔒\n"
        f"✅ Премиум эмодзи Telegram ⭐\n\n"
        f"*Особенность:*\n"
        f"• Выберите любой эмодзи и закрепите его за собой\n"
        f"• Этот эмодзи станет вашей уникальной подписью\n"
        f"• Никто другой не сможет его использовать\n"
        f"• Эмодзи освобождается при отмене подписки\n\n"
        f"*Поддержка:* @anonaltshelper"
    )
    
    keyboard = [[
        InlineKeyboardButton(
            text=f"💳 Купить Premium за {PREMIUM_PRICE} ⭐",
            pay=True
        )
    ]]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        await asyncio.sleep(1)
        await update.message.reply_invoice(
            title="Anon Premium - 1 месяц",
            description=f"Премиум подписка на 1 месяц\nУникальный эмодзи",
            payload=f"premium_1month_{user.id}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="Premium Subscription", amount=PREMIUM_PRICE)],
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error creating invoice: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при создании счета. Попробуйте позже.",
            parse_mode=ParseMode.MARKDOWN
        )

async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик предварительной проверки платежа"""
    query = update.pre_checkout_query
    
    # Проверяем payload
    payload = query.invoice_payload
    if not payload.startswith("premium_1month_"):
        await query.answer(ok=False, error_message="Неверный тип товара")
        return
    
    try:
        user_id = int(payload.split("_")[-1])
        user = db.get_user_info(user_id)
        
        if not user:
            await query.answer(ok=False, error_message="Пользователь не найден")
            return
        
        # Проверяем, не купил ли уже пользователь премиум
        if db.is_user_premium(user_id):
            await query.answer(ok=False, error_message="У вас уже есть активная подписка")
            return
        
        await query.answer(ok=True)
    except Exception as e:
        logger.error(f"Error in pre_checkout: {e}")
        await query.answer(ok=False, error_message="Произошла ошибка")

async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик успешной оплаты"""
    user = update.effective_user
    payment = update.message.successful_payment
    
    try:
        # Логируем платеж
        db.log_payment(
            payment_id=payment.telegram_payment_charge_id,
            user_id=user.id,
            amount=payment.total_amount,
            product="premium_1month",
            payload=payment.invoice_payload
        )
        
        # Активируем премиум
        db.set_user_premium(user.id, months=1, emoji_type="premium")
        
        # Отправляем поздравление
        text = (
            f"🎉 *Поздравляем!*\n\n"
            f"✅ Премиум подписка активирована на 1 месяц!\n\n"
            f"✨ *Теперь вам доступно:*\n"
            f"• Уникальный закрепленный эмодзи 🔒\n"
            f"• Выбор из {len(PREMIUM_EMOJIS)} премиум эмодзи ⭐\n\n"
            f"*Как закрепить эмодзи:*\n"
            f"1. Используйте `/availableemojis`\n"
            f"2. Выберите свободный эмодзи\n"
            f"3. Используйте `/emoji [эмодзи]`\n\n"
            f"*Пример:*\n"
            f"`/emoji 🔥` - закрепить огонь за собой\n\n"
            f"*Посмотреть все функции:*\n"
            f"Используйте `/premium`"
        )
        
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        
    except Exception as e:
        logger.error(f"Error processing payment: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при активации премиума. Свяжитесь с администратором @anonaltshelper.",
            parse_mode=ParseMode.MARKDOWN
        )

async def premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if db.is_user_premium(user.id):
        user_emoji = db.get_user_emoji(user.id)
        emoji_type = db.get_user_emoji_type(user.id)
        reserved_emoji = db.get_reserved_emoji_for_user(user.id)
        
        text = (
            f"✨ *Anon Premium*\n\n"
            f"✅ Ваш премиум аккаунт активен!\n"
            f"🎨 Текущий эмодзи: {user_emoji}"
        )
        
        if emoji_type == "premium":
            text += f" ⭐ *Премиум эмодзи*\n"
        else:
            text += f"\n"
        
        if reserved_emoji and reserved_emoji == user_emoji:
            text += f"🔒 *Уникальный закрепленный эмодзи*\n\n"
        elif reserved_emoji:
            text += f"⚠️ Внимание: Закреплен {reserved_emoji}, но используется {user_emoji}\n\n"
        else:
            text += f"⚠️ *Эмодзи не закреплен*\n\n"
        
        text += (
            f"*Преимущества:*\n"
            f"• Уникальный закрепленный эмодзи 🔒\n"
            f"• Выбор из {len(PREMIUM_EMOJIS)} премиум эмодзи ⭐\n\n"
            f"*Команды:*\n"
            f"`/emoji` - закрепить новый эмодзи\n"
            f"`/availableemojis` - доступные эмодзи\n"
            f"`/myreservations` - мои резервации\n\n"
            f"*Поддержка:* @anonaltshelper"
        )
        
    else:
        text = (
            f"✨ *Anon Premium*\n\n"
            f"⭐ *Получите уникальный эмодзи за собой!*\n\n"
            f"*Что такое уникальный эмодзи?*\n"
            f"• Выберите любой эмодзи и закрепите его 🔒\n"
            f"• Этот эмодзи станет вашей уникальной подписью\n"
            f"• Никто другой не сможет его использовать\n"
            f"• Освобождается при отмене подписки\n\n"
            f"*Преимущества премиум аккаунта:*\n"
            f"• Уникальный закрепленный эмодзи 🔒\n"
            f"• {len(PREMIUM_EMOJIS)} премиум эмодзи Telegram ⭐\n\n"
            f"*Стоимость:*\n"
            f"1 месяц - {PREMIUM_PRICE} звезд Telegram ⭐\n\n"
            f"*Поддержка:* @anonaltshelper"
        )
        
        keyboard = [
            [InlineKeyboardButton(f"💳 Купить Premium ({PREMIUM_PRICE}⭐)", callback_data="buy_premium")],
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
        return
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ===================== ОБРАБОТЧИК КНОПОК =====================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data
    
    # Обработка покупки премиума
    if data == "buy_premium":
        await buy_premium_callback(update, context)
        return
    
    # Обработка админ-панели
    elif data == "admin_panel":
        await admin_panel_callback(update, context)
        return
    
    elif data == "list_users":
        await admin_list_users_callback(update, context)
        return
    
    elif data == "user_stats":
        await stats_callback(update, context)
        return
    
    # Обработка других кнопок
    else:
        await query.edit_message_text("❌ Неизвестная команда.")

async def buy_premium_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки покупки премиума"""
    query = update.callback_query
    await query.answer()
    
    await buy_premium_command(update, context)

async def admin_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if not is_admin(query.from_user.id):
        await query.edit_message_text("❌ Нет прав доступа.")
        return
    
    keyboard = [
        [InlineKeyboardButton("👥 Список пользователей", callback_data='list_users')],
        [InlineKeyboardButton("✨ Premium пользователи", callback_data='list_premium_users')],
        [InlineKeyboardButton("🚫 Забанить", callback_data='ban_options')],
        [InlineKeyboardButton("✅ Разбанить", callback_data='unban_options')],
        [InlineKeyboardButton("🔍 Найти отправителя", callback_data='find_options')],
        [InlineKeyboardButton("🔒 Зарезервированные эмодзи", callback_data='admin_reserved')],
        [InlineKeyboardButton("📊 Статистика", callback_data='user_stats')],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_start")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🔐 *Админ-панель*\nВыберите действие:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup
    )

async def admin_list_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки списка пользователей"""
    query = update.callback_query
    await query.answer()
    
    if not is_admin(query.from_user.id):
        await query.edit_message_text("❌ Нет прав доступа.")
        return
    
    all_users = db.get_all_users()
    
    if not all_users:
        await query.edit_message_text("📭 Нет зарегистрированных пользователей.")
        return
    
    text = f"👥 *Все пользователи: {len(all_users)}*\n\n"
    
    for i, user_data in enumerate(all_users[:20], 1):  # Показываем первые 20
        user_id = user_data[0]
        username = f"@{user_data[1]}" if user_data[1] else "Нет username"
        first_name = user_data[2] or ""
        last_name = user_data[3] or ""
        is_banned = "🚫" if user_data[4] else "✅"
        is_premium = "✨" if user_data[6] else "📱"
        
        text += f"{i}. {is_banned}{is_premium} `{user_id}` {username}\n"
        if first_name or last_name:
            text += f"   👤 {first_name} {last_name}\n"
    
    if len(all_users) > 20:
        text += f"\n... и еще {len(all_users) - 20} пользователей"
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки статистики"""
    query = update.callback_query
    await query.answer()
    
    await stats_command(update, context)

# ===================== ОСНОВНЫЕ ФУНКЦИИ =====================

async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if update.message and update.message.text and update.message.text.startswith('/'):
        return
    
    # Проверяем, является ли пользователь в процессе ответа
    if user.id in pending_replies:
        await process_reply_text(update, context, user.id)
        return
    
    # Проверяем, является ли сообщение пересланным (ответом)
    if hasattr(update.message, 'forward_from_chat') and update.message.forward_from_chat:
        # Это пересланное сообщение из канала - обработка ответа
        if update.message.forward_from_chat.username == CHANNEL_ID.replace('@', ''):
            await handle_reply_message(update, context)
            return
    
    # Если не пересланное сообщение или не из нашего канала - обычное сообщение
    await handle_new_message(update, context)

async def handle_reply_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ответа на сообщение"""
    user = update.effective_user
    
    if db.is_user_banned(user.id):
        await update.message.reply_text("❌ Вы заблокированы.")
        return
    
    spam_check = check_spam_cooldown(user.id)
    if spam_check:
        await update.message.reply_text(spam_check)
        return
    
    db.register_user(
        user.id, 
        user.username if user.username else None, 
        user.first_name if user.first_name else None, 
        user.last_name if user.last_name else None
    )
    
    # Получаем ID оригинального сообщения
    if not update.message.forward_from_message_id:
        await update.message.reply_text(
            "❌ Не удалось определить сообщение для ответа.\n"
            "Пожалуйста, перешлите сообщение из канала корректно.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    original_message_id = update.message.forward_from_message_id
    
    # Проверяем, существует ли оригинальное сообщение
    original_message_info = db.get_message_info(original_message_id)
    if not original_message_info:
        await update.message.reply_text(
            "❌ Оригинальное сообщение не найдено в базе данных.\n"
            "Возможно, оно было удалено.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Сохраняем информацию об ответе
    pending_replies[user.id] = (original_message_id, None)
    
    # Всегда запрашиваем текст ответа
    await update.message.reply_text(
        "✏️ *Ответ на сообщение*\n\n"
        f"Вы отвечаете на сообщение #{original_message_id}\n\n"
        f"Теперь отправьте текст вашего ответа:",
        parse_mode=ParseMode.MARKDOWN
    )

async def process_reply_text(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Обработка текста ответа"""
    if user_id not in pending_replies:
        await update.message.reply_text("❌ Сессия ответа истекла. Пожалуйста, начните заново.")
        return
    
    original_message_id, _ = pending_replies[user_id]
    
    # Получаем текст ответа
    reply_text = update.message.text or update.message.caption or ""
    if not reply_text.strip():
        await update.message.reply_text("❌ Ответ не может быть пустым.")
        return
    
    # Получаем данные пользователя
    user_emoji = db.get_user_emoji(user_id)
    
    # Форматируем ответ
    message_prefix = f"{user_emoji}: "
    formatted_reply = f"{message_prefix}{reply_text}"
    
    try:
        # Отправляем ответ в канал
        sent_message = await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=formatted_reply,
            parse_mode=ParseMode.MARKDOWN if any(mark in reply_text for mark in ['*', '_', '`']) else None
        )
        
        # Логируем ответ в базе данных
        db.log_message(user_id, sent_message.message_id, reply_text, 
                      reply_to=original_message_id, emoji_used=user_emoji)
        
        # Удаляем из pending_replies
        del pending_replies[user_id]
        
        await update.message.reply_text(
            f"✅ *Ответ отправлен!*\n\n"
            f"Ваш ответ был отправлен как ответ на сообщение #{original_message_id}",
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Ошибка отправки ответа: {error_msg}")
        
        await update.message.reply_text(f"❌ Ошибка при отправке: {error_msg}")

async def handle_new_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нового сообщения (не ответа)"""
    user = update.effective_user
    
    if db.is_user_banned(user.id):
        await update.message.reply_text("❌ Вы заблокированы.")
        return
    
    spam_check = check_spam_cooldown(user.id)
    if spam_check:
        await update.message.reply_text(spam_check)
        return
    
    db.register_user(
        user.id, 
        user.username if user.username else None, 
        user.first_name if user.first_name else None, 
        user.last_name if user.last_name else None
    )
    
    # Проверяем, не является ли это текстом ответа на пересланное сообщение
    if user.id in pending_replies:
        # Это должно обрабатываться в handle_all_messages
        return
    
    try:
        message = update.message
        
        # Получаем эмодзи пользователя
        user_emoji = db.get_user_emoji(user.id)
        
        # Форматируем префикс сообщения
        message_prefix = f"{user_emoji}: "
        
        if message.text:
            formatted_message = f"{message_prefix}{message.text}"
            
            # Отправляем сообщение в канал
            sent_message = await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=formatted_message,
                parse_mode=ParseMode.MARKDOWN if any(mark in message.text for mark in ['*', '_', '`']) else None
            )
            
            # Логируем сообщение
            db.log_message(user.id, sent_message.message_id, message.text, emoji_used=user_emoji)
            
            await update.message.reply_text("✅ Сообщение отправлено в канал!")
            
        elif message.photo:
            photo = message.photo[-1]
            caption = f"{message_prefix}Анонимное фото"
            if message.caption:
                caption = f"{message_prefix}{message.caption}"
            
            sent_message = await context.bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=photo.file_id,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN if message.caption and any(mark in message.caption for mark in ['*', '_', '`']) else None
            )
            
            if message.caption:
                db.log_message(user.id, sent_message.message_id, message.caption, emoji_used=user_emoji)
            else:
                db.log_message(user.id, sent_message.message_id, "Анонимное фото", emoji_used=user_emoji)
            
            await update.message.reply_text("✅ Фото отправлено в канал!")
            
        elif message.video:
            video = message.video
            caption = f"{message_prefix}Анонимное видео"
            if message.caption:
                caption = f"{message_prefix}{message.caption}"
            
            sent_message = await context.bot.send_video(
                chat_id=CHANNEL_ID,
                video=video.file_id,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN if message.caption and any(mark in message.caption for mark in ['*', '_', '`']) else None
            )
            
            if message.caption:
                db.log_message(user.id, sent_message.message_id, message.caption, emoji_used=user_emoji)
            else:
                db.log_message(user.id, sent_message.message_id, "Анонимное видео", emoji_used=user_emoji)
            
            await update.message.reply_text("✅ Видео отправлено в канал!")
            
        elif message.voice:
            voice = message.voice
            caption = f"{message_prefix}Анонимное голосовое сообщение"
            
            sent_message = await context.bot.send_voice(
                chat_id=CHANNEL_ID,
                voice=voice.file_id,
                caption=caption
            )
            
            db.log_message(user.id, sent_message.message_id, "Анонимное голосовое сообщение", emoji_used=user_emoji)
            
            await update.message.reply_text("✅ Голосовое сообщение отправлено в канал!")
            
        elif message.document:
            document = message.document
            caption = f"{message_prefix}Анонимный документ"
            if message.caption:
                caption = f"{message_prefix}{message.caption}"
            
            sent_message = await context.bot.send_document(
                chat_id=CHANNEL_ID,
                document=document.file_id,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN if message.caption and any(mark in message.caption for mark in ['*', '_', '`']) else None
            )
            
            if message.caption:
                db.log_message(user.id, sent_message.message_id, message.caption, emoji_used=user_emoji)
            else:
                db.log_message(user.id, sent_message.message_id, "Анонимный документ", emoji_used=user_emoji)
            
            await update.message.reply_text("✅ Документ отправлен в канал!")
            
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Ошибка отправки: {error_msg}")
        await update.message.reply_text(f"❌ Ошибка: {error_msg}")

# ===================== ЗАПУСК БОТА =====================

def main():
    print("=" * 60)
    print("🤖 АНОНИМНЫЙ БОТ С УНИКАЛЬНЫМИ ЭМОДЗИ")
    print("=" * 60)
    print(f"📢 Канал: {CHANNEL_ID}")
    print(f"👑 Админ: {ADMIN_IDS[0]}")
    print(f"💰 Стоимость премиума: {PREMIUM_PRICE} Stars")
    print(f"🎨 Доступно эмодзи: {len(PREMIUM_EMOJIS)}")
    print(f"⏱️ Антиспам: {SPAM_COOLDOWN} секунд")
    print("=" * 60)
    print("✨ *Премиум функции:*")
    print(f"• {PREMIUM_PRICE} Stars за 1 месяц")
    print("• Уникальный закрепленный эмодзи 🔒")
    print("• Премиум эмодзи Telegram ⭐")
    print("=" * 60)
    print("📌 Поддержка: @anonaltshelper")
    print("=" * 60)
    
    try:
        app = Application.builder().token(BOT_TOKEN).build()
        
        # Основные команды
        app.add_handler(CommandHandler("start", start_command))
        
        # Premium команды с уникальными эмодзи
        app.add_handler(CommandHandler("premium", premium_command))
        app.add_handler(CommandHandler("emoji", emoji_command))
        app.add_handler(CommandHandler("myemoji", myemoji_command))
        app.add_handler(CommandHandler("availableemojis", availableemojis_command))
        app.add_handler(CommandHandler("myreservations", myreservations_command))
        app.add_handler(CommandHandler("stats", stats_command))
        app.add_handler(CommandHandler("buy_premium", buy_premium_command))
        
        # Админ команды
        app.add_handler(CommandHandler("admin", admin_command))
        app.add_handler(CommandHandler("ban", ban_command))
        app.add_handler(CommandHandler("unban", unban_command))
        app.add_handler(CommandHandler("find", find_command))
        app.add_handler(CommandHandler("users", users_command))
        app.add_handler(CommandHandler("reserved", admin_reserved_command))
        app.add_handler(CommandHandler("freeemoji", admin_free_emoji_command))
        
        # Обработчики платежей
        app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
        app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
        
        # Обработчики кнопок
        app.add_handler(CallbackQueryHandler(button_handler))
        
        # Обработчик всех сообщений
        app.add_handler(MessageHandler(
            filters.ChatType.PRIVATE & ~filters.COMMAND,
            handle_all_messages
        ))
        
        print("✅ Бот запущен")
        print("👉 Используйте /start для начала работы")
        print("⭐ Используйте /premium для информации о премиуме")
        print("🎨 Используйте /availableemojis для выбора эмодзи")
        print("💳 Используйте /buy_premium для покупки премиума")
        print("📌 Поддержка: @anonaltshelper")
        print("=" * 60)
        
        app.run_polling(drop_pending_updates=True)
        
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен")
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
