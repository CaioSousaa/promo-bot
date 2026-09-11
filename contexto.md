# Contexto — send-promo

## Problema

O usuário participa de 3 grupos/canais de promoções no Telegram que postam dezenas de ofertas por dia, de produtos variados. Ele só tem interesse em **teclado** e **headset**. Acompanhar os 3 manualmente é inviável.

## Fontes monitoradas

| # | Link web                                   | Chat ID          |
|---|--------------------------------------------|------------------|
| 1 | https://web.telegram.org/a/#-1001444838674 | `-1001444838674` |
| 2 | https://web.telegram.org/a/#-1001283210985 | `-1001283210985` |
| 3 | https://web.telegram.org/a/#-1001968825483 | `-1001968825483` |

O prefixo `-100` indica supergrupo ou canal. O print de referência mostra contador de visualizações (302), o que sugere que ao menos um deles é **canal** (broadcast). O usuário é membro, **não admin** — não é possível adicionar um bot dentro dessas fontes.

## Padrão de mensagem observado

Mensagem com **foto + legenda**:

```
🔥 Roteador Mesh WI-FI 7 Deco Be22(2-pack)( Tp-link

💰 R$ 806
🎟️ Cupom: ECONOMIZO

✅ Link do produto: 👇
➡️ https://meli.la/23SoCpZ

(ANÚNCIO)
```

- Primeira linha: nome do produto (é onde "teclado"/"headset" vai aparecer).
- Preço, cupom (opcional), link encurtado (meli.la, amzn.to etc.).
- O texto fica na **legenda** da foto, não em uma mensagem de texto separada.

## Decisões tomadas (e por quê)

| Tema | Decisão | Motivo |
|------|---------|--------|
| Canal de saída | Telegram (não email) | Mantém foto/link/cupom; notificação push imediata. Email exigiria SMTP, é mais lento e perde a imagem. |
| Como ler as fontes | **Userbot** (MTProto via Telethon, conta do próprio usuário) | Bot API não lê chats onde o bot não é membro, e o usuário não é admin das fontes. |
| Quem envia o alerta | **Bot do BotFather, via Bot API HTTP** | Se a própria conta do usuário enviasse, o Telegram não notificaria (remetente = ele mesmo). Bot API HTTP é stateless: evita logar o bot via MTProto a cada execução (login repetido de bot gera FloodWait). |
| Destino | Grupo pessoal do usuário, com o bot adicionado | Escolha do usuário. |
| Keywords | Apenas `teclado` e `headset`, hardcoded | Escolha do usuário. Sem variações, sem lista de exclusão. |
| Formato do alerta | Mensagem original (foto + texto) + nome do grupo de origem | Escolha do usuário. |
| Duplicatas | **Deduplicar por nome + preço + cupom, janela de 24h** (revisado em 11/09/2026) | Inicialmente "enviar todas", mas a mesma promo chegava 3x. O link fica de fora da comparação: cada canal usa o próprio link de afiliado (e o mesmo canal reposta com outro link encurtado), então ele nunca se repete. Estado guardado em `state.json["sent"]`, sem banco. |
| Linguagem | Python + Telethon | Lib de userbot mais madura; script pequeno. |
| Hospedagem | **GitHub Actions com cron, repositório público** | Usuário quer custo zero, sem cartão de crédito e sem se preocupar com código público. Repo público = minutos de Actions ilimitados. |
| Modelo de execução | **Polling** (a cada ~5 min), não listener | Actions não mantém processo vivo; cada execução busca as mensagens novas desde a última lida e termina. |
| Estado | `state.json` com último ID lido por fonte, guardado no **cache do Actions** | Não suja o histórico com commits a cada 5 min. Cobre execuções atrasadas ou puladas pelo cron. |

## Trade-off aceito

**Latência**: o cron do GitHub tem mínimo de 5 min e costuma atrasar (às vezes 10–20 min, e pode pular execuções em horário de pico). O alerta chega com atraso; promoção relâmpago pode acabar antes. Aceito em troca de custo zero sem cartão.

## Alternativas descartadas

- **Email**: mais configuração (SMTP/senha de app), latência maior, perde imagem.
- **Encaminhar com a própria conta**: sem notificação.
- **Oracle Cloud Always Free (listener 24h)**: tempo real, grátis, mas exige cartão de crédito, setup de VM, risco de recuperação de VM ociosa e cuidado para não gerar cobrança. Continua sendo o upgrade natural se a latência incomodar — o filtro/envio é reaproveitável.
- **PC local**: grátis, mas sem alerta com PC desligado/suspenso.
- **Koyeb/Render free**: dormem por inatividade. **Railway/Fly.io**: sem free real para conta nova. **Hosts "free 24/7" obscuros**: descartados por segurança (a sessão dá acesso total à conta).
- **GitHub Actions em repo privado**: 2000 min/mês grátis não comportam cron a cada 5 ou 15 min (cada execução conta no mínimo 1 min).
- **Commit do `state.json` a cada execução**: centenas de commits por dia; substituído por cache.

## Pontos de atenção conhecidos

- **Sessão do Telethon = acesso total à conta Telegram.** Fica só em GitHub Secrets. Nunca commitar, nunca imprimir.
- **Logs do Actions são públicos** em repo público. Não logar segredos nem conteúdo sensível.
- **60 dias sem atividade** no repo público → GitHub desativa workflows agendados. Mitigação: workflow mensal de keepalive (commit vazio).
- **Sessão usada de IPs de datacenter variáveis** (runners do GitHub): Telegram pode, raramente, encerrar a sessão. Mitigação: gerar nova sessão e atualizar o secret.
- **Termos do GitHub Actions**: uso para automação pessoal não ligada a CI é zona cinza; uso leve raramente é problema, mas a GitHub pode restringir.
- **Falsos positivos** esperados (ex.: "suporte para headset", "kit teclado e mouse"). Aceitos pelo usuário.
- **Credenciais**: usuário ainda não tem `api_id`/`api_hash` nem bot. O PRD inclui o passo a passo.
