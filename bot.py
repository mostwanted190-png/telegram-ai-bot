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
    user_id INTEGER PRIMARY KEY,
    role TEXT DEFAULT 'ассистент',
    message_count INTEGER DEFAULT 0,
    subscription_until TEXT,
    reset_time TEXT,
    custom_limit INTEGER,
    blocked INTEGER DEFAULT 0,
    last_active TEXT
)
""")

conn.commit()

# ===== РОЛИ =====

ROLES = {
    "ассистент": "Ты дружелюбный AI ассистент.",
    "программист": "Ты опытный программист.",
    "учитель": "Ты терпеливый учитель."
}

# ===== МЕНЮ =====

def main_menu(is_admin=False):
    keyboard = [
        ["🎭 Роли", "🎨 Картинка"],
        ["📊 Статистика"]
    ]
    if is_admin:
        keyboard.append(["⚙ Админ-панель"])
    return {"keyboard": keyboard, "resize_keyboard": True}

# ===== УТИЛИТЫ =====

def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload)

def send_photo_by_url(chat_id, image_url):
    requests.post(
        f"{TELEGRAM_API}/sendPhoto",
        json={"chat_id": chat_id, "photo": image_url}
    )

def get_user(user_id):
    cursor.execute("""
    SELECT role, message_count, subscription_until, reset_time,
           custom_limit, blocked
    FROM users WHERE user_id = ?
    """, (user_id,))
    result = cursor.fetchone()

    if not result:
        reset = (datetime.now() + timedelta(hours=24)).isoformat()
        cursor.execute(
            "INSERT INTO users (user_id, reset_time, last_active) VALUES (?, ?, ?)",
            (user_id, reset, datetime.now().isoformat())
        )
        conn.commit()
        return "ассистент", 0, None, reset, None, 0

    return result

def update_activity(user_id):
    cursor.execute(
        "UPDATE users SET last_active = ? WHERE user_id = ?",
        (datetime.now().isoformat(), user_id)
    )
    conn.commit()

def is_subscription_active(subscription_until):
    if not subscription_until:
        return False
    return datetime.now() < datetime.fromisoformat(subscription_until)

def check_limit(user_id):
    if user_id == ADMIN_ID:
        return True, None

    role, message_count, subscription_until, reset_time, custom_limit, blocked = get_user(user_id)

    if blocked == 1:
        return False, "blocked"

    if is_subscription_active(subscription_until):
        return True, None

    limit = custom_limit if custom_limit else FREE_LIMIT

    now = datetime.now()
    reset_dt = datetime.fromisoformat(reset_time)

    if now >= reset_dt:
        new_reset = (now + timedelta(hours=24)).isoformat()
        cursor.execute(
            "UPDATE users SET message_count = 0, reset_time = ? WHERE user_id = ?",
            (new_reset, user_id)
        )
        conn.commit()
        return True, None

    if message_count >= limit:
        remaining = reset_dt - now
        return False, remaining

    cursor.execute(
        "UPDATE users SET message_count = message_count + 1 WHERE user_id = ?",
        (user_id,)
    )
    conn.commit()

    return True, None

def generate_image_url(prompt):
    encoded = quote(prompt)
    return f"https://image.pollinations.ai/prompt/{encoded}?width=768&height=768&nologo=true"

# ===== WEBHOOK =====

@app.post("/")
async def webhook(request: Request):
    data = await request.json()

    if "message" not in data:
        return {"ok": True}

    message = data["message"]
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text")

    role, message_count, subscription_until, reset_time, custom_limit, blocked = get_user(user_id)
    update_activity(user_id)

    if text == "/start":
        send_message(chat_id, "🤖 Добро пожаловать!", main_menu(is_admin=(user_id == ADMIN_ID)))
        return {"ok": True}

    # ===== АДМИН КОМАНДЫ =====

    if text == "⚙ Админ-панель" and user_id == ADMIN_ID:
        send_message(chat_id,
                     "Админ-команды:\n"
                     "/pro ID\n"
                     "/unpro ID\n"
                     "/setlimit ID 50\n"
                     "/block ID\n"
                     "/unblock ID\n"
                     "/top\n"
                     "/activity",
                     main_menu(True))
        return {"ok": True}

    if text and user_id == ADMIN_ID:

        if text.startswith("/pro"):
            target = int(text.split()[1])
            until = (datetime.now() + timedelta(days=30)).isoformat()
            cursor.execute("UPDATE users SET subscription_until = ? WHERE user_id = ?", (until, target))
            conn.commit()
            send_message(chat_id, f"✅ PRO выдан до {until[:10]}")
            return {"ok": True}

        if text.startswith("/unpro"):
            target = int(text.split()[1])
            cursor.execute("UPDATE users SET subscription_until = NULL WHERE user_id = ?", (target,))
            conn.commit()
            send_message(chat_id, "✅ PRO снят")
            return {"ok": True}

        if text.startswith("/setlimit"):
            parts = text.split()
            target = int(parts[1])
            new_limit = int(parts[2])
            cursor.execute("UPDATE users SET custom_limit = ? WHERE user_id = ?", (new_limit, target))
            conn.commit()
            send_message(chat_id, f"✅ Лимит изменён на {new_limit}")
            return {"ok": True}

        if text.startswith("/block"):
            target = int(text.split()[1])
            cursor.execute("UPDATE users SET blocked = 1 WHERE user_id = ?", (target,))
            conn.commit()
            send_message(chat_id, "🚫 Пользователь заблокирован")
            return {"ok": True}

        if text.startswith("/unblock"):
            target = int(text.split()[1])
            cursor.execute("UPDATE users SET blocked = 0 WHERE user_id = ?", (target,))
            conn.commit()
            send_message(chat_id, "✅ Пользователь разблокирован")
            return {"ok": True}

        if text == "/top":
            cursor.execute("SELECT user_id, message_count FROM users ORDER BY message_count DESC LIMIT 10")
            users = cursor.fetchall()
            text = "🏆 ТОП пользователей:\n"
            for u in users:
                text += f"{u[0]} — {u[1]}\n"
            send_message(chat_id, text)
            return {"ok": True}

        if text == "/activity":
            cursor.execute("SELECT COUNT(*) FROM users WHERE last_active >= ?", 
                           ((datetime.now() - timedelta(days=1)).isoformat(),))
            count = cursor.fetchone()[0]
            send_message(chat_id, f"📊 Активных за 24ч: {count}")
            return {"ok": True}

    # ===== ОБЫЧНЫЙ ПОЛЬЗОВАТЕЛЬ =====

    allowed, info = check_limit(user_id)

    if allowed is False:
        if info == "blocked":
            send_message(chat_id, "🚫 Вы заблокированы.")
            return {"ok": True}
        else:
            hours = info.seconds // 3600
            minutes = (info.seconds % 3600) // 60
            send_message(chat_id,
                         f"🚫 Лимит исчерпан.\n⏳ Через {hours}ч {minutes}м",
                         main_menu(is_admin=(user_id == ADMIN_ID)))
            return {"ok": True}

    if text.startswith("/image"):
        prompt = text.replace("/image", "").strip()
        image_url = generate_image_url(prompt)
        send_photo_by_url(chat_id, image_url)
        return {"ok": True}

    response = groq.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": ROLES[role]},
            {"role": "user", "content": text}
        ],
        max_tokens=600,
    )

    reply = response.choices[0].message.content
    send_message(chat_id, reply, main_menu(is_admin=(user_id == ADMIN_ID)))

    return {"ok": True}
