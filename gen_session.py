"""Gera a StringSession do Telethon para a conta do usuário.

Rodar uma vez, localmente. A string impressa dá acesso total à conta:
guardar como senha e cadastrar no secret TG_SESSION do GitHub.
"""
import os
from getpass import getpass

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(os.environ.get("TG_API_ID") or input("api_id: "))
api_hash = os.environ.get("TG_API_HASH") or getpass("api_hash: ")

with TelegramClient(StringSession(), api_id, api_hash) as client:
    me = client.get_me()
    print(f"\nLogado como {me.first_name} (id {me.id}).")
    print("TG_SESSION (não compartilhe):\n")
    print(client.session.save())
