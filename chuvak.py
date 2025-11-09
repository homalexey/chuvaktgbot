import re
import logging
import urllib.parse
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import wikipediaapi
import requests
from bs4 import BeautifulSoup
import asyncio
import cloudscraper
import os
import concurrent.futures
from openai import OpenAI
executor = concurrent.futures.ThreadPoolExecutor(max_workers=6)

# === НАСТРОЙКИ ===
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
GROQ_API_KEY = os.environ['GROQ_API_KEY']

# Инициализация Groq (совместим с OpenAI API)
groq_client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

wiki_wiki = wikipediaapi.Wikipedia(
    language='ru',
    extract_format=wikipediaapi.ExtractFormat.WIKI,
    user_agent='ChuvakSharitBot/1.0 (https://t.me/ChuvakSharitBot)'
)

# === ФУНКЦИИ ПОИСКА ===

def get_wikipedia(term: str) -> str:
    try:
        candidates = [term.strip(), term.strip().title(), term.strip().capitalize()]
        candidates = list(dict.fromkeys(candidates))
        for candidate in candidates:
            page = wiki_wiki.page(candidate)
            if page.exists():
                if "может означать" in page.summary or "значения" in page.summary[:100]:
                    continue
                summary = page.summary[:900]
                return f"🔸 *Википедия*: {summary}…"
        return "🔸 *Википедия*: не найдено"
    except Exception as e:
        logger.error(f"Wikipedia error: {e}")
        return "🔸 *Википедия*: ошибка"

def get_wiktionary(term: str) -> str:
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        variants = [term, term.lower(), term.capitalize(), term.upper()]
        for cand in variants:
            url = f"https://ru.wiktionary.org/wiki/{urllib.parse.quote(cand)}"
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                content = soup.find("div", class_="mw-parser-output")
                if content:
                    p = content.find("p")
                    if p and len(p.get_text(strip=True)) > 30:
                        return f"🔹 *Викисловарь*: {p.get_text(' ', strip=True)[:600]}…"
            elif resp.status_code == 404 and cand.isupper():
                # Попробуем англ. версию
                url_en = f"https://en.wiktionary.org/wiki/{urllib.parse.quote(cand)}"
                resp = requests.get(url_en, headers=headers, timeout=10)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    p = soup.find("p")
                    if p and len(p.get_text(strip=True)) > 30:
                        return f"🔹 *Wiktionary (EN)*: {p.get_text(' ', strip=True)[:600]}…"
        return "🔹 *Викисловарь*: не найдено"
    except Exception as e:
        logger.warning(f"Wiktionary error: {e}")
        return "🔹 *Викисловарь*: техническая ошибка"


def get_lurk(term: str) -> str:
    """Парсит статью с Lurkmore.media (через cloudscraper)."""
    try:
        term_norm = term.strip().replace(" ", "_").capitalize()
        url = f"https://lurkmore.media/{urllib.parse.quote(term_norm)}"

        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        scraper.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
            "Referer": "https://lurkmore.media/"
        })

        response = scraper.get(url, timeout=12)

        if response.status_code != 200:
            logger.info(f"Lurk.media {term} HTTP {response.status_code}")
            return "🔶 *Lurk.media*: страница не найдена"

        soup = BeautifulSoup(response.text, "html.parser")
        # удалить мусор
        for tag in soup(["script", "style", "nav", "header", "footer", "table", "sup"]):
            tag.decompose()

        content_div = soup.find("div", id="mw-content-text")
        if not content_div:
            return "🔶 *Lurk.media*: статья есть, но контент не найден"

        # ищем первый параграф с нормальным текстом
        p = content_div.find(["p", "div"], string=re.compile(r".{30,}"))
        if not p:
            return "🔶 *Lurk.media*: статья есть, но нет описания"

        text = p.get_text(" ", strip=True)
        text = re.sub(r"\[.*?\]|\(.*?\)", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text or len(text) < 30:
            return "🔶 *Lurk.media*: статья пустая"

        return f"🔶 *Lurk.media*: {text[:900]}…"
    except Exception as e:
        logger.warning(f"Lurk.media error for {term}: {e}")
        return "🔶 *Lurk.media*: ошибка загрузки"

def get_gramota(term: str) -> str:
    """Парсит результаты с gramota.ru."""
    try:
        url = f"https://gramota.ru/poisk?query={urllib.parse.quote(term)}"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code != 200:
            logger.info(f"Gramota.ru {term} HTTP {resp.status_code}")
            return "📘 *Грамота.ру*: ошибка поиска"

        soup = BeautifulSoup(resp.text, "html.parser")
        # возможные контейнеры
        result = soup.find("div", class_=re.compile(r"(card|result|entry|content)", re.I))
        if not result:
            result = soup.find("p", string=re.compile(r".{15,}"))

        if result:
            text = result.get_text(" ", strip=True)
            # фильтрация мусора
            if any(bad in text for bad in ["©", "Реклама", "Подписка", "Грамота.ру"]):
                logger.debug(f"Gramota skipped noise for {term}")
                return "📘 *Грамота.ру*: не нашёл определения"
            return f"📘 *Грамота.ру*: {text[:800]}…"

        return "📘 *Грамота.ру*: не нашёл определения"
    except Exception as e:
        logger.warning(f"Gramota error for {term}: {e}")
        return "📘 *Грамота.ру*: техническая ошибка"

def get_academic(term: str) -> str:
    """Парсит dic.academic.ru."""
    try:
        url = f"https://dic.academic.ru/dic.nsf/ru/{urllib.parse.quote(term)}"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code != 200:
            logger.info(f"Academic.ru {term} HTTP {resp.status_code}")
            return "📚 *Academic.ru*: страница не найдена"

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "footer", "nav", "sup"]):
            tag.decompose()

        # возможные блоки контента
        content = soup.find("div", class_=re.compile(r"(content|card|main|entry)", re.I)) or soup
        # ищем первый параграф/описание
        definition = content.find(["dd", "p", "div"], string=re.compile(r".{20,}"))
        if not definition:
            return "📚 *Academic.ru*: определение не найдено"

        text = definition.get_text(" ", strip=True)
        if len(text) < 25:
            return "📚 *Academic.ru*: определение слишком короткое"
        if any(bad in text for bad in ["©", "См. также", "Academic.ru"]):
            return "📚 *Academic.ru*: не содержит определения"

        return f"📚 *Academic.ru*: {text[:900]}…"
    except Exception as e:
        logger.warning(f"Academic error for {term}: {e}")
        return "📚 *Academic.ru*: техническая ошибка"

def get_urban(term: str) -> str:
    """Ищет определение в Urban Dictionary (англ.)."""
    try:
        url = f"https://api.urbandictionary.com/v0/define?term={urllib.parse.quote(term)}"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=12)
        if response.status_code != 200:
            logger.warning(f"UrbanDict HTTP {response.status_code} for {term}")
            return "🇺🇸 *Urban Dict*: ошибка API"

        data = response.json()
        if not data.get("list"):
            return "🇺🇸 *Urban Dict*: не найдено"

        # берём самое длинное определение
        best = max(data["list"], key=lambda d: len(d.get("definition", "")))
        definition = best.get("definition", "").replace("\n", " ").strip()
        example = best.get("example", "").replace("\n", " ").strip()
        text = definition
        if example and len(example) > 10:
            text += f" Пример: {example}"
        return f"🇺🇸 *Urban Dict*: {text[:900]}…"
    except Exception as e:
        logger.warning(f"UrbanDict error for {term}: {e}")
        return "🇺🇸 *Urban Dict*: техническая ошибка"

# === ОБРАБОТЧИК СООБЩЕНИЙ ===

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Защита: если нет сообщения или нет текста — выходим
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    chat_type = update.message.chat.type

    # В личке — любой короткий запрос
    if chat_type == 'private':
        clean_text = re.sub(r'[^\w\s]', '', text).strip()
        if clean_text and len(clean_text.split()) <= 5:
            await process_query(update, clean_text)
        return

    # В группе — упоминание ИЛИ фраза с "Чувак"
    if chat_type != 'private':
        if update.message.text.startswith(f"@{context.bot.username}"):
            text = update.message.text[len(f"@{context.bot.username}"):].strip()
            if text:
                await process_query(update, text)
            return

        pattern = re.compile(r'^(чувак\s*,?\s+)(что\s+такое|кто\s+такой)\s+(.+)', re.IGNORECASE)
        match = pattern.search(text)
        if match:
            term = match.group(3).strip(' ?.')
            if term:
                await process_query(update, term)
        return

# === СИНТЕЗ ОТВЕТА ЧЕРЕЗ GROQ (LLAMA 3.1) ===

async def process_query(update: Update, term: str):
    await update.message.chat.send_action(action="typing")

    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(executor, get_wikipedia, term),
        loop.run_in_executor(executor, get_wiktionary, term),
        loop.run_in_executor(executor, get_lurk, term),
        loop.run_in_executor(executor, get_gramota, term),
        loop.run_in_executor(executor, get_academic, term),
        loop.run_in_executor(executor, get_urban, term),
    ]

    # важно: не падаем при исключениях отдельных задач
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Логируем подробно — это поможет дебагу в Render logs
    for i, res in enumerate(results):
        source = ["wikipedia","wiktionary","lurk","gramota","academic","urban"][i]
        if isinstance(res, Exception):
            logger.warning(f"[{term}] source={source} -> EXCEPTION: {res}")
        else:
            logger.info(f"[{term}] source={source} -> {len(str(res))} chars, preview: {str(res)[:120]!r}")

    # Обрабатываем результаты — если элемент exception, превращаем в текст-ошибку
    normalized = []
    for res in results:
        if isinstance(res, Exception):
            normalized.append("ошибка")
        else:
            normalized.append(res)

    clean_facts = []
    for res in normalized:
        if isinstance(res, str) and ":" in res:
            content = res.split(":", 1)[1].strip()
            if content and content.lower() not in ["…", "не найдено", "ошибка", "не найдена", "значение не найдено", "страница не найдена"]:
                clean_facts.append(res)

    if not clean_facts:
        # покажем и что именно вернулось (полезно в логах)
        final_text = "Чувак сегодня не в форме, но вот что нарыл:\n" + "\n".join(normalized)
    else:
        context = "\n".join(clean_facts)
        prompt = f'''
Ты — «Чувак», умный, ироничный, но точный друг. На основе данных ниже дай ответ на вопрос: «Что такое {term}?» или «Кто такой {term}?».

Правила:
- 2–3 предложения,
- на разговорном русском,
- без упоминания источников (не пиши «Википедия говорит...»),
- можно с лёгким сленгом (типо «шарит», «лол», «мем», «треш», «рофл», «хайп», «кринж»),
- как будто объясняешь другу в чате.

Данные:
{context}
'''
        try:
            logger.info(f"[GROQ] Запрашиваю ответ для: {term}")
            # Groq call (как у тебя было)
            llm_response = await loop.run_in_executor(None, lambda: groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=250
            ))
            final_text = llm_response.choices[0].message.content.strip()
            logger.info(f"[GROQ] Ответ: {final_text[:100]}...")
        except Exception as e:
            logger.error(f"Groq error: {e}")
            final_text = "Чувак шарит, но сегодня лень объяснять. Вот что нашёл:\n" + "\n".join(clean_facts)

    response = f'🔍 *{term.capitalize()}*\n\n{final_text}\n\n— Обращайся, чувак'
    if len(response) > 4000:
        response = response[:3990] + '…'

    await update.message.reply_text(response, parse_mode="Markdown", disable_web_page_preview=True)

# === ЗАПУСК ===

import os

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    PORT = int(os.environ.get('PORT', 8000))
    WEBHOOK_URL = os.environ.get('WEBHOOK_URL')

    if not WEBHOOK_URL:
        raise ValueError("WEBHOOK_URL не задан. Укажите его в Environment Variables на Render.")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}"
    )

if __name__ == '__main__':
    main()

