import os
import sqlite3
import requests
from datetime import datetime, timedelta
from urllib.parse import quote
from fastapi import FastAPI, Request
from groq import Groq

app = FastAPI()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
groq = Groq(api_key=GROQ_API_KEY)

ADMIN_ID = 6288084946
FREE_LIMIT = 20

# ===== БАЗА =====

conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY
)
""")
conn.commit()


def add_column(column_name, column_type):
    try:
        cursor.execute(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}")
        conn.commit()
    except sqlite3.OperationalError:
        pass


add_column("first_name", "TEXT")
add_column("username", "TEXT")
add_column("role", "TEXT DEFAULT 'ассистент'")
add_column("message_count", "INTEGER DEFAULT 0")
add_column("subscription_until", "TEXT")
add_column("reset_time", "TEXT")
add_column("custom_limit", "INTEGER")
add_column("blocked", "INTEGER DEFAULT 0")
add_column("last_active", "TEXT")

# ===== РОЛИ =====

ROLES = {
    "ассистент": "Ты дружелюбный AI ассистент. Отвечай понятно и полезно на русском языке.",
    "программист": "Ты опытный программист. Помогай с кодом, объясняй ошибки и давай примеры.",
    "учитель": "Ты терпеливый учитель. Объясняй простым языком, с примерами и пошагово."
}

# ===== МЕНЮ =====

def main_menu(is_admin=False):
    keyboard = [
        ["🎭 Роли", "🎨 Картинка"],
        ["📊 Статистика", "💎 Подписка"]
    ]

    if is_admin:
        keyboard.append(["⚙ Админ-панель"])

    return {
        "keyboard": keyboard,
        "resize_keyboard": True
    }


def roles_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "🧑‍💼 Ассистент", "callback_data": "role_ассистент"}],
            [{"text": "👨‍💻 Программист", "callback_data": "role_программист"}],
            [{"text": "📚 Учитель", "callback_data": "role_учитель"}]
        ]
    }

# ===== УТИЛИТЫ TELEGRAM =====

def send_message(chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup:
        payload["reply_markup"] = reply_markup

    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload)


def send_photo_by_url(chat_id, image_url):
    requests.post(
        f"{TELEGRAM_API}/sendPhoto",
        json={
            "chat_id": chat_id,
            "photo": image_url
        }
    )


def answer_callback(callback_id):
    requests.post(
        f"{TELEGRAM_API}/answerCallbackQuery",
        json={"callback_query_id": callback_id}
    )

# ===== ПОЛЬЗОВАТЕЛИ =====

def ensure_user(user_id, first_name=None, username=None):
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()

    now = datetime.now().isoformat()

    if not result:
        reset_time = (datetime.now() + timedelta(hours=24)).isoformat()
        cursor.execute("""
            INSERT INTO users (
                user_id, first_name, username, role, message_count,
                subscription_until, reset_time, custom_limit, blocked, last_active
            )
            VALUES (?, ?, ?, 'ассистент', 0, NULL, ?, NULL, 0, ?)
        """, (user_id, first_name, username, reset_time, now))
        conn.commit()
    else:
        cursor.execute("""
            UPDATE users
            SET first_name = ?, username = ?, last_active = ?
            WHERE user_id = ?
        """, (first_name, username, now, user_id))
        conn.commit()


def get_user(user_id):
    cursor.execute("""
        SELECT role, message_count, subscription_until, reset_time,
               custom_limit, blocked, first_name, username, last_active
        FROM users
        WHERE user_id = ?
    """, (user_id,))

    result = cursor.fetchone()

    if not result:
        ensure_user(user_id)
        return get_user(user_id)

    role, message_count, subscription_until, reset_time, custom_limit, blocked, first_name, username, last_active = result

    if not role:
        role = "ассистент"

    if message_count is None:
        message_count = 0

    if not reset_time:
        reset_time = (datetime.now() + timedelta(hours=24)).isoformat()
        cursor.execute(
            "UPDATE users SET reset_time = ? WHERE user_id = ?",
            (reset_time, user_id)
        )
        conn.commit()

    if blocked is None:
        blocked = 0

    return {
        "role": role,
        "message_count": message_count,
        "subscription_until": subscription_until,
        "reset_time": reset_time,
        "custom_limit": custom_limit,
        "blocked": blocked,
        "first_name": first_name,
        "username": username,
        "last_active": last_active
    }


def is_subscription_active(subscription_until):
    if not subscription_until:
        return False

    try:
        return datetime.now() < datetime.fromisoformat(subscription_until)
    except:
        return False


def get_user_limit(user):
    if user["custom_limit"]:
        return user["custom_limit"]
    return FREE_LIMIT


def check_limit(user_id):
    if user_id == ADMIN_ID:
        return True, None

    user = get_user(user_id)

    if user["blocked"] == 1:
        return False, "blocked"

    if is_subscription_active(user["subscription_until"]):
        return True, None

    now = datetime.now()
    reset_dt = datetime.fromisoformat(user["reset_time"])

    if now >= reset_dt:
        new_reset = (now + timedelta(hours=24)).isoformat()
        cursor.execute("""
            UPDATE users
            SET message_count = 0, reset_time = ?
            WHERE user_id = ?
        """, (new_reset, user_id))
        conn.commit()
        return True, None

    limit = get_user_limit(user)

    if user["message_count"] >= limit:
        remaining = reset_dt - now
        return False, remaining

    cursor.execute("""
        UPDATE users
        SET message_count = message_count + 1
        WHERE user_id = ?
    """, (user_id,))
    conn.commit()

    return True, None

# ===== КАРТИНКИ =====

def translate_to_english(text):
    try:
        response = groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": "Translate the Russian text into a detailed English prompt for image generation. Return only the English prompt."
                },
                {
                    "role": "user",
                    "content": text
                }
            ],
            max_tokens=200
        )

        return response.choices[0].message.content.strip()
    except:
        return text


def generate_image_url(prompt):
    encoded = quote(prompt)
    return f"https://image.pollinations.ai/prompt/{encoded}?width=768&height=768&nologo=true"

# ===== АДМИНКА =====

def admin_help():
    return (
        "⚙ Админ-панель\n\n"
        "Команды:\n\n"
        "/users — список пользователей с ID\n"
        "/top — топ пользователей по сообщениям\n"
        "/activity — активность за 24 часа\n\n"
        "/pro ID — выдать PRO на 30 дней\n"
        "/unpro ID — убрать PRO\n"
        "/setlimit ID 50 — изменить лимит\n"
        "/block ID — заблокировать\n"
        "/unblock ID — разблокировать\n\n"
        "Пример:\n"
        "/block 123456789"
    )


def format_user_line(row):
    user_id, first_name, username, message_count, blocked, subscription_until, last_active = row

    name = first_name or "Без имени"
    uname = f"@{username}" if username else "без username"

    status = "🚫 BLOCK" if blocked == 1 else "✅ OK"

    if is_subscription_active(subscription_until):
        status += " | 💎 PRO"

    return f"{user_id} | {name} | {uname} | {message_count} сообщений | {status}"

# ===== WEBHOOK =====

@app.post("/")
async def webhook(request: Request):
    data = await request.json()

    # ===== CALLBACK ДЛЯ РОЛЕЙ =====

    if "callback_query" in data:
        callback = data["callback_query"]
        user_id = callback["from"]["id"]
        chat_id = callback["message"]["chat"]["id"]
        action = callback["data"]

        first_name = callback["from"].get("first_name")
        username = callback["from"].get("username")

        ensure_user(user_id, first_name, username)
        answer_callback(callback["id"])

        if action.startswith("role_"):
            role = action.replace("role_", "")

            if role in ROLES:
                cursor.execute(
                    "UPDATE users SET role = ? WHERE user_id = ?",
                    (role, user_id)
                )
                conn.commit()

                send_message(
                    chat_id,
                    f"✅ Роль изменена на: {role}",
                    main_menu(is_admin=(user_id == ADMIN_ID))
                )
            else:
                send_message(chat_id, "❌ Такой роли нет.")

        return {"ok": True}

    # ===== СООБЩЕНИЯ =====

    if "message" not in data:
        return {"ok": True}

    message = data["message"]
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]

    first_name = message["from"].get("first_name")
    username = message["from"].get("username")
    text = message.get("text")

    ensure_user(user_id, first_name, username)

    if not text:
        return {"ok": True}

    is_admin = user_id == ADMIN_ID

    # ===== БАЗОВЫЕ КОМАНДЫ =====

    if text == "/start":
        send_message(
            chat_id,
            "🤖 Добро пожаловать!\n\n"
            "Используйте кнопки меню ниже.",
            main_menu(is_admin=is_admin)
        )
        return {"ok": True}

    if text == "/id":
        send_message(chat_id, f"Ваш Telegram ID: {user_id}")
        return {"ok": True}

    # ===== МЕНЮ =====

    if text == "🎭 Роли":
        send_message(chat_id, "🎭 Выберите роль:", roles_keyboard())
        return {"ok": True}

    if text == "🎨 Картинка":
        send_message(
            chat_id,
            "🎨 Генерация картинок\n\n"
            "Напишите команду:\n"
            "/image описание\n\n"
            "Пример:\n"
            "/image закат над морем",
            main_menu(is_admin=is_admin)
        )
        return {"ok": True}

    if text == "📊 Статистика":
        user = get_user(user_id)
        limit = get_user_limit(user)

        if is_subscription_active(user["subscription_until"]):
            sub_text = f"💎 PRO активен до: {user['subscription_until'][:10]}"
        else:
            reset_dt = datetime.fromisoformat(user["reset_time"])
            sub_text = f"⏳ Сброс лимита: {reset_dt.strftime('%d.%m %H:%M')}"

        status = "🚫 Заблокирован" if user["blocked"] == 1 else "✅ Активен"

        send_message(
            chat_id,
            f"📊 Ваша статистика\n\n"
            f"ID: {user_id}\n"
            f"Роль: {user['role']}\n"
            f"Сообщений: {user['message_count']}/{limit}\n"
            f"Статус: {status}\n"
            f"{sub_text}",
            main_menu(is_admin=is_admin)
        )
        return {"ok": True}

    if text == "💎 Подписка":
        send_message(
            chat_id,
            "💎 PRO подписка\n\n"
            "PRO убирает лимиты на 30 дней.\n"
            "Для подключения напишите администратору.",
            main_menu(is_admin=is_admin)
        )
        return {"ok": True}

    # ===== АДМИН-ПАНЕЛЬ =====

    if text == "⚙ Админ-панель" and is_admin:
        send_message(chat_id, admin_help(), main_menu(is_admin=True))
        return {"ok": True}

    if is_admin:
        if text == "/users":
            cursor.execute("""
                SELECT user_id, first_name, username, message_count, blocked,
                       subscription_until, last_active
                FROM users
                ORDER BY last_active DESC
                LIMIT 20
            """)
            users = cursor.fetchall()

            if not users:
                send_message(chat_id, "Пользователей пока нет.")
                return {"ok": True}

            msg = "👥 Последние пользователи:\n\n"
            for row in users:
                msg += format_user_line(row) + "\n\n"

            send_message(chat_id, msg)
            return {"ok": True}

        if text == "/top":
            cursor.execute("""
                SELECT user_id, first_name, username, message_count, blocked,
                       subscription_until, last_active
                FROM users
                ORDER BY message_count DESC
                LIMIT 10
            """)
            users = cursor.fetchall()

            msg = "🏆 ТОП пользователей:\n\n"
            for row in users:
                msg += format_user_line(row) + "\n\n"

            send_message(chat_id, msg)
            return {"ok": True}

        if text == "/activity":
            since = (datetime.now() - timedelta(hours=24)).isoformat()
            cursor.execute("SELECT COUNT(*) FROM users WHERE last_active >= ?", (since,))
            active = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM users")
            total = cursor.fetchone()[0]

            send_message(
                chat_id,
                f"📊 Активность\n\n"
                f"Всего пользователей: {total}\n"
                f"Активных за 24 часа: {active}"
            )
            return {"ok": True}

        if text.startswith("/pro"):
            try:
                target = int(text.split()[1])
                until = (datetime.now() + timedelta(days=30)).isoformat()

                ensure_user(target)

                cursor.execute(
                    "UPDATE users SET subscription_until = ? WHERE user_id = ?",
                    (until, target)
                )
                conn.commit()

                send_message(chat_id, f"✅ PRO выдан пользователю {target} до {until[:10]}")
            except:
                send_message(chat_id, "Ошибка. Пример: /pro 123456789")
            return {"ok": True}

        if text.startswith("/unpro"):
            try:
                target = int(text.split()[1])
                cursor.execute(
                    "UPDATE users SET subscription_until = NULL WHERE user_id = ?",
                    (target,)
                )
                conn.commit()

                send_message(chat_id, f"✅ PRO убран у пользователя {target}")
            except:
                send_message(chat_id, "Ошибка. Пример: /unpro 123456789")
            return {"ok": True}

        if text.startswith("/setlimit"):
            try:
                parts = text.split()
                target = int(parts[1])
                new_limit = int(parts[2])

                ensure_user(target)

                cursor.execute(
                    "UPDATE users SET custom_limit = ? WHERE user_id = ?",
                    (new_limit, target)
                )
                conn.commit()

                send_message(chat_id, f"✅ Лимит пользователя {target} изменён на {new_limit}")
            except:
                send_message(chat_id, "Ошибка. Пример: /setlimit 123456789 50")
            return {"ok": True}

        if text.startswith("/block"):
            try:
                target = int(text.split()[1])

                ensure_user(target)

                cursor.execute(
                    "UPDATE users SET blocked = 1 WHERE user_id = ?",
                    (target,)
                )
                conn.commit()

                send_message(chat_id, f"🚫 Пользователь {target} заблокирован")
            except:
                send_message(chat_id, "Ошибка. Пример: /block 123456789")
            return {"ok": True}

        if text.startswith("/unblock"):
            try:
                target = int(text.split()[1])

                cursor.execute(
                    "UPDATE users SET blocked = 0 WHERE user_id = ?",
                    (target,)
                )
                conn.commit()

                send_message(chat_id, f"✅ Пользователь {target} разблокирован")
            except:
                send_message(chat_id, "Ошибка. Пример: /unblock 123456789")
            return {"ok": True}

    # ===== ПРОВЕРКА ЛИМИТА И БЛОКИРОВКИ =====

    allowed, info = check_limit(user_id)

    if not allowed:
        if info == "blocked":
            send_message(chat_id, "🚫 Вы заблокированы администратором.")
            return {"ok": True}

        hours = info.seconds // 3600
        minutes = (info.seconds % 3600) // 60

        send_message(
            chat_id,
            f"🚫 Лимит исчерпан.\n"
            f"⏳ Сброс через: {hours}ч {minutes}м\n\n"
            f"💎 Для безлимита подключите PRO.",
            main_menu(is_admin=is_admin)
        )
        return {"ok": True}

    # ===== ГЕНЕРАЦИЯ КАРТИНОК =====

    if text.startswith("/image"):
        prompt = text.replace("/image", "").strip()

        if not prompt:
            send_message(
                chat_id,
                "Напишите описание.\n\nПример:\n/image кот в космосе",
                main_menu(is_admin=is_admin)
            )
            return {"ok": True}

        send_message(chat_id, "🎨 Генерирую изображение...")

        english_prompt = translate_to_english(prompt)
        image_url = generate_image_url(english_prompt)

        send_photo_by_url(chat_id, image_url)
        return {"ok": True}

    # ===== AI ТЕКСТ =====

    user = get_user(user_id)
    role = user["role"]

    try:
        response = groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": ROLES.get(role, ROLES["ассистент"])},
                {"role": "user", "content": text}
            ],
            max_tokens=600,
        )

        reply = response.choices[0].message.content

        send_message(
            chat_id,
            reply,
            main_menu(is_admin=is_admin)
        )

    except:
        send_message(
            chat_id,
            "⚠ Ошибка AI. Попробуйте позже.",
            main_menu(is_admin=is_admin)
        )

    return {"ok": True}
