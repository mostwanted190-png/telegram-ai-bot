import os
import sqlite3
import requests
from datetime import datetime
from fastapi import FastAPI, Request
from groq import Groq

app = FastAPI()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
groq = Groq(api_key=GROQ_API_KEY)

ADMIN_ID = 6288084946  # ТВОЙ ID
FREE_LIMIT = 20

# ===== БАЗА =====

conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    message_count INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS usage (
    user_id INTEGER,
    date TEXT,
    count INTEGER,
    PRIMARY KEY (user_id, date)
)
""")

conn.commit()

# ===== МЕНЮ =====

def main_menu():
    return {
        "keyboard": [
            ["/image", "/stats"],
            ["/buy"]
        ],
        "resize_keyboard": True
    }

# ===== УТИЛИТЫ =====

def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload)

def send_photo(chat_id, image_bytes):
    requests.post(
        f"{TELEGRAM_API}/sendPhoto",
        files={"photo": ("image.png", image_bytes)},
        data={"chat_id": chat_id}
    )

def check_limit(user_id):
    today = datetime.now().strftime("%Y-%m-%d")

    cursor.execute("SELECT count FROM usage WHERE user_id = ? AND date = ?", (user_id, today))
    result = cursor.fetchone()

    if not result:
        cursor.execute("INSERT INTO usage VALUES (?, ?, 1)", (user_id, today))
        conn.commit()
        return True

    if result[0] >= FREE_LIMIT:
        return False

    cursor.execute("UPDATE usage SET count = count + 1 WHERE user_id = ? AND date = ?", (user_id, today))
    conn.commit()
    return True

# ✅ ГЕНЕРАЦИЯ БЕЗ РЕГИСТРАЦИИ

def generate_image(prompt):
    try:
        url = f"https://image.pollinations.ai/prompt/{prompt}"
        response = requests.get(url, timeout=60)
        if response.status_code == 200:
            return response.content
        return None
    except:
        return None

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

    if not text:
        return {"ok": True}

    # ===== START =====
    if text == "/start":
        send_message(chat_id,
                     "🤖 AI Bot PRO\n\n"
                     "20 сообщений бесплатно в день.",
                     main_menu())
        return {"ok": True}

    # ===== ADMIN =====
    if text == "/users" and user_id == ADMIN_ID:
        cursor.execute("SELECT COUNT(*) FROM users")
        total = cursor.fetchone()[0]
        send_message(chat_id, f"👥 Пользователей: {total}", main_menu())
        return {"ok": True}

    # ===== STATS =====
    if text == "/stats":
        cursor.execute("SELECT SUM(count) FROM usage WHERE user_id = ?", (user_id,))
        total = cursor.fetchone()[0] or 0
        send_message(chat_id, f"📊 Сегодня использовано: {total}/{FREE_LIMIT}", main_menu())
        return {"ok": True}

    # ===== BUY =====
    if text == "/buy":
        send_message(chat_id,
                     "💎 Подписка убирает лимит.\n"
                     "Напишите администратору.",
                     main_menu())
        return {"ok": True}

    # ===== IMAGE =====
    if text.startswith("/image"):
        prompt = text.replace("/image", "").strip()

        if not prompt:
            send_message(chat_id, "Напиши: /image кот в космосе", main_menu())
            return {"ok": True}

        if not check_limit(user_id):
            send_message(chat_id, "🚫 Лимит исчерпан.", main_menu())
            return {"ok": True}

        img = generate_image(prompt)

        if img:
            send_photo(chat_id, img)
        else:
            send_message(chat_id, "❌ Ошибка генерации.", main_menu())

        return {"ok": True}

    # ===== AI ТЕКСТ =====

    if not check_limit(user_id):
        send_message(chat_id, "🚫 Лимит исчерпан.", main_menu())
        return {"ok": True}

    try:
        response = groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Ты полезный AI ассистент."},
                {"role": "user", "content": text}
            ],
            max_tokens=600,
        )

        reply = response.choices[0].message.content
        send_message(chat_id, reply, main_menu())

    except:
        send_message(chat_id, "⚠ Ошибка AI", main_menu())

    return {"ok": True}
