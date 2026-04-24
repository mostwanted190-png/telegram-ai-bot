import os
import sqlite3
import requests
from datetime import datetime
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

# ===== РОЛИ (только нужные) =====

ROLES = {
    "ассистент": "Ты дружелюбный и полезный AI ассистент. Отвечай на русском.",
    "программист": "Ты опытный программист. Помогай с кодом и объясняй.",
    "учитель": "Ты терпеливый учитель. Объясняй просто и с примерами."
}

# ===== МЕНЮ =====

def main_menu():
    return {
        "inline_keyboard": [
            [
                {"text": "🎭 Роли", "callback_data": "menu_roles"},
                {"text": "🎨 Генерация картинок", "callback_data": "menu_image"}
            ],
            [
                {"text": "🧠 Очистить память", "callback_data": "clear"},
                {"text": "📊 Статистика", "callback_data": "stats"}
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

def translate_to_english(text):
    try:
        response = groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Translate the following Russian text into a detailed English prompt for image generation. Only return the English prompt."},
                {"role": "user", "content": text}
            ],
            max_tokens=150,
        )
        return response.choices[0].message.content.strip()
    except:
        return text

def generate_image_url(prompt):
    encoded = quote(prompt)
    return f"https://image.pollinations.ai/prompt/{encoded}?width=768&height=768&nologo=true"

# ===== WEBHOOK =====

@app.post("/")
async def webhook(request: Request):
    data = await request.json()

    if "callback_query" in data:
        callback = data["callback_query"]
        user_id = callback["from"]["id"]
        chat_id = callback["message"]["chat"]["id"]
        action = callback["data"]

        requests.post(f"{TELEGRAM_API}/answerCallbackQuery", json={"callback_query_id": callback["id"]})

        if action == "menu_roles":
            keyboard = {
                "inline_keyboard": [
                    [{"text": "🧑‍💼 Ассистент", "callback_data": "role_ассистент"}],
                    [{"text": "👨‍💻 Программист", "callback_data": "role_программист"}],
                    [{"text": "📚 Учитель", "callback_data": "role_учитель"}]
                ]
            }
            send_message(chat_id, "🎭 Выберите роль:", keyboard)

        elif action.startswith("role_"):
            role = action.replace("role_", "")
            cursor.execute("UPDATE users SET role = ? WHERE user_id = ?", (role, user_id))
            conn.commit()
            send_message(chat_id, f"✅ Роль изменена на: {role}", main_menu())

        elif action == "menu_image":
            send_message(chat_id, "🎨 Напишите команду:\n/image ваше описание", main_menu())

        elif action == "clear":
            cursor.execute("DELETE FROM memory WHERE user_id = ?", (user_id,))
            conn.commit()
            send_message(chat_id, "🧠 Память очищена", main_menu())

        elif action == "stats":
            cursor.execute("SELECT SUM(count) FROM usage WHERE user_id = ?", (user_id,))
            total = cursor.fetchone()[0] or 0
            send_message(chat_id, f"📊 Сегодня использовано: {total}/{FREE_LIMIT}", main_menu())

        return {"ok": True}

    if "message" not in data:
        return {"ok": True}

    message = data["message"]
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text")

    if text == "/start":
        send_message(chat_id, "🤖 Добро пожаловать в AI Bot PRO!", main_menu())
        return {"ok": True}

    if text.startswith("/image"):
        prompt = text.replace("/image", "").strip()
        if not prompt:
            send_message(chat_id, "Напишите: /image описание картинки", main_menu())
            return {"ok": True}

        if not check_limit(user_id):
            send_message(chat_id, "🚫 Лимит сообщений исчерпан.", main_menu())
            return {"ok": True}

        send_message(chat_id, "🎨 Генерирую картинку...")

        english_prompt = translate_to_english(prompt)
        image_url = generate_image_url(english_prompt)
        send_photo_by_url(chat_id, image_url)
        return {"ok": True}

    # AI ответ
    if not check_limit(user_id):
        send_message(chat_id, "🚫 Лимит исчерпан.", main_menu())
        return {"ok": True}

    role = cursor.execute("SELECT role FROM users WHERE user_id = ?", (user_id,)).fetchone()
    role = role[0] if role else "ассистент"

    response = groq.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": ROLES[role]},
            {"role": "user", "content": text}
        ],
        max_tokens=700,
    )

    reply = response.choices[0].message.content
    send_message(chat_id, reply, main_menu())

    return {"ok": True}
