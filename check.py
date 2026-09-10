"""Busca mensagens novas nas fontes, filtra por keyword e reenvia via bot.

Roda uma vez e termina (feito para cron no GitHub Actions). O último ID lido
de cada fonte fica em state.json, para a próxima execução continuar dali.
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from html import escape

import requests
from telethon import TelegramClient
from telethon.extensions import html
from telethon.sessions import StringSession

SOURCES = [-1001444838674, -1001283210985, -1001968825483]
KEYWORDS = ("teclado", "headset")
FIRST_RUN_LOOKBACK = timedelta(minutes=30)
MAX_MESSAGES_PER_RUN = 200
STATE_FILE = "state.json"
CAPTION_LIMIT = 1024

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("telethon").setLevel(logging.WARNING)
log = logging.getLogger("check")


class SendError(Exception):
    """Falha temporária (rede, 5xx, 403): tentar de novo na próxima execução."""


class BadRequest(Exception):
    """Bot API rejeitou a mensagem (400): tentar de novo não resolve."""


def load_dotenv(path=".env"):
    """Carrega .env para execução local; no Actions as variáveis vêm dos secrets."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def utf16_len(text):
    # Limites do Telegram são contados em unidades UTF-16 (emoji conta 2).
    return len(text.encode("utf-16-le")) // 2


def bot_call(method, data, files=None):
    # Nunca incluir a exceção original do requests no log: ela traz a URL, que contém o token.
    url = f"https://api.telegram.org/bot{os.environ['BOT_TOKEN']}/{method}"
    for _ in range(3):
        try:
            response = requests.post(url, data=data, files=files, timeout=60)
            body = response.json()
        except (requests.RequestException, ValueError):
            raise SendError(f"{method}: falha de rede ou resposta inválida") from None
        if body.get("ok"):
            return
        description = body.get("description", "")
        retry_after = body.get("parameters", {}).get("retry_after")
        if response.status_code == 429 and retry_after:
            time.sleep(retry_after)
            continue
        if response.status_code == 400:
            raise BadRequest(f"{method}: {description}")
        raise SendError(f"{method}: {response.status_code} {description}")
    raise SendError(f"{method}: rate limit persistente")


def post(method, field, html_text, plain_text, files=None):
    """Envia com HTML; se a Bot API não conseguir interpretar, reenvia como texto puro."""
    dest = os.environ["DEST_CHAT_ID"]
    try:
        bot_call(method, {"chat_id": dest, field: html_text, "parse_mode": "HTML"}, files)
    except BadRequest as e:
        if "parse" not in str(e).lower():
            raise
        bot_call(method, {"chat_id": dest, field: plain_text}, files)


def forward(html_text, plain_text, photo):
    if photo is None:
        post("sendMessage", "text", html_text, plain_text)
        return
    files = {"photo": ("promo.jpg", photo)}
    if utf16_len(plain_text) <= CAPTION_LIMIT:
        post("sendPhoto", "caption", html_text, plain_text, files)
    else:
        bot_call("sendPhoto", {"chat_id": os.environ["DEST_CHAT_ID"]}, files)
        post("sendMessage", "text", html_text, plain_text)


async def forward_message(entity, msg):
    text = msg.message or ""
    link = f"https://t.me/c/{entity.id}/{msg.id}"
    header_plain = f"📢 {entity.title}\n{link}\n\n"
    header_html = f"📢 <b>{escape(entity.title)}</b>\n{link}\n\n"
    photo = await msg.download_media(file=bytes) if msg.photo else None
    forward(header_html + html.unparse(text, msg.entities), header_plain + text, photo)


async def process_source(client, source, state):
    """Retorna False se algo falhou nesta fonte."""
    key = str(source)
    try:
        entity = await client.get_entity(source)
    except ValueError:
        log.error("Fonte %s não encontrada entre os chats da conta", source)
        return False

    if key in state:
        kwargs = {"offset_id": state[key]}
    else:
        kwargs = {"offset_date": datetime.now(timezone.utc) - FIRST_RUN_LOOKBACK}

    ok = True
    read = sent = 0
    try:
        # reverse=True: da mais antiga para a mais nova, só mensagens depois de offset_id/offset_date.
        async for msg in client.iter_messages(entity, reverse=True, limit=MAX_MESSAGES_PER_RUN, **kwargs):
            read += 1
            text = msg.message or ""
            if any(keyword in text.lower() for keyword in KEYWORDS):
                try:
                    await forward_message(entity, msg)
                    sent += 1
                except BadRequest as e:
                    log.error("%s msg %s descartada: %s", entity.title, msg.id, e)
                    ok = False
            state[key] = msg.id
            save_state(state)
    except SendError as e:
        log.error("%s: envio falhou, tenta de novo na próxima execução: %s", entity.title, e)
        ok = False

    log.info("%s: %d lidas, %d enviadas", entity.title, read, sent)
    return ok


async def main():
    load_dotenv()
    client = TelegramClient(
        StringSession(os.environ["TG_SESSION"]),
        int(os.environ["TG_API_ID"]),
        os.environ["TG_API_HASH"],
    )
    state = load_state()
    ok = True
    async with client:
        # StringSession não guarda cache de entidades; get_dialogs permite resolver os IDs das fontes.
        await client.get_dialogs()
        for source in SOURCES:
            ok = await process_source(client, source, state) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
