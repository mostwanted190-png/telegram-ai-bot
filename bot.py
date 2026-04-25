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
    subscription INTEGER DEFAULT 0,
    reset_time TEXT
)
""")

conn.commit()

# ===== РОЛИ =====

ROLES = {
    "ассистент": "Ты дружелюбный AI ассистент.",
    "программист": "Ты опытный программист. Помогай с кодом.",
    "учитель": "Ты терпеливый учитель. Объясняй просто."
}

# ===== МЕНЮ =====

def main_menu():
    return {
        "inline_keyboard": [
            [
                {"text": "🎭 Роли", "callback_data": "roles"},
                {"text": "🎨 Картинка", "callback_data": "image_info"}
            ],
            [
                {"text": "📊 Статистика", "callback_data": "stats"},
                {"text": "💎 Подписка", "callback_data": "buy"}
            ]
        ]
    }

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
    cursor.execute("SELECT role, message_count, subscription, reset_time FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if not result:
        reset = (datetime.now() + timedelta(hours=24)).isoformat()
        cursor.execute(
            "INSERT INTO users (user_id, reset_time) VALUES (?, ?)",
            (user_id, reset)
        )
        conn.commit()
        return "ассистент", 0, 0, reset
    return result

def check_limit(user_id):
    if user_id == ADMIN_ID:
        return True, None

    role, message_count, subscription, reset_time = get_user(user_id)

    if subscription == 1:
        return True, None

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

    if message_count >= FREE_LIMIT:
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

    # CALLBACK
    if "callback_query" in data:
        callback = data["callback_query"]
        user_id = callback["from"]["id"]
        chat_id = callback["message"]["chat"]["id"]
        action = callback["data"]

        requests.post(
            f"{TELEGRAM_API}/answerCallbackQuery",
            json={"callback_query_id": callback["id"]}
        )

        if action == "roles":
            keyboard = {
                "inline_keyboard": [
                    [{"text": "🧑‍💼 Ассистент", "callback_data": "role_ассистент"}],
                    [{"text": "👨‍💻 Программист", "callback_data": "role_программист"}],
                    [{"text": "📚 Учитель", "callback_data": "role_учитель"}]
                ]
            }
            send_message(chat_id, "Выберите роль:", keyboard)

        elif action.startswith("role_"):
            role = action.replace("role_", "")
            cursor.execute("UPDATE users SET role = ? WHERE user_id = ?", (role, user_id))
            conn.commit()
            send_message(chat_id, f"✅ Роль изменена на: {role}", main_menu())

        elif action == "image_info":
            send_message(chat_id, "Напишите: /image описание", main_menu())

        elif action == "stats":
            role, message_count, subscription, reset_time = get_user(user_id)
            if subscription == 1:
                text = "💎 У вас подписка PRO."
            else:
                reset_dt = datetime.fromisoformat(reset_time)
                text = (
                    f"📊 Использовано: {message_count}/{FREE_LIMIT}\n"
                    f"⏳ Сброс: {reset_dt.strftime('%d.%m %H:%M')}"
                )
            send_message(chat_id, text, main_menu())

        elif action == "buy":
            send_message(chat_id, "💎 Напишите администратору для подключения PRO.", main_menu())

        return {"ok": True}

    if "message" not in data:
        return {"ok": True}

    message = data["message"]
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text")

    if text == "/start":
        send_message(chat_id, "🤖 Добро пожаловать!", main_menu())
        return {"ok": True}

    if text and text.startswith("/give_sub") and user_id == ADMIN_ID:
        target = int(text.split()[1])
        cursor.execute("UPDATE users SET subscription = 1 WHERE user_id = ?", (target,))
        conn.commit()
        send_message(chat_id, "✅ Подписка выдана.")
        return {"ok": True}

    if text and text.startswith("/image"):
        allowed, remaining = check_limit(user_id)
        if not allowed:
            hours = remaining.seconds // 3600
            minutes = (remaining.seconds % 3600) // 60
            send_message(chat_id,
                         f"🚫 Лимит исчерпан.\n⏳ Через {hours}ч {minutes}м",
                         main_menu())
            return {"ok": True}

        prompt = text.replace("/image", "").strip()
        image_url = generate_image_url(prompt)
        send_photo_by_url(chat_id, image_url)
        return {"ok": True}

    allowed, remaining = check_limit(user_id)
    if not allowed:
        hours = remaining.seconds // 3600
        minutes = (remaining.seconds % 3600) // 60
        send_message(chat_id,
                     f"🚫 Лимит исчерпан.\n⏳ Через {hours}ч {minutes}м",
                     main_menu())
        return {"ok": True}

    role, _, _, _ = get_user(user_id)

    response = groq.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": ROLES[role]},
            {"role": "user", "content": text}
        ],
        max_tokens=600,
    )

    reply = response.choices[0].message.content
    send_message(chat_id, reply, main_menu())

    return {"ok": True}
