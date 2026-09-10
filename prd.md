# PRD — send-promo

> Ver `contexto.md` para histórico das decisões.

## 1. Objetivo

Script executado periodicamente pelo GitHub Actions que verifica 3 grupos/canais de promoção no Telegram e, quando uma mensagem nova mencionar **teclado** ou **headset**, reenvia a promoção (foto + texto + grupo de origem) para o grupo pessoal do usuário via bot, gerando notificação push.

## 2. Escopo

### Dentro

- Execução agendada (cron) no GitHub Actions, repositório público.
- Buscar mensagens novas das 3 fontes desde a última execução.
- Filtrar por keywords hardcoded.
- Reenviar via bot para o grupo pessoal: foto original, texto original e cabeçalho com nome da fonte e link para a mensagem original.
- Persistir o último ID lido por fonte entre execuções.
- Manter o workflow ativo (keepalive contra desativação por inatividade).

### Fora (não fazer)

- Tempo real / processo 24h.
- API HTTP, interface web, painel.
- Banco de dados.
- Deduplicação entre grupos.
- Filtro por preço, marca ou lista de exclusão.
- Email ou outros canais.
- Mensagens editadas (só mensagens novas).
- Tratamento especial de álbuns (várias fotos): cada mensagem do álbum é avaliada isoladamente.
- Múltiplos usuários / configuração dinâmica.

## 3. Requisitos funcionais

| ID | Requisito |
|----|-----------|
| RF1 | Ler apenas os chats `-1001444838674`, `-1001283210985`, `-1001968825483` (hardcoded). |
| RF2 | Processar mensagens com ID maior que o último ID salvo para aquela fonte, da mais antiga para a mais nova. |
| RF3 | Sem estado salvo para uma fonte (primeira execução ou cache expirado): processar só mensagens dos últimos **30 min**, para não disparar promoções antigas. |
| RF4 | Match quando o texto/legenda contém `teclado` ou `headset`, **case-insensitive, por substring** (pega também "Teclados", "HEADSET", "headsets"). |
| RF5 | Mensagem com foto: bot envia a foto (`sendPhoto`) com legenda = cabeçalho + texto original. |
| RF6 | Mensagem sem foto: bot envia só texto (`sendMessage`) = cabeçalho + texto original. |
| RF7 | Cabeçalho: `📢 <nome da fonte>` + link para a mensagem original (`https://t.me/c/<id sem -100>/<msg_id>`). |
| RF8 | Preservar links/formatação do texto original (converter entidades para HTML e enviar com `parse_mode=HTML`). Se a Bot API rejeitar o HTML, reenviar como texto puro. |
| RF9 | Legenda acima de 1024 caracteres: enviar foto sem legenda e depois o texto em mensagem separada. |
| RF10 | Enviar todas as ocorrências, sem deduplicação entre fontes. |
| RF11 | Mensagens sem texto/legenda são ignoradas (mas o ID avança). |
| RF12 | Atualizar o último ID da fonte **após cada mensagem processada**. Falha temporária no envio (rede, 5xx, 403, rate limit): parar aquela fonte sem avançar o ID (será tentada de novo na próxima execução). Rejeição permanente da Bot API (HTTP 400, exceto erro de parse HTML que tem fallback): logar, descartar a mensagem e avançar, para não travar a fonte. Em qualquer falha, a execução termina com código ≠ 0. |

## 4. Requisitos não funcionais

| ID | Requisito |
|----|-----------|
| RNF1 | Latência aceita: intervalo do cron (5 min) + atraso do GitHub (tipicamente até ~20 min). |
| RNF2 | Execuções não se sobrepõem (`concurrency` no workflow). |
| RNF3 | Cada execução termina em < 2 min (`timeout-minutes: 5`). |
| RNF4 | Credenciais apenas em GitHub Secrets; nunca no repositório nem nos logs (logs são públicos). |
| RNF5 | Logs mínimos: fonte, quantidade de mensagens lidas, quantidade de matches, erros. Sem imprimir texto completo nem segredos. |
| RNF6 | Custo zero (repo público → minutos de Actions ilimitados). |

## 5. Arquitetura

```
GitHub Actions (cron */5)
  │
  ├─ restaura state.json do cache
  │
  ├─ check.py
  │    ├─ Telethon (conta do usuário, StringSession) ──MTProto──> 3 fontes
  │    │     iter_messages(min_id=último ID)
  │    ├─ filtro: "teclado" | "headset" in texto.lower()
  │    ├─ download da foto (bytes)
  │    └─ Bot API HTTP (requests) ──> sendPhoto / sendMessage ──> grupo pessoal
  │
  └─ salva state.json no cache
                                                   │
                                                   ▼
                                     notificação push no celular
```

- **Leitura**: Telethon com a conta do usuário (única forma de ler fontes onde o usuário não é admin).
- **Envio**: Bot API via HTTP. Não loga o bot por MTProto a cada execução (login repetido de bot causa FloodWait).
- **Mídia**: file references são por conta; o bot não reutiliza a foto vista pelo usuário. É preciso baixar os bytes e fazer upload.
- **Entidades**: `StringSession` não guarda cache de entidades entre execuções; chamar `get_dialogs()` no início para resolver os IDs das fontes.

## 6. Estrutura de arquivos

```
send-promo/
├── check.py                    # busca, filtra, envia, atualiza state.json
├── gen_session.py              # roda 1x local: login do usuário, imprime StringSession
├── list_chats.py               # utilitário local: lista chats/IDs (achar DEST_CHAT_ID)
├── requirements.txt            # telethon, requests
├── .env.example                # para teste local
├── .gitignore                  # .env, *.session, state.json, venv/
├── .github/workflows/
│   ├── check.yml               # cron */5
│   └── keepalive.yml           # commit vazio mensal
├── contexto.md
└── prd.md
```

## 7. Configuração

### GitHub Secrets (Settings → Secrets and variables → Actions)

| Secret | Origem |
|--------|--------|
| `TG_API_ID` | my.telegram.org → API development tools |
| `TG_API_HASH` | idem |
| `TG_SESSION` | saída de `gen_session.py` |
| `BOT_TOKEN` | @BotFather |
| `DEST_CHAT_ID` | ID do grupo pessoal (via `list_chats.py`), formato `-100...` |

Para teste local, as mesmas variáveis vão em `.env`.

### Hardcoded em `check.py`

```python
SOURCES = [-1001444838674, -1001283210985, -1001968825483]
KEYWORDS = ("teclado", "headset")
FIRST_RUN_LOOKBACK = timedelta(minutes=30)
```

## 8. Notas de implementação

### `check.py` (esboço, não definitivo)

```python
import asyncio, json, os, logging
from datetime import datetime, timedelta, timezone
import requests
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.extensions import html

SOURCES = [-1001444838674, -1001283210985, -1001968825483]
KEYWORDS = ("teclado", "headset")
FIRST_RUN_LOOKBACK = timedelta(minutes=30)
STATE_FILE = "state.json"
CAPTION_LIMIT = 1024

BOT_API = f"https://api.telegram.org/bot{os.environ['BOT_TOKEN']}"
DEST = os.environ["DEST_CHAT_ID"]


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def bot_call(method, data, files=None):
    r = requests.post(f"{BOT_API}/{method}", data=data, files=files, timeout=30)
    r.raise_for_status()


def send(body_html, body_plain, photo):
    # tenta HTML; se a Bot API rejeitar, cai para texto puro
    ...  # sendPhoto (legenda <= CAPTION_LIMIT) | sendPhoto sem legenda + sendMessage | sendMessage


async def main():
    state = load_state()
    client = TelegramClient(
        StringSession(os.environ["TG_SESSION"]),
        int(os.environ["TG_API_ID"]),
        os.environ["TG_API_HASH"],
    )
    async with client:
        await client.get_dialogs()  # popula cache de entidades
        for source in SOURCES:
            entity = await client.get_entity(source)
            key = str(source)
            if key in state:
                kwargs = {"offset_id": state[key]}
            else:
                kwargs = {"offset_date": datetime.now(timezone.utc) - FIRST_RUN_LOOKBACK}
            async for msg in client.iter_messages(entity, reverse=True, limit=200, **kwargs):
                text = msg.message or ""
                if any(k in text.lower() for k in KEYWORDS):
                    link = f"https://t.me/c/{key[4:]}/{msg.id}"
                    header = f"📢 {entity.title}\n{link}\n\n"
                    photo = await msg.download_media(file=bytes) if msg.photo else None
                    send(header + html.unparse(text, msg.entities), header + text, photo)
                state[key] = msg.id
                save_state(state)


asyncio.run(main())
```

Pontos a tratar:

- `reverse=True` inverte o sentido de `offset_date`/`min_id` no Telethon (mensagens **depois** do ponto, da mais antiga para a mais nova). Validar no teste local.
- Erro no envio (HTTP ≠ 200, exceto erro de parse HTML que tem fallback): logar, sair do loop daquela fonte **sem** avançar o ID, seguir para a próxima fonte; ao final, sair com código ≠ 0 para a execução aparecer como falha no Actions.
- HTTP 429 da Bot API: respeitar `retry_after` da resposta.
- Upload da foto: `files={"photo": ("promo.jpg", photo)}`.
- Fonte não resolvida por `get_entity`: erro claro com o ID.
- `html.unparse` do Telethon gera tags (`<a>`, `<b>`, `<i>`, `<code>`, `<pre>`, `<s>`, `<u>`) aceitas pela Bot API; tags não suportadas caem no fallback de texto puro.

### `.github/workflows/check.yml`

```yaml
name: check-promos

on:
  schedule:
    - cron: "*/5 * * * *"
  workflow_dispatch:

concurrency:
  group: check-promos
  cancel-in-progress: false

permissions:
  contents: read

jobs:
  check:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements.txt
      - uses: actions/cache/restore@v4
        with:
          path: state.json
          key: state-${{ github.run_id }}
          restore-keys: state-
      - run: python check.py
        env:
          TG_API_ID: ${{ secrets.TG_API_ID }}
          TG_API_HASH: ${{ secrets.TG_API_HASH }}
          TG_SESSION: ${{ secrets.TG_SESSION }}
          BOT_TOKEN: ${{ secrets.BOT_TOKEN }}
          DEST_CHAT_ID: ${{ secrets.DEST_CHAT_ID }}
      - uses: actions/cache/save@v4
        if: always() && hashFiles('state.json') != ''
        with:
          path: state.json
          key: state-${{ github.run_id }}
```

- Cache é imutável por chave: cada execução salva com chave nova (`run_id`) e a próxima restaura a mais recente via `restore-keys`. Entradas antigas são removidas automaticamente pelo GitHub (LRU / 7 dias sem acesso).
- `if: always()` salva o estado mesmo se `check.py` falhar no meio (preserva o progresso parcial).
- Usar as versões major mais recentes das actions na implementação.

### `.github/workflows/keepalive.yml`

```yaml
name: keepalive

on:
  schedule:
    - cron: "0 12 1 * *"
  workflow_dispatch:

permissions:
  contents: write

jobs:
  keepalive:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git commit --allow-empty -m "chore: keepalive"
          git push
```

Evita que o GitHub desative os workflows agendados após 60 dias sem atividade no repositório.

## 9. Setup (passo a passo)

### 9.1 Credenciais da API (uma vez)

1. Acessar https://my.telegram.org → login com o número do Telegram.
2. **API development tools** → criar app (nome/descrição qualquer, plataforma "Other").
3. Anotar `api_id` e `api_hash`.

### 9.2 Bot

1. No Telegram, abrir **@BotFather** → `/newbot` → nome e username.
2. Anotar o token → `BOT_TOKEN`.
3. O destino "Promos" é um **canal** (não grupo): bot não entra como inscrito, só como **administrador**. Canal → ⋮ → Gerenciar canal → Administradores → Adicionar administrador → buscar pelo **@username** do bot → manter permissão **Publicar mensagens**.
4. `DEST_CHAT_ID`: abrir o canal em web.telegram.org/a e copiar o número após `#` na URL (`-100...`).
5. Confirmar que posts do bot no canal geram notificação no celular (canal não pode estar silenciado).

### 9.3 Sessão do usuário (local, uma vez)

1. `pip install -r requirements.txt`
2. `python gen_session.py` → informar telefone, código recebido no Telegram e senha 2FA (se houver).
3. Copiar a string impressa → `TG_SESSION`. **Tratar como senha.**
4. `python list_chats.py` → copiar o ID do grupo pessoal → `DEST_CHAT_ID`. Conferir que os 3 IDs de `SOURCES` aparecem na lista.

### 9.4 Teste local

1. Preencher `.env` e rodar `python check.py` duas vezes seguidas.
2. Primeira execução: processa os últimos 30 min e cria `state.json`.
3. Segunda execução: não reenvia nada (só mensagens novas).
4. Para forçar um match: apagar `state.json` e aumentar `FIRST_RUN_LOOKBACK` temporariamente até cobrir uma promo de teclado/headset real.

### 9.5 GitHub

1. Criar repositório **público** e fazer push (conferir que `.env`, `*.session` e `state.json` estão no `.gitignore`).
2. Cadastrar os 5 secrets (seção 7).
3. Actions → `check-promos` → **Run workflow** (execução manual) → conferir log e grupo pessoal.
4. Actions → `keepalive` → **Run workflow** uma vez para validar permissão de push.
5. Aguardar as execuções agendadas (a primeira pode demorar a começar).

## 10. Critérios de aceite

- [ ] Promo contendo "Teclado" postada em qualquer uma das 3 fontes chega ao grupo pessoal na execução seguinte do cron, com foto, texto, nome da fonte e link da mensagem original.
- [ ] Idem para "headset"/"HEADSET".
- [ ] Promo sem as keywords **não** é enviada.
- [ ] Nenhuma promo é enviada duas vezes pela mesma fonte em execuções consecutivas.
- [ ] Execução pulada/atrasada pelo cron: as mensagens do intervalo são processadas na execução seguinte.
- [ ] Sem cache (primeira execução): só mensagens dos últimos 30 min são avaliadas.
- [ ] Notificação push toca no celular do usuário.
- [ ] Links do texto continuam clicáveis na mensagem reenviada.
- [ ] Falha de envio não avança o ID; a mensagem é tentada de novo na execução seguinte.
- [ ] Logs públicos do Actions não contêm secrets nem texto completo das mensagens.
- [ ] Workflow `keepalive` roda e faz push com sucesso.

## 11. Riscos

| Risco | Impacto | Mitigação |
|-------|---------|-----------|
| Vazamento da `TG_SESSION` | Acesso total à conta Telegram | Só em Secrets; nunca imprimir; se vazar, encerrar sessão em Configurações → Dispositivos e gerar outra. |
| Workflow malicioso via PR em repo público | Exfiltração de secrets | Não usar `pull_request_target`; manter padrão do GitHub (secrets não expostos a PRs de fork); aprovar execuções de contribuidores externos. |
| Atraso/pulo do cron do GitHub | Alerta atrasado; promo pode expirar | Aceito. Upgrade: listener 24h (Oracle Always Free). |
| Telegram encerra sessão usada de IPs variados | Script para de ler | Execução falha (visível no Actions); gerar nova sessão e atualizar secret. |
| Ban/limitação da conta | Perde a leitura | Uso só leitura; envio pelo bot. |
| Cache expira/é removido | Perde mensagens entre última execução e agora (> 30 min) | Execuções a cada 5 min mantêm o cache acessado; aceito. |
| Workflows desativados após 60 dias sem atividade | Para de rodar | `keepalive.yml` mensal. |
| GitHub restringe uso de Actions para automação não-CI | Para de rodar | Uso leve; plano de migração para Oracle/PC local. |
| Fonte muda formato / foto sem legenda | Perde matches | Aceito; revisar se parar de chegar alerta. |
| Falsos positivos | Ruído | Aceito pelo usuário. |
| Mesma promo em 3 fontes | Até 3 alertas iguais | Aceito (decisão: sem dedup). |
