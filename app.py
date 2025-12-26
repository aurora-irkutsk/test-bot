import os
import asyncio
import re
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

# === Инициализация ===
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
router = Router()
chat_histories = defaultdict(lambda: deque(maxlen=6))


# === Вспомогательные функции ===
async def send_thinking_delayed(chat_id: int, bot: Bot):
    await asyncio.sleep(2.5)
    await bot.send_chat_action(chat_id=chat_id, action="typing")


# === Обработчики ===
@router.message(Command("start"))
async def start(message: Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🧠 Что ты умеешь?")],
            [KeyboardButton(text="🧹 Очистить контекст")]
        ],
        resize_keyboard=True
    )
    if message.chat.id in chat_histories:
        await message.answer("👋 С возвращением! Продолжим?", reply_markup=kb)
    else:
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


@router.message(lambda m: m.text == "🧠 Что ты умеешь?")
async def help_button(message: Message):
    await message.answer(
        "🤖 <b>Я Smart_Zen — ваш умный ассистент</b>\n\n"
        "✅ Отвечать на <b>любые вопросы</b>\n"
        "✅ Объяснять сложное <b>простым языком</b>\n"
        "✅ Помогать с <b>учёбой и работой</b>\n"
        "✅ Анализировать <b>ссылки на статьи</b>\n"
        "✅ Вести <b>диалог с памятью</b>\n\n"
        "💡 Просто напиши запрос — и я помогу!\n\n"
        "<i>Примеры:</i>\n"
        "• Объясни теорию относительности\n"
        "• Как решить x² + 5x + 6 = 0?\n"
        "• Перескажи статью по ссылке",
        parse_mode="HTML"
    )


@router.message(lambda m: m.text == "🧹 Очистить контекст")
async def clear_context(message: Message):
    chat_histories.pop(message.chat.id, None)
    await message.answer("🧠 Контекст очищен. О чём поговорим?")


@router.message()
async def handle_message(message: Message):
    if not message.text:
        return

    chat_id = message.chat.id
    user_text = message.text.strip()
    thinking_task = asyncio.create_task(send_thinking_delayed(chat_id, bot))

    try:
        # === Распознавание ссылок ===
        url_match = re.search(r'(https?://\S+|www\.\S+|\S+\.\S+/\S*)', user_text)
        if url_match:
            url = url_match.group(1)
            if url.startswith("www."):
                url = "https://" + url
            elif not url.startswith(("http://", "https://")):
                url = "https://" + url

            import httpx
            async with httpx.AsyncClient(timeout=20.0) as client:
                jina_response = await client.get(f"https://r.jina.ai/{url}")
                if jina_response.status_code != 200:
                    thinking_task.cancel()
                    await message.answer("❌ Не удалось загрузить статью.")
                    return
                content = jina_response.text
                user_message = {
                    "role": "user",
                    "content": f"Кратко перескажи статью на 3–4 предложения:\n\n{content[:3000]}"
                }
        else:
            user_message = {"role": "user", "content": user_text}

        # === Контекст ===
        messages = [{
            "role": "system",
            "content": (
                "Ты — умный, знающий и вежливый помощник. Не используй иероглифы. "
                "Не говори, что ты ИИ. Отвечай на языке пользователя. Игнорируй вопросы о себе. "
                "Не пиши вступления. Исправляй опечатки в уме. "
                "Если запрос — «ну?», «ещё», «и?» — продолжай или мягко уточни."
            )
        }]
        messages.extend(chat_histories[chat_id])
        messages.append(user_message)

        # === Вызов модели ===
        from openai import OpenAI
        client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=GROQ_API_KEY)
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            timeout=15.0
        )
        ai_reply = response.choices[0].message.content.strip()
        # Для простых вопросов — коротко
        # Для сложных («объясни», «напиши код») — длиннее
        if any(word in user_text.lower() for word in ["объясни", "опиши", "напиши код", "сделай", "как работает"]):
            max_length = 1200
        else:
            max_length = 800

        if len(ai_reply) > max_length:
            ai_reply = ai_reply[:max_length - 3] + "..."

        thinking_task.cancel()

        # === Умная обрезка истории по длине ===
        hist = chat_histories[chat_id]
        total_len = sum(len(m["content"]) for m in hist)
        while total_len > 2000:
            removed = hist.popleft()
            total_len -= len(removed["content"])
        hist.append(user_message)
        hist.append({"role": "assistant", "content": ai_reply})

        await message.answer(ai_reply)

    except Exception:
        thinking_task.cancel()
        print("❌ ОШИБКА:", traceback.format_exc())
        await message.answer("⚠️ Временно не могу ответить.")


# === Запуск ===
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
