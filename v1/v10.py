#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
INFERVPN БОТ v11_final
-----------------------
Функционал:
- Поддержка регионов, устройств, промокодов, платежей через polling.
- Главное меню: "Профиль", "Купить подписку", "Пополнить", "Доп. трафик", "Поддержка".
- Профиль с кнопками: "Мои ключи", "Баланс", "⬇️ Скачать Outline", "📄 Инструкции", "⬅️ Назад".
- Новые пользователи получают тестовый период (2 дня, бонус 1 ГБ) и автоматически получают ключ.
- При покупке подписки пользователь выбирает устройство (кол-во устройств) и сервер.
- VPN-сервера динамически управляются через новую таблицу "servers". Админ может добавлять новые серверы.
- Дополнительный трафик можно приобрести отдельной кнопкой.
- Если пользователь не подписан на канал, предлагаются кнопки "Я подписался" и "Пропустить".
- Платежи создаются через CryptoPay; заказ отменяется, если не оплачен за 10 минут.
- Команда /broadcast (через reply) позволяет администратору разослать сообщение всем пользователям.
- Админ-панель содержит также команды /addserver и /serverlist для управления VPN-серверами.
- Статистика и техподдержка доступны в админ-панели.
"""

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import sqlite3
import requests
from datetime import datetime, timedelta
import threading
import time
import urllib3
import os

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Конфигурация ---
API_TOKEN = "7809159665:AAEM1x02VKgTAs8OzTt0I-ZUC57XbKXMORU"
CRYPTO_BOT_TOKEN = "368678:AAda5iskM0F7iKcgL38whM8fpUJw5XoTgjW"
OUTLINE_API_URL = "https://194.156.66.56:43396/elRRhieGh9AghrUPycn9iA"
CRYPTO_BOT_API = "https://pay.crypt.bot/api"
ADMIN_ID = 1637201884

# Статические значения для базовой подписки (множитель для разных вариантов)
SUBSCRIPTION_OPTIONS = {
    "1 устройство": 1,
    "2 устройства": 1.5,
    "3 устройства": 2
}

# Дополнительные пакеты дополнительного трафика
ADDITIONAL_TRAFFIC_PACKAGES = {
    "500 МБ": 50,
    "1 ГБ": 90,
    "2 ГБ": 170
}

# Настройки тестового периода и скидки
TRIAL_PERIOD_DAYS = 2
TRIAL_TRAFFIC_GB = 1
SUBSCRIPTION_DISCOUNT_PERCENT = 20

# Параметры платежей
PAYMENT_POLL_INTERVAL = 30      # секунд
ORDER_TIMEOUT_SECONDS = 600     # 10 минут

# Прочие настройки
manual_usdt_rate = None
extra_gb_price = 100
TRAFFIC_LIMIT_GB = 1
REQUIRE_SUBSCRIPTION = True

# --- Инициализация бота ---
bot = telebot.TeleBot(API_TOKEN, threaded=True)

# --- Глобальные переменные ---
waiting_for_topup = set()          # Пользователи, ожидающие ввода суммы пополнения
temp_device_choice = {}            # Выбор устройства (например, "1 устройство")
temp_region_choice = {}            # Выбор сервера (будет идентификатор сервера из таблицы servers)

# Флаги ожидания ввода от администратора для дополнительных команд
pending_admin_add_balance = False
pending_admin_create_promo = False
pending_admin_set_price = False

# --- Подключение к базе данных и создание таблиц ---
try:
    db = sqlite3.connect("infervpn.db", check_same_thread=False)
except sqlite3.DatabaseError:
    print("❌ Ошибка: база данных повреждена. Удалите файл infervpn.db.")
    exit()

with db:
    # Таблица пользователей
    db.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        key_id TEXT,
        access_url TEXT,
        expires DATETIME,
        is_trial BOOLEAN DEFAULT 1,
        balance REAL DEFAULT 0,
        is_subscribed BOOLEAN DEFAULT 0,
        traffic_used REAL DEFAULT 0,
        allocated_traffic REAL DEFAULT 0,
        region TEXT,
        device_count INTEGER DEFAULT 1,
        subscription_skipped BOOLEAN DEFAULT 0
    )''')
    try:
        db.execute("ALTER TABLE users ADD COLUMN allocated_traffic REAL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN subscription_skipped BOOLEAN DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # Таблица платежей – created_at хранится как INTEGER (Unix timestamp)
    db.execute('''CREATE TABLE IF NOT EXISTS payments (
        payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        invoice_id TEXT,
        amount REAL,
        status TEXT DEFAULT 'pending',
        created_at INTEGER DEFAULT (strftime('%s','now'))
    )''')

    # Таблица промокодов
    db.execute('''CREATE TABLE IF NOT EXISTS promocodes (
        code TEXT PRIMARY KEY,
        traffic_bonus REAL DEFAULT 0.1,
        subscription_days INTEGER DEFAULT 0,
        max_activations INTEGER DEFAULT 1
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS promo_used (
        user_id INTEGER,
        code TEXT,
        PRIMARY KEY (user_id, code)
    )''')

    # Новая таблица VPN-серверов (динамическое управление)
    db.execute('''CREATE TABLE IF NOT EXISTS servers (
        server_id INTEGER PRIMARY KEY AUTOINCREMENT,
        region TEXT,
        api_url TEXT,
        base_price REAL
    )''')

# --- Вспомогательные функции ---

def get_usdt_rate():
    if manual_usdt_rate:
        return manual_usdt_rate
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=USDTRUB", timeout=5)
        return float(r.json()['price'])
    except Exception:
        return 90

def is_user_subscribed(user_id):
    with db:
        cur = db.cursor()
        cur.execute("SELECT subscription_skipped FROM users WHERE id=?", (user_id,))
        row = cur.fetchone()
        if row and row[0] == 1:
            return True
    if not REQUIRE_SUBSCRIPTION:
        return True
    try:
        member = bot.get_chat_member("@INFERvpn", user_id)
        return member.status in ("member", "creator", "administrator")
    except Exception:
        return False

def subscribe_prompt(user_id):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("Я подписался", callback_data='i_subscribed'))
    markup.add(InlineKeyboardButton("Пропустить", callback_data='skip_subscribe'))
    bot.send_message(user_id, "Вы не подписаны на канал @INFERvpn.\nНажмите «Я подписался», если подписались или «Пропустить» для входа.", reply_markup=markup)

def create_access_key(api_url):
    try:
        response = requests.post(f"{api_url}/access-keys", verify=False, timeout=5)
        data = response.json()
        return data.get('id'), data.get('accessUrl')
    except Exception:
        return None, None

def create_invoice(user_id, amount_rub):
    rate = get_usdt_rate()
    usdt_amount = round(amount_rub / rate, 2)
    payload = {
        "asset": "USDT",
        "amount": str(usdt_amount),
        "description": f"Пополнение INFERVPN на {amount_rub}₽",
        "hidden_message": "Спасибо за поддержку!",
        "paid_btn_name": "openBot",
        "paid_btn_url": "https://t.me/infervpn_bot"
    }
    headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN}
    try:
        r = requests.post(f"{CRYPTO_BOT_API}/createInvoice", json=payload, headers=headers, timeout=10)
        r.raise_for_status()
        result = r.json()
        if result.get("ok"):
            data = result["result"]
            invoice_id = data.get("invoice_id")
            pay_url = data.get("pay_url")
            return invoice_id, pay_url
        else:
            return None, None
    except Exception:
        return None, None

def get_invoice_status(invoice_id):
    headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN}
    try:
        r = requests.post(f"{CRYPTO_BOT_API}/getInvoice", json={"invoice_id": invoice_id}, headers=headers, timeout=10)
        r.raise_for_status()
        result = r.json()
        return result["result"].get("status")
    except Exception:
        return None

def load_servers():
    """Возвращает список серверов из БД."""
    with db:
        cur = db.cursor()
        cur.execute("SELECT server_id, region, api_url, base_price FROM servers")
        return cur.fetchall()

def region_selection_markup():
    """Создаем кнопки выбора сервера из таблицы servers."""
    servers = load_servers()
    markup = InlineKeyboardMarkup()
    for server in servers:
        server_id, region, api_url, base_price = server
        button_text = f"{region} ({base_price}₽)"
        markup.add(InlineKeyboardButton(button_text, callback_data=f"region_{server_id}"))
    return markup

def additional_traffic_markup():
    markup = InlineKeyboardMarkup()
    for package, price in ADDITIONAL_TRAFFIC_PACKAGES.items():
        markup.add(InlineKeyboardButton(f"{package} - {price}₽", callback_data=f"buy_traffic_{package}"))
    return markup

def main_menu():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("👤 Профиль", callback_data='profile'))
    markup.add(InlineKeyboardButton("🛒 Купить подписку", callback_data='buy'))
    markup.add(InlineKeyboardButton("💳 Пополнить", callback_data='topup'))
    markup.add(InlineKeyboardButton("📶 Доп. трафик", callback_data='buy_traffic'))
    markup.add(InlineKeyboardButton("🆘 Поддержка", callback_data='support'))
    return markup

def profile_menu():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔑 Мои ключи", callback_data='my_keys'))
    markup.add(InlineKeyboardButton("💰 Баланс", callback_data='my_balance'))
    markup.add(InlineKeyboardButton("⬇️ Скачать Outline", callback_data='outline_download'))
    markup.add(InlineKeyboardButton("📄 Инструкции", callback_data='instructions'))
    markup.add(InlineKeyboardButton("⬅️ Назад", callback_data='back'))
    return markup

# --- Админ-команды для серверов ---

@bot.message_handler(commands=['addserver'])
def handle_addserver(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return
    # Формат: /addserver <регион> <api_url> <base_price>
    try:
        parts = message.text.split(maxsplit=3)
        if len(parts) < 4:
            bot.send_message(user_id, "Формат: /addserver <регион> <api_url> <base_price>")
            return
        region = parts[1]
        api_url = parts[2]
        base_price = float(parts[3])
        with db:
            db.execute("INSERT INTO servers (region, api_url, base_price) VALUES (?, ?, ?)", (region, api_url, base_price))
        bot.send_message(user_id, f"✅ Сервер '{region}' добавлен!")
    except Exception as e:
        bot.send_message(user_id, f"Ошибка: {e}")

@bot.message_handler(commands=['serverlist'])
def handle_serverlist(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return
    servers = load_servers()
    if not servers:
        bot.send_message(user_id, "Нет добавленных серверов.")
        return
    msg = "Список серверов:\n"
    for server in servers:
        server_id, region, api_url, base_price = server
        msg += f"ID: {server_id} | {region} | {api_url} | {base_price}₽\n"
    bot.send_message(user_id, msg)

# --- Обработчики команд пользователя ---

@bot.message_handler(commands=['start'])
def handle_start(message):
    user_id = message.from_user.id
    if not is_user_subscribed(user_id):
        subscribe_prompt(user_id)
        return
    with db:
        cur = db.cursor()
        cur.execute("SELECT is_subscribed, expires FROM users WHERE id=?", (user_id,))
        row = cur.fetchone()
        if row is None or (row is not None and not row[0]):
            db.execute("INSERT OR IGNORE INTO users (id) VALUES (?)", (user_id,))
            trial_expires = datetime.now() + timedelta(days=TRIAL_PERIOD_DAYS)
            key_id, access_url = create_access_key(OUTLINE_API_URL)
            db.execute("UPDATE users SET expires=?, is_trial=1, key_id=?, access_url=? WHERE id=?",
                       (trial_expires.strftime("%Y-%m-%d %H:%M:%S"), key_id, access_url, user_id))
            welcome_message = (f"👋 Привет от команды INFERVPN!\nТестовый период: {TRIAL_PERIOD_DAYS} дня(ей), бонус {TRIAL_TRAFFIC_GB} ГБ.\nМы за свободный интернет!")
        else:
            welcome_message = "👋 Добро пожаловать обратно!"
    bot.send_message(user_id, welcome_message, reply_markup=main_menu())

@bot.message_handler(commands=['admin'])
def handle_admin(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📬 Рассылка (reply)", callback_data='admin_broadcast'))
    markup.add(InlineKeyboardButton("💰 Начислить баланс", callback_data='admin_add_balance'))
    markup.add(InlineKeyboardButton("🎁 Создать промокод", callback_data='admin_create_promo'))
    markup.add(InlineKeyboardButton("💸 Установить цену", callback_data='admin_set_price'))
    markup.add(InlineKeyboardButton("⚙️ Подписка", callback_data='admin_toggle_subscription'))
    markup.add(InlineKeyboardButton("📊 Статистика", callback_data='admin_stats'))
    markup.add(InlineKeyboardButton("💬 Тех поддержка", callback_data='admin_support'))
    bot.send_message(user_id, "🛠 Админ-панель:", reply_markup=markup)

@bot.message_handler(commands=['broadcast'])
def handle_broadcast(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return
    if not message.reply_to_message:
        bot.send_message(user_id, "Ошибка: ответьте (reply) на сообщение для рассылки.")
        return
    broadcast_msg = message.reply_to_message
    with db:
        cur = db.cursor()
        cur.execute("SELECT id FROM users")
        all_ids = [row[0] for row in cur.fetchall()]
    total = 0
    for uid in all_ids:
        if uid == ADMIN_ID:
            continue
        try:
            if broadcast_msg.content_type == 'photo':
                bot.send_photo(uid, broadcast_msg.photo[-1].file_id, caption=broadcast_msg.caption)
            elif broadcast_msg.text:
                bot.send_message(uid, broadcast_msg.text)
            total += 1
        except Exception:
            pass
    bot.send_message(user_id, f"✅ Рассылка завершена. Отправлено {total} пользователям.")

# --- Callback обработчики для пользователя ---

@bot.callback_query_handler(func=lambda call: call.data == 'i_subscribed')
def handle_i_subscribed(call):
    user_id = call.from_user.id
    if is_user_subscribed(user_id):
        bot.send_message(user_id, "Отлично, подписка подтверждена!", reply_markup=main_menu())
    else:
        subscribe_prompt(user_id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == 'skip_subscribe')
def handle_skip_subscribe(call):
    user_id = call.from_user.id
    with db:
        db.execute("UPDATE users SET subscription_skipped=1 WHERE id=?", (user_id,))
    bot.send_message(user_id, "Вы пропустили проверку подписки. Добро пожаловать!", reply_markup=main_menu())
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == 'profile')
def handle_profile(call):
    user_id = call.from_user.id
    bot.send_message(user_id, "👤 Ваш профиль:", reply_markup=profile_menu())
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == 'my_balance')
def handle_my_balance(call):
    user_id = call.from_user.id
    with db:
        cur = db.cursor()
        cur.execute("SELECT balance FROM users WHERE id=?", (user_id,))
        row = cur.fetchone()
    if row:
        bot.send_message(user_id, f"💰 Ваш баланс: {row[0]:.2f} ₽")
    else:
        bot.send_message(user_id, "Информация о балансе недоступна.")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == 'topup')
def handle_topup_callback(call):
    user_id = call.from_user.id
    waiting_for_topup.add(user_id)
    bot.send_message(user_id, "💳 Введите сумму для пополнения в рублях:")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == 'buy_traffic')
def handle_buy_traffic_callback(call):
    user_id = call.from_user.id
    bot.send_message(user_id, "📶 Выберите пакет дополнительного трафика:", reply_markup=additional_traffic_markup())
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("buy_traffic_"))
def handle_buy_traffic_package(call):
    user_id = call.from_user.id
    package = call.data.split("buy_traffic_")[1]
    price = ADDITIONAL_TRAFFIC_PACKAGES.get(package)
    if price is None:
        bot.send_message(user_id, "⚠️ Неверный выбор пакета.")
        bot.answer_callback_query(call.id)
        return
    with db:
        cur = db.cursor()
        cur.execute("SELECT balance FROM users WHERE id=?", (user_id,))
        row = cur.fetchone()
        if not row or row[0] < price:
            bot.send_message(user_id, f"❌ Недостаточно средств для пакета {package} (цена: {price}₽).")
            bot.answer_callback_query(call.id)
            return
        db.execute("UPDATE users SET balance = balance - ? WHERE id=?", (price, user_id))
        # Определяем добавленный трафик
        added_traffic = 0
        if "МБ" in package:
            try:
                mb_value = float(package.split()[0])
                added_traffic = mb_value / 1024
            except Exception:
                added_traffic = 0
        elif "ГБ" in package:
            try:
                gb_value = float(package.split()[0])
                added_traffic = gb_value
            except Exception:
                added_traffic = 0
        db.execute("UPDATE users SET allocated_traffic = allocated_traffic + ? WHERE id=?", (added_traffic, user_id))
    bot.send_message(user_id, f"✅ Пакет {package} за {price}₽ приобретен.\nДоп. трафик увеличен на {added_traffic:.2f} ГБ.")
    bot.answer_callback_query(call.id)

def subscription_options_markup():
    markup = InlineKeyboardMarkup()
    for label, price in SUBSCRIPTION_OPTIONS.items():
        markup.add(InlineKeyboardButton(f"{label} ({price}₽)", callback_data=f"device_{label}"))
    return markup

@bot.callback_query_handler(func=lambda call: call.data == 'buy')
def handle_buy_callback(call):
    user_id = call.from_user.id
    bot.send_message(user_id, "🛒 Выберите количество устройств:", reply_markup=subscription_options_markup())
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("device_"))
def handle_device(call):
    user_id = call.from_user.id
    device_label = call.data.split("device_", 1)[1]
    temp_device_choice[user_id] = device_label
    bot.send_message(user_id, "🌍 Выберите сервер (регион):", reply_markup=region_selection_markup())
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("region_"))
def handle_region(call):
    user_id = call.from_user.id
    try:
        server_id = int(call.data.split("region_")[1])
    except Exception:
        bot.send_message(user_id, "Ошибка выбора сервера.")
        return
    with db:
        cur = db.cursor()
        cur.execute("SELECT region, api_url, base_price FROM servers WHERE server_id=?", (server_id,))
        server = cur.fetchone()
    if not server:
        bot.send_message(user_id, "Сервер не найден.")
        return
    region_name, api_url, base_price = server
    device_label = temp_device_choice.get(user_id)
    if device_label not in SUBSCRIPTION_OPTIONS:
        bot.send_message(user_id, "Ошибка: тип устройства не найден.")
        return
    multiplier = SUBSCRIPTION_OPTIONS[device_label]
    final_price = base_price * multiplier
    with db:
        cur = db.cursor()
        cur.execute("SELECT balance, expires, is_trial FROM users WHERE id=?", (user_id,))
        row = cur.fetchone()
        if not row:
            bot.send_message(user_id, "Ошибка: данные пользователя не найдены.")
            return
        balance, expires_str, is_trial = row
        now = datetime.now()
        trial_active = False
        trial_expired = False
        if expires_str:
            current_expires = datetime.strptime(expires_str, "%Y-%m-%d %H:%M:%S")
            if is_trial and current_expires > now:
                trial_active = True
            elif is_trial and current_expires <= now:
                trial_expired = True
        if trial_active:
            bot.send_message(user_id, f"Тестовый период до {current_expires.strftime('%d.%m.%Y %H:%M')}. Подписка со скидкой {SUBSCRIPTION_DISCOUNT_PERCENT}% будет доступна после его окончания.")
            return
        if trial_expired and is_trial:
            final_price = final_price * (100 - SUBSCRIPTION_DISCOUNT_PERCENT) / 100
            cur.execute("UPDATE users SET is_trial=0 WHERE id=?", (user_id,))
        if balance < final_price:
            bot.send_message(user_id, f"❌ Недостаточно средств ({balance}₽); требуется {final_price}₽.")
            return
        key_id, access_url = create_access_key(api_url)
        if not access_url:
            bot.send_message(user_id, "⚠️ Не удалось создать ключ. Попробуйте позже.")
            return
        new_expires = datetime.now() + timedelta(days=30)
        cur.execute("""
            UPDATE users
               SET key_id=?,
                   access_url=?,
                   expires=?,
                   is_subscribed=1,
                   is_trial=0,
                   traffic_used=0,
                   balance=balance-?,
                   region=?,
                   device_count=?
             WHERE id=?
        """, (key_id, access_url, new_expires.strftime("%Y-%m-%d %H:%M:%S"), final_price, region_name, int(device_label.split()[0]), user_id))
        bot.send_message(user_id, f"✅ Подписка оформлена!\n🔑 Ключ: {access_url}\n📅 До: {new_expires.strftime('%d.%m.%Y %H:%M')}\n📍 Сервер: {region_name}\n📱 Устройств: {device_label}\nЦена: {final_price}₽")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == 'support')
def handle_support(call):
    user_id = call.from_user.id
    bot.send_message(user_id, "🆘 Поддержка: @alfageran01")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == 'my_keys')
def handle_my_keys(call):
    user_id = call.from_user.id
    with db:
        cur = db.cursor()
        cur.execute("SELECT access_url FROM users WHERE id=?", (user_id,))
        row = cur.fetchone()
    if row and row[0]:
        bot.send_message(user_id, f"🔑 Ваш ключ: {row[0]}")
    else:
        bot.send_message(user_id, "❌ Ключ не найден.")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == 'outline_download')
def handle_outline_download(call):
    user_id = call.from_user.id
    bot.send_message(user_id, "⬇️ Скачайте Outline по ссылке: https://getoutline.org")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == 'instructions')
def handle_instructions(call):
    user_id = call.from_user.id
    bot.send_message(user_id, "📄 Инструкции:\n1. Скачайте и установите Outline.\n2. Используйте выданный ключ.\nПодробнее на сайте.")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == 'back')
def handle_back(call):
    user_id = call.from_user.id
    bot.send_message(user_id, "🔙 Главное меню:", reply_markup=main_menu())
    bot.answer_callback_query(call.id)

# --- Обработчики сообщений для пополнения и промокодов ---

@bot.message_handler(func=lambda m: m.text and m.text.strip().isdigit() and m.from_user.id in waiting_for_topup)
def handle_topup_amount(message):
    user_id = message.from_user.id
    waiting_for_topup.remove(user_id)
    amount = int(message.text.strip())
    if amount < 10:
        bot.send_message(user_id, "⚠️ Минимальная сумма пополнения — 10₽.")
        return
    invoice_id, pay_url = create_invoice(user_id, amount)
    if invoice_id and pay_url:
        with db:
            db.execute("INSERT INTO payments (user_id, invoice_id, amount, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
                       (user_id, invoice_id, amount, int(time.time())))
        bot.send_message(user_id, f"💳 Счёт на {amount}₽ создан.\nОплатите по ссылке:\n{pay_url}")
    else:
        bot.send_message(user_id, "❌ Не удалось создать счёт. Попробуйте позже.")

@bot.message_handler(func=lambda m: m.text and (not m.text.strip().isdigit()) and m.from_user.id not in [ADMIN_ID] and m.from_user.id not in waiting_for_topup)
def handle_promocode(message):
    from datetime import datetime, timedelta
    user_id = message.from_user.id
    code = message.text.strip().upper()
    with db:
        cur = db.cursor()
        cur.execute("SELECT traffic_bonus, subscription_days, max_activations FROM promocodes WHERE code=?", (code,))
        promo = cur.fetchone()
        if not promo:
            bot.send_message(user_id, "❌ Промокод не найден.")
            return
        traffic_bonus, subs_days, max_act = promo
        cur.execute("SELECT COUNT(*) FROM promo_used WHERE code=?", (code,))
        usage_count = cur.fetchone()[0]
        if usage_count >= max_act:
            bot.send_message(user_id, "⚠️ Этот промокод уже полностью использован.")
            return
        cur.execute("UPDATE users SET traffic_used = traffic_used - ? WHERE id=?", (traffic_bonus, user_id))
        cur.execute("SELECT expires FROM users WHERE id=?", (user_id,))
        row = cur.fetchone()
        now = datetime.now()
        if row and row[0]:
            try:
                current_expires = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
            except Exception:
                current_expires = now
            new_expires = now + timedelta(days=subs_days) if current_expires < now else current_expires + timedelta(days=subs_days)
        else:
            new_expires = now + timedelta(days=subs_days)
        cur.execute("UPDATE users SET expires=? WHERE id=?", (new_expires.strftime("%Y-%m-%d %H:%M:%S"), user_id))
        cur.execute("INSERT INTO promo_used (user_id, code) VALUES (?, ?)", (user_id, code))
        bot.send_message(user_id, f"✅ Промокод применён!\nДобавлено: {traffic_bonus} ГБ трафика, подписка продлена на {subs_days} дн.")

# --- Обработка сообщений от администратора для дополнительных команд ---

@bot.callback_query_handler(func=lambda call: call.data == 'admin_broadcast')
def handle_admin_broadcast(call):
    admin_id = call.from_user.id
    bot.send_message(admin_id, "Чтобы разослать сообщение, ответьте (reply) на сообщение, которое хотите отправить, и отправьте команду /broadcast.")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == 'admin_add_balance')
def handle_admin_add_balance(call):
    global pending_admin_add_balance
    admin_id = call.from_user.id
    pending_admin_add_balance = True
    bot.send_message(admin_id, "Введите ID пользователя и сумму для начисления, разделенные пробелом.\nПример: 123456789 100")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == 'admin_create_promo')
def handle_admin_create_promo(call):
    global pending_admin_create_promo
    admin_id = call.from_user.id
    pending_admin_create_promo = True
    bot.send_message(admin_id, "Введите промокод в формате:\nКОД трафик_бонус подписка_дней max_activations\nПример: PROMO10 1 7 10")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == 'admin_set_price')
def handle_admin_set_price(call):
    global pending_admin_set_price
    admin_id = call.from_user.id
    pending_admin_set_price = True
    bot.send_message(admin_id, "Введите новую цену USDT (числовое значение).\nПример: 85")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == 'admin_toggle_subscription')
def handle_admin_toggle_subscription(call):
    global REQUIRE_SUBSCRIPTION
    admin_id = call.from_user.id
    REQUIRE_SUBSCRIPTION = not REQUIRE_SUBSCRIPTION
    status = "включено" if REQUIRE_SUBSCRIPTION else "выключено"
    bot.send_message(admin_id, f"Требование подписки теперь {status}.")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == 'admin_stats')
def handle_admin_stats(call):
    admin_id = call.from_user.id
    with db:
        cur = db.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        total_users = cur.fetchone()[0]
        cur.execute("SELECT SUM(balance) FROM users")
        total_balance = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM payments WHERE status='paid'")
        paid_payments = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM servers")
        total_servers = cur.fetchone()[0]
    stats_msg = (f"📊 Статистика:\n"
                 f"Пользователей: {total_users}\n"
                 f"Общий баланс: {total_balance:.2f}₽\n"
                 f"Оплаченных платежей: {paid_payments}\n"
                 f"Серверов: {total_servers}")
    bot.send_message(admin_id, stats_msg)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == 'admin_support')
def handle_admin_support(call):
    admin_id = call.from_user.id
    bot.send_message(admin_id, "💬 Техподдержка для админа: @alfageran01")
    bot.answer_callback_query(call.id)

# Обработчик сообщений от администратора для ввода данных по дополнительным командам
@bot.message_handler(func=lambda m: m.from_user.id == ADMIN_ID and m.text and not m.text.startswith('/'))
def handle_admin_pending_input(message):
    global pending_admin_add_balance, pending_admin_create_promo, pending_admin_set_price, manual_usdt_rate
    admin_id = message.from_user.id
    text = message.text.strip()
    # Начисление баланса
    if pending_admin_add_balance:
        try:
            parts = text.split()
            if len(parts) != 2:
                raise ValueError("Неверный формат.")
            target_id = int(parts[0])
            amount = float(parts[1])
            with db:
                db.execute("UPDATE users SET balance = balance + ? WHERE id=?", (amount, target_id))
            bot.send_message(admin_id, f"✅ Баланс пользователя {target_id} успешно пополнен на {amount}₽.")
        except Exception as e:
            bot.send_message(admin_id, f"Ошибка при начислении баланса: {e}")
        pending_admin_add_balance = False
        return
    # Создание промокода
    if pending_admin_create_promo:
        try:
            parts = text.split()
            if len(parts) != 4:
                raise ValueError("Неверный формат.")
            code = parts[0].upper()
            traffic_bonus = float(parts[1])
            subs_days = int(parts[2])
            max_act = int(parts[3])
            with db:
                db.execute("INSERT INTO promocodes (code, traffic_bonus, subscription_days, max_activations) VALUES (?, ?, ?, ?)",
                           (code, traffic_bonus, subs_days, max_act))
            bot.send_message(admin_id, f"✅ Промокод {code} успешно создан!")
        except Exception as e:
            bot.send_message(admin_id, f"Ошибка при создании промокода: {e}")
        pending_admin_create_promo = False
        return
    # Установка цены USDT
    if pending_admin_set_price:
        try:
            new_price = float(text)
            manual_usdt_rate = new_price
            bot.send_message(admin_id, f"✅ Новая цена USDT установлена: {new_price}")
        except Exception as e:
            bot.send_message(admin_id, f"Ошибка при установке цены: {e}")
        pending_admin_set_price = False
        return
    # Если нет ожидающих операций, можно добавить и другие админ-функции
    bot.send_message(admin_id, "Команда не распознана.")

# --- Фоновая проверка платежей ---
def poll_pending_payments():
    while True:
        try:
            with db:
                cur = db.cursor()
                cur.execute("SELECT payment_id, user_id, invoice_id, amount, created_at FROM payments WHERE status='pending'")
                pending = cur.fetchall()
                now_ts = int(time.time())
                for payment in pending:
                    payment_id, uid, invoice_id, amount, created_at_ts = payment
                    if now_ts - int(created_at_ts) > ORDER_TIMEOUT_SECONDS:
                        db.execute("UPDATE payments SET status='canceled' WHERE payment_id=?", (payment_id,))
                        bot.send_message(uid, "⚠️ Ваш заказ не оплачен вовремя и был отменен.")
                        continue
                    status = get_invoice_status(invoice_id)
                    if status == "paid":
                        db.execute("UPDATE users SET balance = balance + ? WHERE id=?", (amount, uid))
                        db.execute("UPDATE payments SET status='paid' WHERE payment_id=?", (payment_id,))
                        bot.send_message(uid, f"✅ Оплата {amount}₽ прошла успешно, баланс пополнен!")
                    elif status == "expired":
                        db.execute("UPDATE payments SET status='canceled' WHERE payment_id=?", (payment_id,))
                        bot.send_message(uid, "⚠️ Ваш счёт просрочен и отменен. Создайте новый счёт.")
            time.sleep(PAYMENT_POLL_INTERVAL)
        except Exception:
            time.sleep(PAYMENT_POLL_INTERVAL)

threading.Thread(target=poll_pending_payments, daemon=True).start()

def run_bot():
    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=10)
        except Exception:
            time.sleep(5)

threading.Thread(target=run_bot, daemon=True).start()
while True:
    time.sleep(1)
