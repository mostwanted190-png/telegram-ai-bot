import os
import requests
from fastapi import FastAPI, Request
from groq import Groq

app = FastAPI()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
groq = Groq(api_key=GROQ_API_KEY)

user_memory = {}
user_roles = {}
user_stats = {}

ROLES = {
    "ассистент": "Ты дружелюбный AI ассистент. Отвечай на русском.",
    "программист": "Ты опытный программист.",
    "учитель": "Ты объясняешь просто.",
    "шутник": "Ты отвечаешь с юмором.",
    "психолог": "Ты поддерживаешь.",
    "художник": "Ты создаёшь детальные промпты для генерации изображений."
}

# ===== ОТПРАВКА =====

def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload)

# ===== ГЛАВНОЕ МЕНЮ =====

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
            user_roles[user_id] = role
            user_memory[user_id] = []
            send_message(chat_id, f"✅ Роль: {role}", main_menu())

        elif action == "clear":
            user_memory[user_id] = []
            send_message(chat_id, "🧠 Память очищена", main_menu())

        elif action == "stats":
            messages = user_stats.get(user_id, 0)
            send_message(chat_id, f"📊 Ты отправил сообщений: {messages}", main_menu())

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

    if user_id not in user_memory:
        user_memory[user_id] = []
    if user_id not in user_stats:
        user_stats[user_id] = 0

    user_stats[user_id] += 1

    role = user_roles.get(user_id, "ассистент")
    system_prompt = ROLES[role]

    user_memory[user_id].append({"role": "user", "content": text})

    messages = [{"role": "system", "content": system_prompt}] + \
               user_memory[user_id][-10:]

    response = groq.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        max_tokens=800,
    )

    reply = response.choices[0].message.content
    user_memory[user_id].append({"role": "assistant", "content": reply})

    send_message(chat_id, reply)

    return {"ok": True}
