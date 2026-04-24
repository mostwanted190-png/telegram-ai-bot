import os
import requests
from fastapi import FastAPI, Request
from groq import Groq

app = FastAPI()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
groq = Groq(api_key=GROQ_API_KEY)

# ===== ПАМЯТЬ И РОЛИ =====

user_memory = {}
user_roles = {}

MAX_HISTORY = 10

ROLES = {
    "assistant": "Ты дружелюбный AI ассистент. Отвечай на русском.",
    "programmer": "Ты опытный программист. Помогай писать код.",
    "teacher": "Ты учитель. Объясняй просто и понятно.",
    "joker": "Ты шутишь и отвечаешь с юмором.",
    "psychologist": "Ты поддерживающий психолог.",
}

# ===== УТИЛИТА ОТПРАВКИ =====

def send_message(chat_id, text):
    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": chat_id, "text": text}
    )

# ===== WEBHOOK =====

@app.post("/")
async def webhook(request: Request):
    data = await request.json()

    if "message" not in data:
        return {"ok": True}

    chat_id = data["message"]["chat"]["id"]
    user_id = data["message"]["from"]["id"]
    text = data["message"].get("text", "")

    # ===== КОМАНДЫ =====

    if text == "/start":
        send_message(chat_id,
                     "🤖 AI Бот активен!\n\n"
                     "/role — сменить роль\n"
                     "/roles — список ролей")
        return {"ok": True}

    if text == "/roles":
        roles_list = "\n".join(ROLES.keys())
        send_message(chat_id, f"Доступные роли:\n{roles_list}")
        return {"ok": True}

    if text.startswith("/role"):
        parts = text.split()
        if len(parts) < 2:
            send_message(chat_id, "Использование: /role assistant")
            return {"ok": True}

        role = parts[1]
        if role not in ROLES:
            send_message(chat_id, "Такой роли нет.")
            return {"ok": True}

        user_roles[user_id] = role
        send_message(chat_id, f"✅ Роль изменена на {role}")
        return {"ok": True}

    # ===== AI ЛОГИКА =====

    if user_id not in user_memory:
        user_memory[user_id] = []

    role = user_roles.get(user_id, "assistant")
    system_prompt = ROLES[role]

    user_memory[user_id].append({"role": "user", "content": text})

    messages = [{"role": "system", "content": system_prompt}] + \
               user_memory[user_id][-MAX_HISTORY:]

    try:
        response = groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=1000,
            temperature=0.7,
        )

        reply = response.choices[0].message.content

        user_memory[user_id].append({"role": "assistant", "content": reply})
        user_memory[user_id] = user_memory[user_id][-MAX_HISTORY:]

        send_message(chat_id, reply)

    except Exception as e:
        send_message(chat_id, "❌ Ошибка ИИ. Попробуй позже.")

    return {"ok": True}
