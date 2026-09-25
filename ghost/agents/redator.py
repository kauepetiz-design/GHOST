"""Agente 3 — Redator.

Escreve o texto de cada oferta no tom da marca (gancho, dica de chef, benefícios, roteiro de vídeo)
e formata a versão certa para cada canal. Usa IA gratuita quando há chave; senão, templates.
"""
from __future__ import annotations

import html
import random
import zlib

from .. import llm
from ..core import brl, cfg, clean_title, get_logger, load_json, public_base_url, save_json, tema_atual

log = get_logger("redator")

PROMPT = """Você é o redator da página "{marca}", de achadinhos de cozinha escolhidos por quem trabalha em cozinha profissional.
Tom de voz: {tom}
Tema comercial do momento: {tema}

Produto (dados reais do marketplace — NÃO invente características que não estejam no título):
- Título: {titulo}
- Preço: {preco}
- Desconto: {desconto}
- Avaliação: {nota} estrelas, {vendas} vendidos
{nota_chef}
Responda SOMENTE um JSON com as chaves:
"titulo_curto": nome do produto em até 38 caracteres, sem marca e sem exagero;
"gancho": frase de abertura de até 60 caracteres que fala de um problema real na cozinha;
"dica_chef": 1 ou 2 frases (até 200 caracteres) com uma dica prática de cozinha profissional sobre usar esse tipo de produto;
"beneficios": lista com 3 benefícios curtos (até 45 caracteres cada), coerentes com o título;
"roteiro": lista com 4 frases curtas para narrar um vídeo de 15 segundos (gancho, produto, dica, chamada "link no perfil");
"hashtags": lista com 5 hashtags em português sem acento, relevantes.
Proibido: CAPS LOCK, "corre", urgência falsa, promessas de saúde, citar concorrentes."""

GANCHOS = [
    "Isso aqui resolve um problema chato da cozinha",
    "O item que eu mais uso na cozinha do restaurante",
    "Achei e testei: vale cada centavo",
    "Parece bobo, mas muda sua rotina na cozinha",
    "Cozinha de chef gastando pouco",
]
DICAS = {
    "termômetro": "Carne boa é carne no ponto certo. Com termômetro você para de cortar a peça para olhar e ela não perde suco.",
    "faca": "Faca afiada é mais segura que faca cega: ela não escorrega. Afie toda semana e guarde longe da gaveta bagunçada.",
    "afiador": "Passe a faca sempre no mesmo ângulo e poucas vezes. Chaira no dia a dia, afiador quando perder o corte.",
    "balança": "Confeitaria é química: pese, não use xícara. A diferença no resultado é enorme.",
    "pote": "Pote transparente e etiquetado é regra na cozinha profissional: você vê o que tem e evita desperdício.",
    "silicone": "Silicone não risca antiaderente e aguenta calor. Espátula boa raspa a panela toda e nada se perde.",
    "air fryer": "Não lote o cesto: o ar precisa circular para dourar. Chacoalhe na metade do tempo.",
    "frigideira": "Panela de ferro bem curada vira antiaderente natural. Seque no fogo e passe um fio de óleo depois de lavar.",
    "mixer": "Bata sopas e molhos direto na panela: menos louça e textura lisa sem transferir nada quente.",
    "tempero": "Tempero organizado e à vista é tempero usado. Deixe os mais usados na frente.",
    "fatiador": "Corte uniforme = cozimento uniforme. Use sempre o protetor de mão, a lâmina é muito afiada.",
}


def _dica(titulo: str) -> str:
    t = titulo.lower()
    for k, v in DICAS.items():
        if k in t:
            return v
    return "Na cozinha profissional, ferramenta certa economiza tempo todo dia. Esse é daqueles que você usa mais do que imagina."


def _template(o: dict) -> dict:
    rnd = random.Random(zlib.crc32(o["id"].encode()))
    curto = clean_title(o["titulo"], 38)
    return {
        "titulo_curto": curto,
        "gancho": rnd.choice(GANCHOS),
        "dica_chef": o.get("nota_chef") or _dica(o["titulo"]),
        "beneficios": [
            f"Nota {o['nota']:.1f} de quem comprou".replace(".", ",") if o.get("nota") else "Bem avaliado por quem comprou",
            f"{o['vendas']:,} vendidos".replace(",", ".") if o.get("vendas") else "Muito procurado",
            f"{o['desconto_pct']}% abaixo do preço cheio" if o.get("desconto_pct") else "Preço justo pelo que entrega",
        ],
        "roteiro": [
            rnd.choice(GANCHOS) + ".",
            f"{curto}.",
            (o.get("nota_chef") or _dica(o["titulo"])).split(".")[0] + ".",
            "O link tá no perfil. Salva pra não perder.",
        ],
        "hashtags": ["achadinhos", "cozinha", "dicasdecozinha", "utilidadesdomesticas", "achadinhosshopee"],
    }


def copy(o: dict) -> dict:
    """Retorna (e guarda no pool) o texto-base da oferta."""
    if o.get("copy"):
        return o["copy"]
    marca = cfg("marca")
    c = None
    if llm.disponivel():
        c = llm.gerar_json(PROMPT.format(
            marca=marca["nome"], tom=marca["tom_de_voz"].strip(), tema=tema_atual().get("gancho", ""),
            titulo=o["titulo"], preco=brl(o.get("preco")) or "não informado",
            desconto=f"{o.get('desconto_pct', 0)}%", nota=o.get("nota", "?"), vendas=o.get("vendas", "?"),
            nota_chef=f"- Observação do chef (use!): {o['nota_chef']}\n" if o.get("nota_chef") else "",
        ))
    base = _template(o)
    if c:
        for k in base:  # completa o que faltar
            if not c.get(k):
                c[k] = base[k]
        c["titulo_curto"] = c["titulo_curto"][:40]
        c["origem"] = "ia"
    else:
        c = base
        c["origem"] = "template"
    o["copy"] = c
    pool = load_json("ofertas.json", [])
    for p in pool:
        if p["id"] == o["id"]:
            p["copy"] = c
    save_json("ofertas.json", pool)
    return c


# ------------------------------------------------------------------ formatos por canal
def _linha_preco(o: dict) -> str:
    if not o.get("preco"):
        return "Confira o preço atualizado no link."
    s = f"Por {brl(o['preco'])}"
    if o.get("preco_de") and o["preco_de"] > o["preco"]:
        s = f"De {brl(o['preco_de'])} por {brl(o['preco'])}"
    return s


def telegram(o: dict) -> str:
    """HTML do Telegram (legenda de foto, máx. 1024 caracteres)."""
    c, m = copy(o), cfg("marca")
    e = html.escape
    partes = [
        f"<b>{e(c['gancho'])}</b>",
        "",
        f"🍳 <b>{e(c['titulo_curto'])}</b>",
        f"💰 {e(_linha_preco(o))}" + (f" <i>(-{o['desconto_pct']}%)</i>" if o.get("desconto_pct") else ""),
        "",
        f"👨‍🍳 {e(c['dica_chef'])}",
        "",
        *[f"✔️ {e(b)}" for b in c["beneficios"][:3]],
        "",
        f"<i>{e(m['aviso_afiliado'])} {e(m['aviso_preco'])}</i>",
    ]
    return "\n".join(partes)[:1024]


def threads(o: dict, url: str) -> str:
    c, m = copy(o), cfg("marca")
    txt = (
        f"{c['gancho']}\n\n{c['titulo_curto']}: {_linha_preco(o)}\n\n"
        f"Dica de chef: {c['dica_chef']}\n\n{url}\n\n{m['aviso_afiliado']}"
    )
    if len(txt.encode()) > 490:  # limite de 500 (em bytes UTF-8)
        txt = f"{c['gancho']}\n\n{c['titulo_curto']}: {_linha_preco(o)}\n\n{url}\n\n{m['aviso_afiliado']}"
    return txt


def instagram(o: dict) -> str:
    c, m = copy(o), cfg("marca")
    vitrine = public_base_url()
    tags = " ".join("#" + h.lstrip("#") for h in c["hashtags"][:5])
    return (
        f"{c['gancho']}\n\n"
        f"{c['titulo_curto']} | {_linha_preco(o)}\n\n"
        f"👨‍🍳 Dica de chef: {c['dica_chef']}\n\n"
        + "\n".join(f"✔️ {b}" for b in c["beneficios"][:3])
        + f"\n\n🔗 Link na bio → procure a oferta #{o.get('numero')}"
        + (" na vitrine" if vitrine else "")
        + "\n📲 Ofertas todo dia no nosso canal do Telegram (link na bio)\n"
        f"💾 Salva pra achar depois.\n\n{m['aviso_afiliado']} {m['aviso_preco']}\n\n{tags}"
    )[:2200]


def instagram_carrossel(ofertas: list[dict]) -> str:
    m = cfg("marca")
    linhas = [f"#{o.get('numero')} {copy(o)['titulo_curto']} | {_linha_preco(o)}" for o in ofertas]
    return (
        f"Top {len(ofertas)} achados da semana, escolhidos a dedo 👨‍🍳\n\n" + "\n".join(linhas)
        + "\n\n🔗 Todos os links na vitrine (link na bio), é só procurar o número.\n"
        f"Qual você levaria? Comenta o número 👇\n\n{m['aviso_afiliado']} {m['aviso_preco']}\n\n"
        "#achadinhos #cozinha #dicasdecozinha #achadinhosshopee #utilidadesdomesticas"
    )


def whatsapp(ofertas_links: list[tuple[dict, str]]) -> str:
    m = cfg("marca")
    blocos = [f"🍳 *Achados do dia: {m['nome']}*\n"]
    for o, url in ofertas_links:
        c = copy(o)
        blocos.append(f"*{c['titulo_curto']}*\n{_linha_preco(o)}\n{url}\n")
    blocos.append(f"_{m['aviso_afiliado']}_")
    return "\n".join(blocos)
