import os
import sqlite3
import requests
from fastapi import FastAPI, Request
from groq import Groq

app = FastAPI()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
groq = Groq(api_key=GROQ_API_KEY)

# ===== БАЗА ДАННЫХ =====

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
CREATE TABLE IF NOT EXISTS memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    role TEXT,
    content TEXT
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

def get_user(user_id):
    cursor.execute("SELECT role, message_count FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if not result:
        cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        return "ассистент", 0
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

# ===== МЕНЮ =====

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
            role, count = get_user(user_id)
            send_message(chat_id, f"📊 Сообщений: {count}\nРоль: {role}", main_menu())

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

    role, _ = get_user(user_id)
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
