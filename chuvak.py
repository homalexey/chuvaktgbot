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
from openai import OpenAI

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
        url = f"https://ru.wiktionary.org/wiki/{urllib.parse.quote(term)}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=6)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            for tag in soup(['script', 'style', 'sup', '.mw-editsection', '.reference']):
                tag.decompose()
            meaning_heading = soup.find('span', {'id': 'Значение'})
            if meaning_heading:
                parent = meaning_heading.find_parent()
                if parent:
                    ol = parent.find_next(['ol', 'ul'])
                    if ol:
                        text = ol.get_text(' ', strip=True)
                        text = re.sub(r'\[\d+\]|\(.*?\)|\d+\.', '', text)
                        text = re.sub(r'\s+', ' ', text).strip()
                        if len(text) > 20:
                            return f"🔹 *Викисловарь*: {text[:800]}…"
            content = soup.find('div', {'class': 'mw-parser-output'})
            if content:
                p = content.find('p')
                if p:
                    text = p.get_text(' ', strip=True)
                    if len(text) > 30:
                        return f"🔹 *Викисловарь*: {text[:600]}…"
            return "🔹 *Викисловарь*: значение не найдено"
        else:
            return "🔹 *Викисловарь*: страница не существует"
    except Exception as e:
        logger.warning(f"Wiktionary error: {e}")
        return "🔹 *Викисловарь*: техническая ошибка"

def get_lurk(term: str) -> str:
    try:
        term_norm = term.strip().title().replace(' ', '_')
        encoded_term = urllib.parse.quote(term_norm)
        scraper = cloudscraper.create_scraper(
            browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
        )
        url = f"https://lurkmore.media/{encoded_term}"
        response = scraper.get(url, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            for tag in soup(['script', 'style', 'nav', 'header', 'footer', '.infobox', '.mw-editsection']):
                tag.decompose()
            content_div = soup.find('div', id='mw-content-text')
            if content_div:
                first_block = content_div.find(['p', 'div'])
                if first_block:
                    text = first_block.get_text(' ', strip=True)
                    text = re.sub(r'\[.*?\]|\(.*?\)|\b(?:править|редактировать)\b', '', text)
                    text = re.sub(r'\s+', ' ', text).strip()
                    if len(text) > 30 and "Loading" not in text and "Cloudflare" not in text:
                        return f"🔶 *Lurk.media*: {text[:900]}…"
            return "🔶 *Lurk.media*: статья есть, но нет описания"
        else:
            return "🔶 *Lurk.media*: страница не найдена"
    except Exception as e:
        logger.warning(f"Lurk.media error: {e}")
        return "🔶 *Lurk.media*: ошибка загрузки"

def get_gramota(term: str) -> str:
    try:
        url = f"https://gramota.ru/poisk?query={urllib.parse.quote(term)}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=6)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            result = soup.find('div', class_=re.compile(r'(card|result|search|entry)', re.IGNORECASE))
            if not result:
                result = soup.find(['p', 'div'], string=re.compile(r'.{10,}'))
            if result:
                text = result.get_text(' ', strip=True)
                if len(text) > 30 and not any(t in text for t in ["Подписка", "Реклама", "Слово дня", "©", "Грамота.ру"]):
                    return f"📘 *Грамота.ру*: {text[:800]}…"
            return "📘 *Грамота.ру*: не нашёл определения"
        else:
            return "📘 *Грамота.ру*: ошибка поиска"
    except Exception as e:
        logger.warning(f"Gramota error: {e}")
        return "📘 *Грамота.ру*: техническая ошибка"

def get_academic(term: str) -> str:
    try:
        url = f"https://dic.academic.ru/dic.nsf/ru/{urllib.parse.quote(term)}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=6)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            for tag in soup(['script', 'style', 'nav', 'footer', '.nav', '.footer']):
                tag.decompose()
            content = soup.find('div', class_=re.compile(r'(card|content|main)', re.I))
            if not content:
                content = soup
            definition = content.find(['dd', 'p', 'div'], string=re.compile(r'.{10,}'))
            if definition:
                text = definition.get_text(' ', strip=True)
                if len(text) > 25 and not any(t in text for t in ["См. также", "©", "Academic.ru", "Научно-технический", "Энциклопедический"]):
                    return f"📚 *Academic.ru*: {text[:800]}…"
            return "📚 *Academic.ru*: определение не найдено"
        else:
            return "📚 *Academic.ru*: страница не найдена"
    except Exception as e:
        logger.warning(f"Academic.ru error: {e}")
        return "📚 *Academic.ru*: техническая ошибка"

def get_urban(term: str) -> str:
    try:
        url = f"https://api.urbandictionary.com/v0/define?term={urllib.parse.quote(term)}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=6)
        if response.status_code == 200:
            data = response.json()
            if data.get('list'):
                definition = data['list'][0].get('definition', '').replace('\n', ' ').strip()
                example = data['list'][0].get('example', '').replace('\n', ' ').strip()
                if definition:
                    text = definition
                    if example and len(example) > 10:
                        text += f" Пример: {example}"
                    return f"🇺🇸 *Urban Dict*: {text[:800]}…"
            return "🇺🇸 *Urban Dict*: не найдено"
        else:
            return "🇺🇸 *Urban Dict*: ошибка API"
    except Exception as e:
        logger.warning(f"Urban error: {e}")
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

        pattern = re.compile(r'^(чувств?ак\s*,?\s+)(что\s+такое|кто\s+такой)\s+(.+)', re.IGNORECASE)
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
        loop.run_in_executor(None, get_wikipedia, term),
        loop.run_in_executor(None, get_wiktionary, term),
        loop.run_in_executor(None, get_lurk, term),
        loop.run_in_executor(None, get_gramota, term),
        loop.run_in_executor(None, get_academic, term),
        loop.run_in_executor(None, get_urban, term),
    ]

    results = await asyncio.gather(*tasks)

    clean_facts = []
    for res in results:
        if ":" in res:
            content = res.split(":", 1)[1].strip()
            if content and content not in ["…", "не найдено", "ошибка", "не найдена", "значение не найдено", "страница не найдена"]:
                clean_facts.append(res)

    if not clean_facts:
        final_text = "Чувак сегодня не в форме, но вот что нарыл:\n" + "\n".join(results)
    else:
        context = "\n".join(clean_facts)
        prompt = f'''
Ты — «Чувак», умный, ироничный, но точный друг. На основе данных ниже дай ответ на вопрос: «Что такое {term}?» или «Кто такой {term}?».

Правила:
- 2–3 предложения,
- на разговорном русском,
- без упоминания источников (не пиши «Википедия говорит...»),
- можно с лёгким сленгом (типо «шарит», «лол», «мем», «треш»),
- как будто объясняешь другу в чате.

Данные:
{context}
'''

        try:
            print(f"[GROQ] Запрашиваю ответ для: {term}")
            llm_response = await loop.run_in_executor(None, lambda: groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=250
            ))
            final_text = llm_response.choices[0].message.content.strip()
            print(f"[GROQ] Ответ: {final_text[:100]}...")
        except Exception as e:
            logger.error(f"Groq error: {e}")
            print(f"[GROQ ERROR] {e}")
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
