import os
from aiohttp import web
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from collections import defaultdict, deque

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "final-secret").strip()
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "https://aismartzenbot-smartzenbot.up.railway.app").strip()
WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"
WEBHOOK_URL = f"{WEBHOOK_BASE_URL}{WEBHOOK_PATH}"

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
router = Router()

@router.message(lambda msg: msg.text == "🧹 Очистить контекст")
async def clear_button(message: Message):
    chat_histories.pop(message.chat.id, None)
    await message.answer("🧠 Контекст очищен. О чём поговорим?")

@router.message(Command("start"))
async def start(message: Message):
    # 🔥 Исправленная ссылка: убрано "blob", добавлено "raw", убраны пробелы
    welcome_image_url = "https://github.com/aurora-irkutsk/AI_smartzenbot/raw/main/start.png"
    
    await message.answer_photo(
        photo=welcome_image_url,
        caption=(
            "🧠 Привет!\n\n" 
            "Я Smart_Zen — ваш личный ассистент ❤️\n\n"
            "Отвечаю на вопросы, объясняю сложное простым языком, помогаю в учёбе и работе 🔥\n\n"
            "💡 Просто напишите свой запрос!\n\n"
            "Например: Что ты можешь? 🤷‍♂️"
        )
    )

@router.message()
async def handle_message(message: Message):
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    try:
        from openai import OpenAI
        client = OpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=os.getenv("GROQ_API_KEY", "").strip()
        )
        
        chat_id = message.chat.id
        user_message = {"role": "user", "content": message.text}
        
        # Формируем историю: system + прошлые сообщения + новый запрос
        messages = [
            {
                "role": "system",
                "content": "Ты — умный помощник. Отвечай, по делу, на языке пользователя. Никогда не упоминай, что ты ИИ."
            }
        ]
        
        # Добавляем историю диалога
        messages.extend(chat_histories[chat_id])
        messages.append(user_message)
        
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            timeout=30.0
        )
        
        ai_reply = response.choices[0].message.content.strip()
        ai_message = {"role": "assistant", "content": ai_reply}
        
        # Сохраняем обмен в историю
        chat_histories[chat_id].append(user_message)
        chat_histories[chat_id].append(ai_message)
        
        await message.answer(ai_reply)
        
    except Exception as e:
        await message.answer("⚠️ Временно не могу ответить.")

dp.include_router(router)

# Хранилище истории: {chat_id: deque([msg1, msg2, ...])}
chat_histories = defaultdict(lambda: deque(maxlen=6))  # 3 пары = 6 сообщений

async def on_startup(app):
    print(f"✅ Устанавливаю webhook: {WEBHOOK_URL}")
    await bot.set_webhook(WEBHOOK_URL, secret_token=WEBHOOK_SECRET)

async def on_shutdown(app):
    await bot.delete_webhook()
    await bot.session.close()

def main():
    app = web.Application()
    SimpleRequestHandler(dp, bot, secret_token=WEBHOOK_SECRET).register(app, path=WEBHOOK_PATH)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))

if __name__ == "__main__":
    main()
