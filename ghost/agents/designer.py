"""Agente 4 — Designer.

Cria as artes da marca com Pillow: post 1080x1350 (feed/Telegram/Threads), carrossel "Top 5"
e os quadros verticais 1080x1920 usados pelo Editor de Vídeo. Tudo salvo em docs/media/,
que o GitHub Pages publica — é assim que o Instagram consegue baixar a imagem.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from ..core import FONTS_DIR, MEDIA_DIR, OUT_DIR, brl, cfg, get_logger, http, now, public_base_url, slugify
from . import redator

log = get_logger("designer")


# ------------------------------------------------------------------ utilitários
def _font(tipo: str, size: int) -> ImageFont.FreeTypeFont:
    nome = cfg("marca")["fontes"][tipo]
    try:
        return ImageFont.truetype(str(FONTS_DIR / nome), size)
    except OSError:
        return ImageFont.truetype("DejaVuSans-Bold.ttf" if tipo == "titulo" else "DejaVuSans.ttf", size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    linhas, atual = [], ""
    for palavra in text.split():
        teste = f"{atual} {palavra}".strip()
        if draw.textlength(teste, font=font) <= max_w:
            atual = teste
        else:
            if atual:
                linhas.append(atual)
            atual = palavra
    if atual:
        linhas.append(atual)
    return linhas


def _fit(draw, text, tipo, max_w, max_linhas, start, minimo=28):
    size = start
    while size > minimo:
        f = _font(tipo, size)
        linhas = _wrap(draw, text, f, max_w)
        if len(linhas) <= max_linhas:
            return f, linhas
        size -= 4
    f = _font(tipo, minimo)
    linhas = _wrap(draw, text, f, max_w)[:max_linhas]
    if linhas:
        linhas[-1] = linhas[-1].rstrip(".,") + "…"
    return f, linhas


def _cor(nome: str) -> str:
    return cfg("marca")["cores"][nome]


def imagem_produto(o: dict) -> Image.Image:
    """Baixa (com cache) a foto do produto; sem foto, desenha um ícone neutro."""
    cache = OUT_DIR / "img" / f"{slugify(o['id'], 60)}.jpg"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if o.get("imagem_local") and Path(o["imagem_local"]).exists():
        return Image.open(o["imagem_local"]).convert("RGB")
    if cache.exists():
        return Image.open(cache).convert("RGB")
    if o.get("imagem_url"):
        try:
            r = http().get(o["imagem_url"], timeout=30)
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            img.save(cache, quality=92)
            return img
        except Exception as e:  # noqa: BLE001
            log.warning("não baixou imagem de %s: %s", o["id"], e)
    # ícone: prato com talheres
    img = Image.new("RGB", (800, 800), _cor("fundo"))
    d = ImageDraw.Draw(img)
    d.ellipse([170, 170, 630, 630], fill="white", outline=_cor("texto"), width=10)
    d.ellipse([260, 260, 540, 540], outline=_cor("primaria"), width=8)
    d.rounded_rectangle([90, 200, 115, 600], radius=12, fill=_cor("texto"))
    d.rounded_rectangle([685, 200, 710, 600], radius=12, fill=_cor("texto"))
    return img


def _cartao_produto(o: dict, w: int, h: int) -> Image.Image:
    foto = ImageOps.contain(imagem_produto(o), (w - 60, h - 60), Image.LANCZOS)
    card = Image.new("RGB", (w, h), "white")
    card.paste(foto, ((w - foto.width) // 2, (h - foto.height) // 2))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w, h], radius=48, fill=255)
    out = Image.new("RGBA", (w, h))
    out.paste(card, (0, 0), mask)
    return out


def _sombra(base: Image.Image, box, raio=48):
    sh = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(box, radius=raio, fill=(0, 0, 0, 60))
    sh = sh.filter(ImageFilter.GaussianBlur(18))
    base.alpha_composite(sh)


def _selo_desconto(d: ImageDraw.ImageDraw, cx: int, cy: int, pct: int, r: int = 95):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_cor("primaria"))
    f1, f2 = _font("titulo", 58), _font("medio", 30)
    d.text((cx, cy - 12), f"-{pct}%", font=f1, fill="white", anchor="mm")
    d.text((cx, cy + 38), "OFF", font=f2, fill="white", anchor="mm")


def _destino(o: dict | None, sufixo: str) -> Path:
    pasta = MEDIA_DIR / now().strftime("%Y-%m-%d")
    pasta.mkdir(parents=True, exist_ok=True)
    nome = f"{slugify(o['titulo'], 30)}-{o.get('numero', 0)}" if o else "capa"
    return pasta / f"{nome}-{sufixo}.jpg"


def url_publica(path: Path) -> str:
    rel = path.relative_to(MEDIA_DIR.parent).as_posix()
    base = public_base_url()
    return f"{base}/{rel}" if base else rel


# ------------------------------------------------------------------ peças
def post(o: dict, sufixo: str = "post") -> Path:
    """Arte 1080x1350 de uma oferta."""
    W, H = 1080, 1350
    c, m = redator.copy(o), cfg("marca")
    img = Image.new("RGBA", (W, H), _cor("fundo"))
    d = ImageDraw.Draw(img)

    # topo
    d.rectangle([0, 0, W, 110], fill=_cor("texto"))
    d.text((60, 55), m["nome"].upper(), font=_font("titulo", 40), fill=_cor("fundo"), anchor="lm")
    d.text((W - 60, 55), f"#{o.get('numero', '')}", font=_font("titulo", 40), fill=_cor("primaria"), anchor="rm")

    # gancho
    f, linhas = _fit(d, c["gancho"], "medio", W - 120, 2, 44)
    y = 150
    for ln in linhas:
        d.text((60, y), ln, font=f, fill=_cor("primaria"))
        y += f.size + 8

    # foto
    box = (90, 280, W - 90, 280 + 640)
    _sombra(img, box)
    img.alpha_composite(_cartao_produto(o, box[2] - box[0], box[3] - box[1]), (box[0], box[1]))
    if o.get("desconto_pct", 0) >= 10:
        _selo_desconto(d, W - 150, 320, o["desconto_pct"])

    # título do produto
    f, linhas = _fit(d, c["titulo_curto"], "titulo", W - 120, 2, 62, 40)
    y = 960
    for ln in linhas:
        d.text((60, y), ln, font=f, fill=_cor("texto"))
        y += f.size + 6

    # preço
    y = max(y + 20, 1110)
    if o.get("preco"):
        if o.get("preco_de") and o["preco_de"] > o["preco"]:
            fd = _font("texto", 36)
            txt = f"de {brl(o['preco_de'])}"
            d.text((60, y), txt, font=fd, fill="#8A817C")
            tw = d.textlength(txt, font=fd)
            d.line([(60 + d.textlength('de ', font=fd), y + 22), (60 + tw, y + 22)], fill="#8A817C", width=3)
            y += 48
        fp = _font("titulo", 72)
        preco = brl(o["preco"])
        pw = d.textlength(preco, font=fp)
        d.rounded_rectangle([50, y, 50 + pw + 50, y + 100], radius=26, fill=_cor("preco_fundo"))
        d.text((75, y + 50), preco, font=fp, fill="white", anchor="lm")
    else:
        fp = _font("titulo", 46)
        d.rounded_rectangle([50, y, 640, y + 90], radius=26, fill=_cor("preco_fundo"))
        d.text((80, y + 45), "Veja o preço no link", font=fp, fill="white", anchor="lm")

    # rodapé
    d.text((W - 60, H - 50), "link de afiliado · preço sujeito a alteração", font=_font("texto", 24),
           fill="#8A817C", anchor="rm")

    out = _destino(o, sufixo)
    img.convert("RGB").save(out, quality=90)
    return out


def capa_carrossel(n: int, titulo: str = "Top achados da semana") -> Path:
    W, H = 1080, 1350
    m = cfg("marca")
    img = Image.new("RGB", (W, H), _cor("primaria"))
    d = ImageDraw.Draw(img)
    d.text((W // 2, 300), m["nome"].upper(), font=_font("titulo", 44), fill=_cor("fundo"), anchor="mm")
    f, linhas = _fit(d, titulo, "titulo", W - 160, 3, 110, 60)
    y = 520
    for ln in linhas:
        d.text((W // 2, y), ln, font=f, fill="white", anchor="mm")
        y += f.size + 10
    d.text((W // 2, y + 80), f"{n} itens escolhidos por um chef", font=_font("medio", 44), fill=_cor("fundo"), anchor="mm")
    ft = _font("titulo", 48)
    tw = d.textlength("arraste", font=ft)
    x0 = W // 2 - (tw + 70) / 2
    d.text((x0, H - 140), "arraste", font=ft, fill="white", anchor="lm")
    ax = x0 + tw + 25
    d.polygon([(ax, H - 162), (ax + 42, H - 140), (ax, H - 118)], fill="white")
    out = _destino(None, f"carrossel-{now().strftime('%H%M')}")
    img.save(out, quality=90)
    return out


def carrossel(ofertas: list[dict]) -> list[Path]:
    return [capa_carrossel(len(ofertas))] + [post(o, "slide") for o in ofertas]


def quadro_vertical(o: dict, texto: str, cena: int, total: int, foto_zoom: float = 1.0) -> Image.Image:
    """Um quadro 1080x1920 para o vídeo (o Editor de Vídeo anima o zoom)."""
    W, H = 1080, 1920
    m = cfg("marca")
    img = Image.new("RGBA", (W, H), _cor("fundo"))
    d = ImageDraw.Draw(img)
    # foto grande com zoom (efeito Ken Burns)
    fw = int(900 * foto_zoom)
    foto = ImageOps.contain(imagem_produto(o), (fw, fw), Image.LANCZOS)
    card = Image.new("RGBA", (900, 900), "white")
    card.paste(foto, ((900 - foto.width) // 2, (900 - foto.height) // 2))
    mask = Image.new("L", (900, 900), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, 900, 900], radius=56, fill=255)
    img.paste(card, (90, 420), mask)
    # legenda (cena atual)
    f, linhas = _fit(d, texto, "titulo", W - 140, 4, 76, 44)
    bloco_h = len(linhas) * (f.size + 12) + 60
    y0 = 1400
    d.rounded_rectangle([50, y0, W - 50, y0 + bloco_h], radius=36, fill=_cor("texto"))
    y = y0 + 30
    for ln in linhas:
        d.text((W // 2, y + f.size // 2), ln, font=f, fill="white", anchor="mm")
        y += f.size + 12
    # topo
    d.text((W // 2, 230), m["nome"].upper(), font=_font("titulo", 46), fill=_cor("texto"), anchor="mm")
    if o.get("preco") and cena >= total - 2:
        preco = brl(o["preco"])
        fp = _font("titulo", 70)
        pw = d.textlength(preco, font=fp)
        d.rounded_rectangle([W // 2 - pw / 2 - 40, 300, W // 2 + pw / 2 + 40, 395], radius=30, fill=_cor("primaria"))
        d.text((W // 2, 348), preco, font=fp, fill="white", anchor="mm")
    # barra de progresso
    d.rectangle([0, H - 14, int(W * (cena + 1) / total), H], fill=_cor("primaria"))
    return img.convert("RGB")
