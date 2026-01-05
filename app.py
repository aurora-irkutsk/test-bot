import os
import re
import json
import logging
import uuid
from time import time
from collections import defaultdict, deque
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, ChatMemberHandler, MessageHandler,
    CommandHandler, CallbackQueryHandler, ContextTypes, filters
)
from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest
import httpx
import feedparser
from dotenv import load_dotenv

# === Настройки ===
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
NEWS_CHANNEL_ID = os.getenv("NEWS_CHANNEL_ID")
NEWS_SOURCE = "https://civil.ge/ru/feed/"

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не задан!")

ALLOWED_CHANNELS = {"georgia_rabota", "georgia_gamarjoba"}

STOP_WORDS = [
    "оплата за обучение", "гарантируем работу", "заработок в telegram", "кредит без проверки",
    "присоединяйся к каналу", "тысячи в день", "без вложений", "удалённый заработок",
    "секретный метод", "инвестируй сейчас", "казино", "ставки", "выиграй",
    "розыгрыш", "подарок", "тестовый период", "чат с иностранцами",
    "знакомства с богатыми", "интим", "секс чат"
]
STOP_PATTERNS = [re.compile(rf'\b{re.escape(w)}\b', re.IGNORECASE) for w in STOP_WORDS]

PROFANITY_WORDS = {
    "блядь", "ебать", "хуй", "пизда", "сука", "нахуй", "пидр", "пидор", "педик", "лох", "мудак",
    "урод", "шлюха", "проститутка", "гомик", "чмо", "тварь", "сволочь", "идиот", "дебил",
    "убью", "изнасилую", "отсоси", "трахни", "в жопу", "мразь", "гад", "жид", "ниггер",
    "хохол", "кацап", "чурка", "грузня", "армяшка", "косоглазый", "цыган", "петух", "падлы", "залупа", "дно"
}

CHAR_MAP = str.maketrans({
    'a': 'а', 'e': 'е', 'o': 'о', 'p': 'р', 'c': 'с',
    'x': 'х', 'y': 'у', 'k': 'к', 'm': 'м', 't': 'т', 'b': 'в',
    '@': 'а', '0': 'о', '3': 'з', '4': 'ч'
})

WELCOME_MESSAGE = """👋 Добро пожаловать в чат «Старт в Грузии»!

Здесь мы помогаем друг другу с:
• Переездом и регистрацией
• Поиском жилья и работы
• Банками, транспортом, документами

📌 Важно:
— Вакансии публикуются ТОЛЬКО в канале: @georgia_rabota
— Запрещена реклама, спам, ненормативная лексика и оффтоп

Спасибо, что вы с нами! 💙"""

HELP_MESSAGE = """🤖 Я — помощник и модератор чата «Старт в Грузии».

Я автоматически удаляю:
• Рекламу и спам
• Ссылки на посторонние каналы
• Оскорбления и ненормативную лексику

Также могу отвечать на вопросы!
Пример: `/ask Как получить ВНЖ в Грузии?`

Вакансии — только в @georgia_rabota. Соблюдайте правила! 💙"""

# === Глобальные данные ===
WARNINGS_FILE = "warnings.json"
LAST_NEWS_FILE = "last_news.txt"
LAST_RATE_FILE = "last_rate.txt"
user_warnings = {}
user_messages = defaultdict(lambda: deque(maxlen=10))
STATS = {"total_deleted": 0, "total_kicks": 0, "start_time": time()}

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


# === Файловые операции ===
def load_warnings():
    if os.path.exists(WARNINGS_FILE):
        try:
            with open(WARNINGS_FILE, "r", encoding="utf-8") as f:
                return {int(k): v for k, v in json.load(f).items()}
        except Exception as e:
            logger.error(f"Ошибка загрузки предупреждений: {e}")
    return {}

def save_warnings():
    try:
        with open(WARNINGS_FILE, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in user_warnings.items()}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")

def get_last_news_id():
    if os.path.exists(LAST_NEWS_FILE):
        with open(LAST_NEWS_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""

def save_last_news_id(news_id):
    with open(LAST_NEWS_FILE, "w", encoding="utf-8") as f:
        f.write(news_id)

def get_last_rate_id():
    if os.path.exists(LAST_RATE_FILE):
        with open(LAST_RATE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""

def save_last_rate_id(rate_id):
    with open(LAST_RATE_FILE, "w", encoding="utf-8") as f:
        f.write(rate_id)


# === Groq API ===
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

async def ask_groq(query: str) -> str:
    if not GROQ_API_KEY:
        return "❌ Groq API не настроен."
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{
            "role": "system",
            "content": (
                "Ты — умный, дружелюбный и полезный ассистент. Отвечай на русском языке. "
                "Давай полные, чёткие и структурированные ответы. Не используй markdown или звёздочки. "
                "Если не знаешь ответа — честно скажи, но предложи полезную информацию. "
                "Избегай вымысла. Пиши вежливо и по-человечески."
            )
        }, {"role": "user", "content": query}],
        "temperature": 0.7,
        "max_tokens": 1500
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(GROQ_API_URL, headers=headers, json=payload)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Ошибка Groq: {e}")
        return "⚠️ Не могу сейчас ответить. Попробуйте позже."


# === Модерация ===
def _normalize(text: str) -> str:
    return re.sub(r'[^а-я\s]', ' ', text.lower().translate(CHAR_MAP))

def contains_profanity(text: str) -> bool:
    return any(w in _normalize(text) for w in PROFANITY_WORDS)

def contains_stop_words(text: str) -> bool:
    return any(p.search(text) for p in STOP_PATTERNS)

def contains_disallowed_links(text: str) -> bool:
    if "t.me" not in text.lower() and "@" not in text:
        return False
    refs = set(re.findall(r't\.me/([a-zA-Z0-9_]+)', text, re.IGNORECASE) + re.findall(r'@([a-zA-Z0-9_]+)', text))
    return not any(ch in refs for ch in ALLOWED_CHANNELS)

def is_violation(text: str) -> bool:
    return contains_stop_words(text) or contains_disallowed_links(text) or contains_profanity(text)

def get_text_or_caption(msg) -> str:
    return msg.text or msg.caption or ""


# === Обработчики сообщений ===
async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    my_chat_member = update.my_chat_member
    if not my_chat_member or my_chat_member.new_chat_member.user.id == context.bot.id:
        return
    new_status = my_chat_member.new_chat_member.status
    old_status = getattr(my_chat_member.old_chat_member, "status", None)
    if new_status == ChatMemberStatus.MEMBER and old_status in (
        None, ChatMemberStatus.LEFT, ChatMemberStatus.KICKED, ChatMemberStatus.RESTRICTED
    ):
        try:
            await context.bot.send_message(chat_id=my_chat_member.chat.id, text=WELCOME_MESSAGE)
            logger.info(f"✅ Приветствие отправлено: {my_chat_member.new_chat_member.user.id}")
        except Exception as e:
            logger.error(f"❌ Ошибка приветствия: {e}")

async def moderate_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or msg.from_user.id == context.bot.id:
        return
    user_id = msg.from_user.id
    chat = msg.chat
    now = time()
    user_messages[user_id].append(now)
    while user_messages[user_id] and now - user_messages[user_id][0] > 10:
        user_messages[user_id].popleft()
    if len(user_messages[user_id]) >= 3:
        try:
            await chat.ban_member(user_id)
            await chat.unban_member(user_id)
            mention = f"@{msg.from_user.username}" if msg.from_user.username else "спамер"
            await context.bot.send_message(chat.id, f"⛔ {mention} — флуд запрещён!")
            logger.info(f"Флуд-кик: {user_id}")
            return
        except Exception as e:
            logger.error(f"Ошибка флуд-кика: {e}")
            return
    text = get_text_or_caption(msg)
    if not text.strip() or not is_violation(text):
        return
    try:
        await msg.delete()
        logger.info(f"🧹 Удалено сообщение от {user_id}")
        STATS["total_deleted"] += 1
    except Exception as e:
        logger.error(f"Ошибка удаления: {e}")
    if user_id not in user_warnings:
        user_warnings[user_id] = 1
        save_warnings()
        warning = (
            "⚠️ Ваше сообщение нарушает правила чата:\n"
            "• Запрещена реклама, спам, оскорбления и ссылки на посторонние каналы.\n"
            "При повторном нарушении вы будете удалены."
        )
        try:
            await context.bot.send_message(chat_id=user_id, text=warning)
            logger.info(f"📩 Личное предупреждение: {user_id}")
        except Exception:
            try:
                mention = f"@{msg.from_user.username}" if msg.from_user.username else "пользователь"
                await context.bot.send_message(chat.id, f"⚠️ {mention}, ваше сообщение нарушает правила чата.")
                logger.info(f"📢 Публичное предупреждение: {user_id}")
            except Exception as e2:
                logger.error(f"Не удалось отправить предупреждение: {e2}")
        return
    try:
        await chat.ban_member(user_id)
        await chat.unban_member(user_id)
        user_warnings.pop(user_id, None)
        save_warnings()
        STATS["total_kicks"] += 1
        logger.info(f"⛔️ Кик за повторное нарушение: {user_id}")
    except BadRequest as e:
        if "user is an administrator" not in str(e).lower():
            logger.error(f"Ошибка кика: {e}")
    except Exception as e:
        logger.error(f"Неожиданная ошибка при кике: {e}")


# === Команды ===
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_CHAT_ID or str(update.effective_user.id) != ADMIN_CHAT_ID:
        return
    active = sum(1 for dq in user_messages.values() if dq)
    uptime = int(time() - STATS["start_time"])
    h, r = divmod(uptime, 3600)
    m, s = divmod(r, 60)
    stats_text = (
        "📊 **Статистика бота**\n"
        f"• Предупреждений: {len(user_warnings)}\n"
        f"• Активных в флуд-контроле: {active}\n"
        f"• Удалено сообщений: {STATS['total_deleted']}\n"
        f"• Киков за нарушения: {STATS['total_kicks']}\n"
        f"• Время работы: {h}ч {m}м {s}с"
    )
    await update.message.reply_text(stats_text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_CHAT_ID or str(update.effective_user.id) != ADMIN_CHAT_ID:
        return
    await update.message.reply_text(HELP_MESSAGE)

async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not GROQ_API_KEY:
        await update.message.reply_text("❌ /ask недоступен.")
        return
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("❓ Пример: `/ask Как открыть счёт в банке?`", parse_mode="Markdown")
        return
    if len(query) > 300:
        await update.message.reply_text("❌ Макс. 300 символов.")
        return
    logger.info(f"🧠 Запрос: {query}")
    thinking = await update.message.reply_text("🤔 Думаю...")
    try:
        answer = await ask_groq(query)
        await thinking.edit_text(answer)
    except Exception:
        logger.exception("/ask ошибка")
        await thinking.edit_text("⚠️ Не удалось сформировать ответ.")


# === Новости ===
NEWS_SOURCE = "https://civil.ge/ru/feed/"

async def summarize_news(title: str, summary: str) -> str:
    prompt = (
        f"Кратко перескажи эту новость на русском языке в 2–3 предложениях. "
        f"Будь нейтральным, точным и полезным для людей, живущих в Грузии.\n\n"
        f"Заголовок: {title}\n\nТекст: {summary}"
    )
    return await ask_groq(prompt)

async def check_and_send_news(context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_CHAT_ID or not NEWS_CHANNEL_ID:
        return
    try:
        feed = feedparser.parse(NEWS_SOURCE)
        if not feed.entries:
            return
        latest = feed.entries[0]
        news_id = latest.get("id", latest.get("link", ""))
        if news_id == get_last_news_id():
            return
        title = latest.get("title", "Новость")
        summary = latest.get("summary", "")
        link = latest.get("link", "")
        post_text = await summarize_news(title, summary) + f"\n\n🔗 {link}"
        news_uuid = str(uuid.uuid4())
        context.bot_data[news_uuid] = post_text
        keyboard = [
            [InlineKeyboardButton("✅ Опубликовать", callback_data=f"publish_news_{news_uuid}")],
            [InlineKeyboardButton("❌ Отклонить", callback_data="reject_news")]
        ]
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"🗞 Новая новость:\n\n{post_text}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        save_last_news_id(news_id)
        logger.info(f"📰 Новость отправлена: {title}")
    except Exception as e:
        logger.error(f"Ошибка новостей: {e}")


# === Курсы валют ===
async def fetch_exchange_rates():
    """Получает актуальные курсы USD, EUR, RUB от НБ Грузии"""
    url = "https://nbg.gov.ge/gw/api/ct/monetarypolicy/currencies/ka/json"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
            
            if not isinstance(data, list):
                logger.error(f"НБ Грузии: ожидается список, получено: {type(data)}")
                return None

            rates = {"USD": None, "EUR": None, "RUB": None}
            for item in data:
                code = str(item.get("code", "")).strip().upper()
                rate_val = item.get("rate")
                if code in rates and rate_val is not None:
                    try:
                        rates[code] = float(rate_val)
                    except (TypeError, ValueError) as e:
                        logger.warning(f"НБ Грузии: не удалось преобразовать курс {code}={rate_val} → {e}")
                        continue

            if all(v is not None for v in rates.values()):
                return rates
            else:
                logger.info(f"НБ Грузии: не все валюты доступны сегодня. Получено: {rates}")
                return None

    except Exception as e:
        logger.error(f"Ошибка запроса к НБ Грузии: {e}")
        return None

async def check_and_send_rates(context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_CHAT_ID or not NEWS_CHANNEL_ID:
        return
    rates = await fetch_exchange_rates()
    if not rates:
        logger.info("💱 Курсы не получены — пропускаем публикацию.")
        return
    rate_id = f"{rates['USD']}_{rates['EUR']}_{rates['RUB']}"
    if rate_id == get_last_rate_id():
        logger.info("💱 Курсы не изменились — пропускаем.")
        return
    post_text = (
        "💱 **Официальные курсы НБ Грузии**\n\n"
        f"• 1 USD = {rates['USD']:.4f} GEL\n"
        f"• 1 EUR = {rates['EUR']:.4f} GEL\n"
        f"• 1 RUB = {rates['RUB']:.4f} GEL\n\n"
        "Данные обновлены сегодня."
    )
    rate_uuid = str(uuid.uuid4())
    context.bot_data[rate_uuid] = post_text
    keyboard = [
        [InlineKeyboardButton("✅ Опубликовать", callback_data=f"publish_rate_{rate_uuid}")],
        [InlineKeyboardButton("❌ Отклонить", callback_data="reject_rate")]
    ]
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=post_text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    save_last_rate_id(rate_id)
    logger.info("💱 Курсы отправлены на модерацию.")


# === Callback-обработчики ===
async def handle_news_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "reject_news":
        await query.edit_message_text("❌ Новость отклонена.")
    elif query.data.startswith("publish_news_"):
        news_uuid = query.data.split("_", 2)[2]
        post_text = context.bot_data.pop(news_uuid, None)
        if not post_text:
            await query.edit_message_text("⚠️ Данные устарели.")
            return
        try:
            await context.bot.send_message(chat_id=NEWS_CHANNEL_ID, text=post_text)
            await query.edit_message_text("✅ Новость опубликована!")
            logger.info("📰 Опубликована.")
        except Exception as e:
            await query.edit_message_text(f"⚠️ Ошибка: {str(e)[:100]}")

async def handle_rate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "reject_rate":
        await query.edit_message_text("❌ Курсы отклонены.")
    elif query.data.startswith("publish_rate_"):
        rate_uuid = query.data.split("_", 2)[2]
        post_text = context.bot_data.pop(rate_uuid, None)
        if not post_text:
            await query.edit_message_text("⚠️ Данные устарели.")
            return
        try:
            await context.bot.send_message(chat_id=NEWS_CHANNEL_ID, text=post_text)
            await query.edit_message_text("✅ Курсы опубликованы!")
            logger.info("💱 Опубликованы.")
        except Exception as e:
            await query.edit_message_text(f"⚠️ Ошибка: {str(e)[:100]}")


# === Запуск ===
def main():
    global user_warnings
    user_warnings = load_warnings()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(ChatMemberHandler(welcome_new_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(
        MessageHandler(
            (~filters.COMMAND) & (
                filters.TEXT | filters.PHOTO | filters.VIDEO |
                filters.ANIMATION | filters.Document.ALL
            ),
            moderate_message
        ),
        group=1
    )
    app.add_handler(CommandHandler("ask", ask_command))
    app.add_handler(CallbackQueryHandler(handle_news_callback))
    app.add_handler(CallbackQueryHandler(handle_rate_callback))
    if ADMIN_CHAT_ID:
        app.add_handler(CommandHandler("start", help_command))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("stats", stats_command))
    if ADMIN_CHAT_ID and NEWS_CHANNEL_ID:
        jq = app.job_queue
        jq.run_repeating(check_and_send_news, interval=4 * 3600, first=10)
        jq.run_repeating(check_and_send_rates, interval=900, first=30)  # каждые 15 мин
    logger.info("🚀 Бот запущен! Модерация + AI + новости + курсы.")
    app.run_polling()

if __name__ == "__main__":
    main()
