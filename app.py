import os
import asyncio
import traceback
from collections import defaultdict, deque
from aiohttp import web
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.webhook.aiohttp_server import SimpleRequestHandler


# === Конфигурация ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "final-secret").strip()
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "https://aismartzenbot-smartzenbot.up.railway.app").strip()
WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"
WEBHOOK_URL = f"{WEBHOOK_BASE_URL}{WEBHOOK_PATH}"

# === Инициализация бота ===
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
router = Router()

# === Хранилище истории диалогов (макс. 6 сообщений на чат) ===
chat_histories = defaultdict(lambda: deque(maxlen=6))


# === Вспомогательные функции ===
async def send_thinking_delayed(chat_id: int, bot: Bot):
    """Отправляет действие 'печатает...' через 2.5 секунды, если ответ ещё не пришёл."""
    await asyncio.sleep(2.5)
    await bot.send_chat_action(chat_id=chat_id, action="typing")


# === Обработчики сообщений ===
@router.message(Command("start"))
async def start(message: Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🧠 Что ты умеешь?")],
            [KeyboardButton(text="🧹 Очистить контекст")]
        ],
        resize_keyboard=True
    )
    await message.answer_photo(
        photo="https://github.com/aurora-irkutsk/AI_smartzenbot/raw/main/start.png",
        caption=(
            "🧠 Привет!\n\n"
            "Я Smart_Zen — ваш личный ассистент ❤️\n\n"
            "Отвечаю на вопросы, объясняю сложное простым языком, помогаю в учёбе и работе 🔥\n\n"
            "💡 Просто напишите свой запрос!\n\n"
            "Например: Что ты умеешь? 🤷‍♂️"
        ),
        reply_markup=kb
    )


@router.message(lambda msg: msg.text == "🧠 Что ты умеешь?")
async def help_button(message: Message):
    await message.answer(
        "🤖 <b>Я Smart_Zen — ваш умный ассистент</b>\n\n"
        "Вот что я могу:\n\n"
        "✅ Отвечать на <b>любые вопросы</b> — от науки до быта\n"
        "✅ Объяснять сложное <b>простым языком</b>\n"
        "✅ Помогать с <b>учёбой и работой</b>\n"
        "✅ Анализировать <b>ссылки на статьи</b> (просто скинь URL)\n"
        "✅ Вести <b>диалог с памятью</b> — можешь уточнять!\n\n"
        "💡 Просто напиши свой запрос — и я помогу!\n\n"
        "<i>Примеры:</i>\n"
        "• Объясни теорию относительности\n"
        "• Как решить уравнение x² + 5x + 6 = 0?\n"
        "• Перескажи статью по ссылке\n"
        "• Сделай конспект статьи про ИИ",
        parse_mode="HTML"
    )


@router.message(lambda msg: msg.text == "🧹 Очистить контекст")
async def clear_context(message: Message):
    chat_histories.pop(message.chat.id, None)
    await message.answer("🧠 Контекст очищен. О чём поговорим?")


@router.message()
async def handle_message(message: Message):
    if not message.text:
        return

    chat_id = message.chat.id
    user_text = message.text.strip()

    # Запуск индикатора "печатает..." с отменой при получении ответа или ошибке
    thinking_task = asyncio.create_task(send_thinking_delayed(chat_id, bot))

    try:
        # Обработка ссылок через Jina AI Reader
        if user_text.startswith(("http://", "https://")):
            import httpx
            async with httpx.AsyncClient(timeout=20.0) as client:
                jina_url = f"https://r.jina.ai/{user_text}"
                jina_response = await client.get(jina_url)
                if jina_response.status_code == 200:
                    content = jina_response.text
                    user_message = {
                        "role": "user",
                        "content": f"Кратко перескажи статью на 3–4 предложения:\n\n{content[:3000]}"
                    }
                else:
                    thinking_task.cancel()
                    await message.answer("❌ Не удалось загрузить статью.")
                    return
        else:
            user_message = {"role": "user", "content": user_text}

        # Формирование контекста
        messages = [
            {
                "role": "system",
                "content": (
                    "Ты — умный, знающий и вежливый помощник, никогда не используй китайские, японские или корейские иероглифы. "
                    "Никогда не упоминай, что ты искусственный интеллект, бот, программа или модель. "
                    "Не отвечай на вопросы о том, кто ты или как ты устроен. "
                    "Всегда отвечай на том языке, на котором к тебе обратился пользователь. "
                    "Если спросят — переадресуй вопрос на содержание запроса или ответь уклончиво. "
                    "Никогда не пиши вступления вроде «Конечно!» или «Вот ответ»: отвечай всегда по делу. "
                    "Если в запросе пользователя есть опечатки, орфографические или грамматические ошибки — "
                    "исправь их мысленно и отвечай на правильный вопрос."
                )
            }
        ]
        messages.extend(chat_histories[chat_id])
        messages.append(user_message)

        # Вызов модели через Groq
        from openai import OpenAI
        client = OpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=GROQ_API_KEY
        )
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            timeout=15.0
        )
        ai_reply = response.choices[0].message.content.strip()

        if len(ai_reply) > 500:
            ai_reply = ai_reply[:497] + "..."

        # Отмена индикатора и отправка ответа
        thinking_task.cancel()
        
        # === УМНАЯ ОБРЕЗКА ИСТОРИИ (ТОЛЬКО ЭТО ДОБАВЛЕНО) ===
        total_length = sum(len(msg["content"]) for msg in chat_histories[chat_id])
        while total_length > 2000:
            removed = chat_histories[chat_id].popleft()
            total_length -= len(removed["content"])
        # =====================================================
        
        chat_histories[chat_id].append(user_message)
        chat_histories[chat_id].append({"role": "assistant", "content": ai_reply})
        await message.answer(ai_reply)

    except Exception as e:
        thinking_task.cancel()
        print("❌ ОШИБКА:", traceback.format_exc())
        await message.answer("⚠️ Временно не могу ответить.")


# === Регистрация роутера ===
dp.include_router(router)


# === Вебхук и запуск сервера ===
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
