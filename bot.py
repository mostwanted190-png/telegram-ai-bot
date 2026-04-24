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

ADMIN_ID = 123456789  # <-- ВСТАВЬ СВОЙ TELEGRAM ID

DAILY_LIMIT = 20

# ===== БАЗА =====

conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    role TEXT DEFAULT 'ассистент',
    message_count INTEGER DEFAULT 0,
    subscription INTEGER DEFAULT 0
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

# ===== РОЛИ =====

ROLES = {
    "ассистент": "Ты дружелюбный AI ассистент. Отвечай на русском.",
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

def get_user(user_id):
    cursor.execute("SELECT role, message_count, subscription FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if not result:
        cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        return "ассистент", 0, 0
    return result

def save_message(user_id, role, content):
    cursor.execute(
        "INSERT INTO memory (user_id, role, content) VALUES (?, ?, ?)",
        (user_id, role, content)
    )
    conn.commit()

def get_memory(user_id, limit=10):
    cursor.execute(
        "SELECT role, content FROM memory WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    )
    rows = cursor.fetchall()
    return [{"role": r, "content": c} for r, c in reversed(rows)]

def clear_memory(user_id):
    cursor.execute("DELETE FROM memory WHERE user_id = ?", (user_id,))
    conn.commit()

def increment_message_count(user_id):
    cursor.execute(
        "UPDATE users SET message_count = message_count + 1 WHERE user_id = ?",
        (user_id,)
    )
    conn.commit()

def check_limit(user_id):
    today = datetime.now().strftime("%Y-%m-%d")

    cursor.execute("SELECT subscription FROM users WHERE user_id = ?", (user_id,))
    sub = cursor.fetchone()

    if sub and sub[0] == 1:
        return True

    cursor.execute(
        "SELECT count FROM usage WHERE user_id = ? AND date = ?",
        (user_id, today)
    )
    result = cursor.fetchone()

    if not result:
        cursor.execute(
            "INSERT INTO usage (user_id, date, count) VALUES (?, ?, 1)",
            (user_id, today)
        )
        conn.commit()
        return True

    if result[0] >= DAILY_LIMIT:
        return False

    cursor.execute(
        "UPDATE usage SET count = count + 1 WHERE user_id = ? AND date = ?",
        (user_id, today)
    )
    conn.commit()
    return True

def main_menu():
    return {
        "inline_keyboard": [
            [{"text": "🎭 Роли", "callback_data": "roles"}],
            [{"text": "🧠 Очистить память", "callback_data": "clear"}],
            [{"text": "📊 Статистика", "callback_data": "stats"}],
        ]
    }

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
                    [{"text": role, "callback_data": f"role_{role}"}]
                    for role in ROLES.keys()
                ]
            }
            send_message(chat_id, "🎭 Выбери роль:", keyboard)

        elif action.startswith("role_"):
            role = action.replace("role_", "")
            cursor.execute("UPDATE users SET role = ? WHERE user_id = ?", (role, user_id))
            conn.commit()
            clear_memory(user_id)
            send_message(chat_id, f"✅ Роль: {role}", main_menu())

        elif action == "clear":
            clear_memory(user_id)
            send_message(chat_id, "🧠 Память очищена", main_menu())

        elif action == "stats":
            role, count, sub = get_user(user_id)
            send_message(
                chat_id,
                f"📊 Сообщений: {count}\n"
                f"Роль: {role}\n"
                f"Подписка: {'✅ Да' if sub else '❌ Нет'}",
                main_menu()
            )

        return {"ok": True}

    # MESSAGE
    if "message" not in data:
        return {"ok": True}

    message = data["message"]
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text")

    if text == "/start":
        send_message(chat_id, "🤖 Добро пожаловать!", main_menu())
        return {"ok": True}

    if text == "/buy":
        send_message(chat_id,
            "💎 Подписка — 299₽/месяц.\n"
            "Напишите администратору для подключения.")
        return {"ok": True}

    if text and text.startswith("/give_sub") and user_id == ADMIN_ID:
        target = int(text.split()[1])
        cursor.execute("UPDATE users SET subscription = 1 WHERE user_id = ?", (target,))
        conn.commit()
        send_message(chat_id, "✅ Подписка выдана")
        return {"ok": True}

    role, _, _ = get_user(user_id)

    if not check_limit(user_id):
        send_message(chat_id,
            "🚫 Лимит 20 сообщений в день.\n"
            "Купите подписку: /buy")
        return {"ok": True}

    increment_message_count(user_id)

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
