import os
import sqlite3
import requests
from datetime import datetime
from fastapi import FastAPI, Request
from groq import Groq

app = FastAPI()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
STABILITY_API_KEY = os.environ.get("STABILITY_API_KEY")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
groq = Groq(api_key=GROQ_API_KEY)

ADMIN_ID = 6288084946

FREE_LIMIT = 20
PREMIUM_LIMIT = 100

# ===== БАЗА =====

conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    role TEXT DEFAULT 'ассистент',
    message_count INTEGER DEFAULT 0,
    tier INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    role TEXT,
    content TEXT
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

ROLES = {
    "ассистент": "Ты дружелюбный AI ассистент.",
    "программист": "Ты опытный программист.",
    "учитель": "Ты объясняешь просто.",
    "шутник": "Ты отвечаешь с юмором.",
    "психолог": "Ты поддерживаешь.",
    "художник": "Ты создаёшь детальные промпты для генерации изображений."
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

def generate_image(prompt):
    response = requests.post(
        "https://api.stability.ai/v2beta/stable-image/generate/sd3",
        headers={
            "Authorization": f"Bearer {STABILITY_API_KEY}",
            "Accept": "image/*"
        },
        files={
            "prompt": (None, prompt),
            "output_format": (None, "png"),
        },
    )
    if response.status_code == 200:
        return response.content
    return None

def get_user(user_id):
    cursor.execute("SELECT role, tier FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if not result:
        cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        return "ассистент", 0
    return result

def check_limit(user_id):
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("SELECT tier FROM users WHERE user_id = ?", (user_id,))
    tier = cursor.fetchone()[0]

    if tier == 2:
        return True

    limit = FREE_LIMIT if tier == 0 else PREMIUM_LIMIT

    cursor.execute("SELECT count FROM usage WHERE user_id = ? AND date = ?", (user_id, today))
    result = cursor.fetchone()

    if not result:
        cursor.execute("INSERT INTO usage VALUES (?, ?, 1)", (user_id, today))
        conn.commit()
        return True

    if result[0] >= limit:
        return False

    cursor.execute("UPDATE usage SET count = count + 1 WHERE user_id = ? AND date = ?", (user_id, today))
    conn.commit()
    return True

def save_message(user_id, role, content):
    cursor.execute("INSERT INTO memory (user_id, role, content) VALUES (?, ?, ?)",
                   (user_id, role, content))
    conn.commit()

def get_memory(user_id):
    cursor.execute("SELECT role, content FROM memory WHERE user_id = ? ORDER BY id DESC LIMIT 10",
                   (user_id,))
    rows = cursor.fetchall()
    return [{"role": r, "content": c} for r, c in reversed(rows)]

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

    if text == "/start":
        send_message(chat_id,
                     "🤖 AI Бот PRO\n\n"
                     "/image — генерация изображения\n"
                     "/buy — подписка\n"
                     "/stats — статистика")
        return {"ok": True}

    if text == "/buy":
        send_message(chat_id,
                     "💎 Подписка PRO:\n"
                     "• 100 сообщений — Premium\n"
                     "• Безлимит — PRO\n\n"
                     "Свяжитесь с администратором.")
        return {"ok": True}

    if text == "/users" and user_id == ADMIN_ID:
        cursor.execute("SELECT COUNT(*) FROM users")
        total = cursor.fetchone()[0]
        send_message(chat_id, f"👥 Пользователей: {total}")
        return {"ok": True}

    if text == "/stats" and user_id == ADMIN_ID:
        cursor.execute("SELECT SUM(message_count) FROM users")
        total = cursor.fetchone()[0] or 0
        send_message(chat_id, f"📊 Всего сообщений: {total}")
        return {"ok": True}

    if text and text.startswith("/image"):
        prompt = text.replace("/image", "").strip()
        if not check_limit(user_id):
            send_message(chat_id, "🚫 Лимит исчерпан.")
            return {"ok": True}

        img = generate_image(prompt)
        if img:
            send_photo(chat_id, img)
        else:
            send_message(chat_id, "❌ Ошибка генерации.")
        return {"ok": True}

    # ===== AI =====

    role, _ = get_user(user_id)

    if not check_limit(user_id):
        send_message(chat_id, "🚫 Лимит сообщений исчерпан.")
        return {"ok": True}

    save_message(user_id, "user", text)
    history = get_memory(user_id)

    messages = [{"role": "system", "content": ROLES[role]}] + history

    response = groq.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        max_tokens=800,
    )

    reply = response.choices[0].message.content
    save_message(user_id, "assistant", reply)

    send_message(chat_id, reply)

    return {"ok": True}
