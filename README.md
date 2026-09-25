# Ghost: empresa de afiliados no piloto automático

Marca: **Achei na Cozinha**. Achadinhos de cozinha e casa com comissão da Shopee, da Amazon e do Mercado Livre.
Roda de graça no GitHub Actions. Custo mensal: R$ 0.

## Os agentes

| # | Agente | Arquivo | O que faz |
|---|---|---|---|
| 1 | Caçador | `ghost/agents/cacador.py` | Busca ofertas na API da Shopee e lê `data/curadoria.csv` (Amazon e Mercado Livre) |
| 2 | Curador | `ghost/agents/curador.py` | Filtra por nota, vendas, preço e comissão, pontua, evita repetir e numera (#N) |
| 3 | Redator | `ghost/agents/redator.py` | Escreve gancho, dica de chef, benefícios e roteiro (Gemini grátis, com templates de reserva) |
| 4 | Designer | `ghost/agents/designer.py` | Artes 1080x1350, carrossel e quadros verticais |
| 5 | Editor de vídeo | `ghost/agents/videomaker.py` | Reels de 15 s com narração (edge-tts ou gTTS) e legendas |
| 6 | Publicadores | `telegram.py`, `meta.py` | Canal do Telegram, Instagram (post, reel, carrossel), Threads |
| 7 | Vitrine | `ghost/agents/vitrine.py` | Página "link na bio" no GitHub Pages |
| 8 | Analista | `ghost/agents/analista.py` | Relatório semanal de vendas por canal (subIds da Shopee) |
| — | Gerente | `ghost/run.py` | Orquestra tudo, manda alertas e o resumo diário para o seu Telegram |

## Testar sem nenhuma conta

```bash
pip install -r requirements.txt
python -m ghost.run demo      # simula um dia inteiro; veja out/ e docs/index.html
python -m pytest -q
```

## Colocar no ar (uma vez, ~3 h)

1. **GitHub**: crie um repositório **público** (Actions e Pages grátis) e suba estes arquivos.
   Em *Settings → Pages*, escolha **Source: GitHub Actions**.
2. **Variables** (*Settings → Secrets and variables → Actions → Variables*):
   - `PUBLIC_BASE_URL` = `https://SEU_USUARIO.github.io/NOME_DO_REPO`
   - `TELEGRAM_CHANNEL_URL` = `https://t.me/seucanal`
3. **Secrets** (mesma tela, aba *Secrets*):

| Secret | Onde conseguir |
|---|---|
| `SHOPEE_APP_ID`, `SHOPEE_SECRET` | affiliate.shopee.com.br → cadastro → pedir acesso à Open API pelo suporte → página "Open API" |
| `GEMINI_API_KEY` | aistudio.google.com → Get API key (grátis, sem cartão) |
| `TELEGRAM_BOT_TOKEN` | @BotFather → /newbot |
| `TELEGRAM_CHANNEL_ID` | `@seucanal` (adicione o bot como administrador) |
| `TELEGRAM_OWNER_CHAT_ID` | mande "oi" para o bot e abra `https://api.telegram.org/bot<TOKEN>/getUpdates` → `chat.id` |
| `IG_USER_ID`, `IG_ACCESS_TOKEN` | developers.facebook.com → app do tipo Business → produto "Instagram" → API com login do Instagram → gerar token (conta Profissional) |
| `THREADS_USER_ID`, `THREADS_ACCESS_TOKEN` | mesmo app → caso de uso "Threads API" → gerar token longo |
| `GH_PAT` | github.com/settings/tokens → fine-grained → só este repo → *Secrets: Read and write* (renova os tokens da Meta sozinho) |
| `GROQ_API_KEY` (opcional) | console.groq.com (IA reserva, grátis) |

4. Rode manualmente *Actions → Ofertas → Run workflow* e confira o canal.
5. Ajuste `config/*.yaml` (marca, nicho, calendário, canais) quando quiser; não precisa mexer no código.

## Rotina de horários (Brasília)

- **Ofertas**: 08h05 a 22h05, a cada 2 h (Telegram; Threads a cada ~3,5 h).
- **Instagram**: 11h post (domingo = carrossel Top 5), 18h30 Reel. O vídeo também chega no seu Telegram para você postar no TikTok e no Shorts.
- **Resumo diário**: 08h30 no seu Telegram, com o texto pronto para o canal do WhatsApp.
- **Semanal**: segunda 09h, relatório. Dias 1 e 15: renovação dos tokens.

## Limites honestos

- A API do TikTok só publica em modo privado até passar por auditoria, então o vídeo vem para você postar (1 min).
- O Canal do WhatsApp não tem API oficial. Você cola a mensagem pronta (30 s).
- Amazon: preço só pode aparecer se vier da API. A curadoria manual da Amazon sai sem preço. A Creators API libera depois das primeiras vendas.
- Mercado Livre não tem API pública de afiliados: os links entram pela `data/curadoria.csv`.
