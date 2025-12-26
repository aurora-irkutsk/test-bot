import os
from aiohttp import web
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from collections import defaultdict, deque  # ← ДОБАВЛЕНО

# Хранилище истории диалогов: {chat_id: deque([msg1, msg2, ...])}
chat_histories = defaultdict(lambda: deque(maxlen=6))  # ← ДОБАВЛЕНО

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "final-secret").strip()
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "https://aismartzenbot-smartzenbot.up.railway.app").strip()  # ← УБРАНЫ ПРОБЕЛЫ
WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"
WEBHOOK_URL = f"{WEBHOOK_BASE_URL}{WEBHOOK_PATH}"

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
router = Router()

@router.message(lambda msg: msg.text == "🧹 Очистить контекст")  # ← ДОБАВЛЕНО
async def clear_button(message: Message):
    chat_histories.pop(message.chat.id, None)
    await message.answer("🧠 Контекст очищен. О чём поговорим?")

@router.message(Command("start"))
async def start(message: Message):
    welcome_image_url = "https://github.com/aurora-irkutsk/AI_smartzenbot/raw/main/start.png"  # ← УБРАНЫ ПРОБЕЛЫ
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🧹 Очистить контекст")]],
        resize_keyboard=True
    )
    await message.answer_photo(
        photo=welcome_image_url,
        caption=(
            "🧠 Привет!\n\n" 
            "Я Smart_Zen — ваш личный ассистент ❤️\n\n"
            "Отвечаю на вопросы, объясняю сложное простым языком, помогаю в учёбе и работе 🔥\n\n"
            "💡 Просто напишите свой запрос!\n\n"
            "Например: Что ты умеешь? 🤷‍♂️"
        ),
        reply_markup=kb
    )

@router.message()
async def handle_message(message: Message):
    if not message.text:  # ← ИГНОРИРУЕМ НЕ ТЕКСТОВЫЕ СООБЩЕНИЯ
        return
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        from openai import OpenAI
        import httpx  # ← ЕДИНСТВЕННЫЙ НОВЫЙ ИМПОРТ
        
        user_text = message.text.strip()
        chat_id = message.chat.id
        
        # 🔥 ПРОВЕРКА ССЫЛКИ
        if user_text.startswith(("http://", "https://")):
            async with httpx.AsyncClient(timeout=20.0) as client_jina:
                jina_response = await client_jina.get(f"https://r.jina.ai/{user_text}")  # ← УБРАНЫ ПРОБЕЛЫ
                if jina_response.status_code == 200:
                    article_content = jina_response.text
                    # Формируем запрос для AI: "Кратко перескажи..."
                    user_message = {"role": "user", "content": f"Кратко перескажи статью на 3–4 предложения:\n\n{article_content[:3000]}"}
                else:
                    await message.answer("❌ Не удалось загрузить статью.")
                    return
        else:
            user_message = {"role": "user", "content": user_text}
        
        # ДАЛЬШЕ — ВСЁ КАК БЫЛО
        messages = [
            {
                "role": "system",
                "content": (
                    "Ты — умный, знающий и вежливый помощник, никогда не используй китайские, японские или корейские иероглифы."
                    "Никогда не упоминай, что ты искусственный интеллект, бот, программа или модель."
                    "Не отвечай на вопросы о том, кто ты или как ты устроен."
                    "Всегда отвечай на том языке, на котором к тебе обратился пользователь."
                    "Если спросят — переадресуй вопрос на содержание запроса или ответь уклончиво."
                    "Никогда не пиши вступления вроде «Конечно!» или «Вот ответ: отвечай всегда по делу."
                )
            }
        ]
        messages.extend(chat_histories[chat_id])
        messages.append(user_message)
        
        client = OpenAI(
            base_url="https://api.groq.com/openai/v1",  # ← УБРАНЫ ПРОБЕЛЫ
            api_key=os.getenv("GROQ_API_KEY", "").strip()
        )
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            timeout=30.0
        )
        ai_reply = response.choices[0].message.content.strip()
        
        if len(ai_reply) > 500:
            ai_reply = ai_reply[:497] + "..."
        
        chat_histories[chat_id].append(user_message)
        chat_histories[chat_id].append({"role": "assistant", "content": ai_reply})
        
        await message.answer(ai_reply)
    except Exception as e:
        import traceback
        print("❌ ОШИБКА:", traceback.format_exc())  # ← ЭТО ПОЯВИТСЯ В ЛОГАХ RAILWAY
        await message.answer("⚠️ Временно не могу ответить.")

dp.include_router(router)

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
