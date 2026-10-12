"""Agente 7 — Conferência semanal (toda segunda, no seu Telegram).

Para cada produto que provavelmente vai ao ar nos próximos dias, monta UMA imagem com a FOTO DO ANÚNCIO ao lado da ARTE
FINAL do Reel e manda para você. Conferência de poucos minutos:

    1. o recorte mostra o produto inteiro?      (olhe a arte final)
    2. é o mesmo item do anúncio?               (kit não pode virar unidade; compare as duas fotos)
    3. preço e estoque continuam valendo?       (toque no link que vem junto)

O robô já marca com ⚠️ o que merece atenção: foto com cenário/selo/infográfico (cai no cartão branco), foto pequena
(fica borrada) e foto que não abriu (o robô usaria um ícone). Para trocar a foto de um produto, mude a coluna
`imagem_url` dele no data/curadoria.csv (Upload files).

    python -m ghost.run conferencia        # 10 produtos
    python -m ghost.run conferencia 15     # 15 produtos
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from ..core import OUT_DIR, brl, env, get_logger, load_json, slugify
from . import curador, designer, telegram, videofaixas
from . import videomaker as vm

log = get_logger("conferencia")

VERDE, AMBAR, VERMELHO = "#3A7D44", "#B7791F", "#B3261E"
FOTO_MIN_PX = 500   # abaixo disso a foto fica borrada quando ampliada


# ------------------------------------------------------------------ escolha dos produtos
def candidatas(pool: list[dict], n: int) -> list[dict]:
    """Os que o Curador escolheria a seguir para Reel (sem registrar nada nem gastar numeração)."""
    ja = curador._recentes("instagram_reel")
    boas = [o for o in pool if curador._passa_filtros(o) and o["id"] not in ja]
    for o in boas:
        o["score"] = curador.nota(o)
    boas.sort(key=lambda o: o["score"], reverse=True)
    return boas[:n]


def _numero(o: dict) -> str:
    n = o.get("numero") or load_json("estado.json", {}).get("numeros", {}).get(o["id"])
    return f"#{n}" if n else "novo"


def _baixou(o: dict) -> bool:
    """designer.imagem_produto guarda a foto em cache quando baixa; sem foto ele desenha um ícone e NÃO guarda."""
    if o.get("imagem_local") and Path(o["imagem_local"]).exists():
        return True
    return (OUT_DIR / "img" / f"{slugify(o['id'], 60)}.jpg").exists()


# ------------------------------------------------------------------ avaliação + imagem
def avaliar(o: dict) -> tuple[Image.Image, Image.Image, list[tuple[str, str]]]:
    """(foto original, arte final, avisos[(nivel, texto)]); nivel: ok | aviso | erro."""
    original = designer.imagem_produto(o)
    baixou = _baixou(o)
    arte, info = videofaixas.previa(o)
    avisos: list[tuple[str, str]] = []
    if not baixou:
        avisos.append(("erro", "A foto do anúncio não abriu (usei um ícone). Confira o link da imagem no CSV."))
    elif not info["recorte"]:
        avisos.append(("aviso", "Foto com cenário, selo ou infográfico: vai num cartão branco. "
                                "Troque por uma foto de fundo branco."))
    else:
        avisos.append(("ok", "Recorte automático feito. Confira se o produto aparece inteiro e é o mesmo do anúncio."))
    if baixou and min(info["foto_px"]) < FOTO_MIN_PX:
        avisos.append(("aviso", f"Foto pequena ({info['foto_px'][0]}x{info['foto_px'][1]} px): pode ficar borrada."))
    return original, arte, avisos


def _gravidade(avisos: list[tuple[str, str]]) -> str:
    niveis = {n for n, _ in avisos}
    return "erro" if "erro" in niveis else "aviso" if "aviso" in niveis else "ok"


def _linhas(texto: str, f, largura: int) -> list[str]:
    linhas, atual = [], ""
    for p in texto.split():
        teste = f"{atual} {p}".strip()
        if vm._MD.textlength(teste, font=f) <= largura or not atual:
            atual = teste
        else:
            linhas.append(atual)
            atual = p
    return linhas + ([atual] if atual else [])


def painel(o: dict, original: Image.Image, arte: Image.Image, avisos: list[tuple[str, str]]) -> Image.Image:
    """Imagem de conferência: título e preço em cima, foto do anúncio | arte final no meio, avisos embaixo."""
    L, M, PW, PH = 1200, 40, 540, 960
    cor = {k: vm._cor(k) for k in ("primaria", "fundo", "texto")}
    f_t, f_p, f_c, f_a = vm.F("titulo", 36), vm.F("titulo", 44), vm.F("titulo", 24), vm.F("medio", 28)
    gravidade = _gravidade(avisos)
    faixa_cor = {"ok": VERDE, "aviso": AMBAR, "erro": VERMELHO}[gravidade]
    rotulo = {"ok": "OK: ", "aviso": "ATENÇÃO: ", "erro": "ERRO: "}   # a fonte da marca não tem ✔/⚠
    txt_av = [rotulo[n] + t for n, t in avisos]
    linhas_av = [ln for t in txt_av for ln in _linhas(t, f_a, L - 2 * M)]
    h_rodape = 28 + 38 * len(linhas_av)
    H = 130 + 40 + PH + 24 + h_rodape
    im = Image.new("RGB", (L, H), cor["fundo"])
    d = ImageDraw.Draw(im)

    # cabeçalho: número, título e preço
    d.ellipse([M, 30, M + 76, 106], fill=cor["primaria"])
    d.text((M + 38, 68), _numero(o), font=vm.F("titulo", 24 if len(_numero(o)) > 3 else 30), fill=cor["fundo"], anchor="mm")
    for i, ln in enumerate(_linhas(o["titulo"], f_t, 700)[:2]):
        d.text((M + 100, 44 + i * 44), ln, font=f_t, fill=cor["texto"], anchor="lm")
    if o.get("preco"):
        d.text((L - M, 52), f"por {brl(o['preco'])}", font=f_p, fill=cor["primaria"], anchor="rm")
        if o.get("preco_de") and o["preco_de"] > o["preco"]:
            d.text((L - M, 96), f"de {brl(o['preco_de'])}", font=f_a, fill="#7A726B", anchor="rm")

    # painéis
    y0 = 130 + 40
    for x, legenda in ((M, "FOTO DO ANÚNCIO"), (L - M - PW, "ARTE FINAL DO REEL")):
        d.text((x, y0 - 16), legenda, font=f_c, fill="#7A726B", anchor="lm")
    branco = Image.new("RGB", (PW, PH), "white")
    foto = ImageOps.contain(original.convert("RGB"), (PW - 20, PH - 20), Image.LANCZOS)
    branco.paste(foto, ((PW - foto.width) // 2, (PH - foto.height) // 2))
    im.paste(branco, (M, y0))
    im.paste(arte.convert("RGB").resize((PW, PH), Image.LANCZOS), (L - M - PW, y0))
    d.rectangle([M - 1, y0 - 1, M + PW, y0 + PH], outline="#D8CFC4", width=2)

    # rodapé com os avisos
    ry = y0 + PH + 24
    d.rectangle([0, ry, L, H], fill=faixa_cor)
    for i, ln in enumerate(linhas_av):
        d.text((M, ry + 14 + 19 + i * 38), ln, font=f_a, fill="white", anchor="lm")
    return im


def _legenda(o: dict, avisos: list[tuple[str, str]]) -> str:
    preco = f"por {brl(o['preco'])}" if o.get("preco") else "preço no link"
    if o.get("preco") and o.get("preco_de") and o["preco_de"] > o["preco"]:
        preco += f" (de {brl(o['preco_de'])})"
    sinais = "\n".join(("✅ " if n == "ok" else "⚠️ ") + t for n, t in avisos)
    return f"{_numero(o)} · {o['titulo'][:90]}\n💰 {preco}\n{sinais}\n🔗 {o.get('link', '')}"


# ------------------------------------------------------------------ envio
def _enviar_foto(arquivo: Path, legenda: str) -> None:
    chat = env("TELEGRAM_OWNER_CHAT_ID") or "dono"
    with open(arquivo, "rb") as f:
        telegram._api("sendPhoto", {"chat_id": chat, "caption": legenda[:1024]}, {"photo": f})


def executar(n: str | int = 10) -> None:
    n = int(n)
    if not videofaixas.fundos_disponiveis():
        telegram.para_dono("⚠️ Conferência: não achei imagens em assets/fundos, então os Reels estão saindo no modelo "
                           "clássico. Suba os fundos de cozinha para ligar o modelo novo.")
        return
    pool = load_json("ofertas.json", [])
    escolhidas = candidatas(pool, n)
    if not escolhidas:
        telegram.para_dono("📋 Conferência da semana: nenhuma oferta elegível no momento.")
        return
    telegram.para_dono(
        f"📋 Conferência da semana: {len(escolhidas)} produtos que devem ir ao ar nos próximos dias.\n\n"
        "Em cada um, compare a FOTO DO ANÚNCIO (esquerda) com a ARTE FINAL (direita):\n"
        "1. o produto aparece inteiro?\n2. é o mesmo item (kit não pode virar unidade)?\n"
        "3. preço e estoque continuam valendo? (toque no link)\n\n"
        "⚠️ = precisa de atenção. Para trocar a foto, mude a coluna imagem_url no data/curadoria.csv.")
    contagem = {"ok": 0, "aviso": 0, "erro": 0}
    atencao: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for o in escolhidas:
            try:
                original, arte, avisos = avaliar(o)
                img = painel(o, original, arte, avisos)
                arq = Path(tmp) / f"{slugify(o['id'], 40)}.jpg"
                img.save(arq, quality=88)
                _enviar_foto(arq, _legenda(o, avisos))
                g = _gravidade(avisos)
            except Exception as e:  # noqa: BLE001 - um produto com problema não derruba os outros
                log.warning("conferência de %s falhou: %s", o.get("id"), e)
                telegram.para_dono(f"❌ {_numero(o)} · {o['titulo'][:80]}\nNão consegui montar a prévia ({type(e).__name__}: {e}).")
                g = "erro"
            contagem[g] += 1
            if g != "ok":
                atencao.append(_numero(o))
    resumo = (f"✅ {contagem['ok']} sem alerta automático · ⚠️ {contagem['aviso']} com aviso · ❌ {contagem['erro']} com erro")
    if atencao:
        resumo += "\nPrecisam de atenção: " + ", ".join(atencao)
    telegram.para_dono("📋 Fim da conferência.\n" + resumo)
