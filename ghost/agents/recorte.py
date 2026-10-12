"""Recorte de produto: tira o fundo da foto e devolve a imagem com transparência (RGBA).

Duas formas (a segunda também serve de conferência da primeira):

1. IA (biblioteca `rembg`, grátis, modelo baixado do GitHub na primeira execução): contorno limpo, tira até sombras.
2. Branco ligado à borda (só Pillow): serve para fotos de fundo branco, como as do Mercado Livre.

TRAVA DE SEGURANÇA (modo auto): só recorta foto de estúdio, com a borda branca. Foto com cenário, selo ou infográfico
(que a IA recorta pela metade e mostraria um produto diferente do anunciado) NÃO é recortada: devolve None e o vídeo
usa a foto inteira num cartão branco. Se a IA recortar bem menos que o método do branco (perdeu peças), vale o do branco.

Config opcional em config/marca.yaml: video_recorte: auto | forcar | branco | off   (padrão: auto)
  forcar = usa a IA sem a trava (por sua conta e risco); branco = só o método do branco; off = nunca recorta.
"""
from __future__ import annotations

from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from ..core import OUT_DIR, cfg, get_logger, slugify

log = get_logger("recorte")
MODELO = "u2net"
LADO_MAX = 1200


# ------------------------------------------------------------------ 1) IA
@lru_cache(None)
def _sessao():
    from rembg import new_session

    return new_session(MODELO)


def _por_ia(img: Image.Image) -> Image.Image:
    from rembg import remove

    im = img.copy()
    im.thumbnail((LADO_MAX, LADO_MAX), Image.LANCZOS)
    return remove(im, session=_sessao()).convert("RGBA")


# ------------------------------------------------------------------ 2) branco ligado à borda (Pillow puro)
def _candidatos_brancos(img: Image.Image):
    pq = img.copy()
    pq.thumbnail((640, 640), Image.BILINEAR)  # a máscara é calculada pequena (rápido) e depois ampliada
    r, g, b = pq.split()
    menor = ImageChops.darker(ImageChops.darker(r, g), b)
    maior = ImageChops.lighter(ImageChops.lighter(r, g), b)
    claro = menor.point(lambda v: 255 if v >= 228 else 0)
    neutro = ImageChops.subtract(maior, menor).point(lambda v: 255 if v <= 22 else 0)
    cand = ImageChops.darker(claro, neutro)  # "quase branco e sem cor"
    w, h = cand.size
    borda = [(x, 0) for x in range(w)] + [(x, h - 1) for x in range(w)] + [(0, y) for y in range(h)] + [(w - 1, y) for y in range(h)]
    return cand, borda


def _borda_branca(img: Image.Image) -> float:
    """Fração da borda da foto que é branca e sem cor (foto de estúdio ~ 1.0; cenário/infográfico bem menos)."""
    cand, borda = _candidatos_brancos(img.convert("RGB"))
    return sum(1 for p in borda if cand.getpixel(p) == 255) / len(borda)


def _por_branco(img: Image.Image) -> Image.Image:
    im = img.copy()
    im.thumbnail((LADO_MAX, LADO_MAX), Image.LANCZOS)
    cand, borda = _candidatos_brancos(im)
    brancos = sum(1 for p in borda if cand.getpixel(p) == 255)
    if brancos / len(borda) < 0.85:
        raise ValueError("a borda da foto não é branca")
    w, h = cand.size
    preenchido = cand.copy()
    for p in borda:
        if preenchido.getpixel(p) == 255:
            ImageDraw.floodfill(preenchido, p, 128)
    fundo = preenchido.point(lambda v: 255 if v == 128 else 0)
    alfa = ImageChops.invert(fundo).resize(im.size, Image.LANCZOS).filter(ImageFilter.GaussianBlur(1.3))
    alfa = alfa.point(lambda v: 0 if v < 70 else 255 if v > 185 else int((v - 70) * 255 / 115))
    out = im.convert("RGBA")
    out.putalpha(alfa)
    return out


# ------------------------------------------------------------------ checagem e acabamento
def _acabar(rgba: Image.Image) -> Image.Image | None:
    alfa = rgba.getchannel("A")
    caixa = alfa.point(lambda v: 255 if v > 40 else 0).getbbox()
    if not caixa:
        return None
    cobre = sum(alfa.resize((64, 64), Image.BILINEAR).getdata()) / (64 * 64 * 255)
    if cobre < 0.03 or cobre > 0.93:  # sumiu tudo ou não tirou fundo nenhum
        return None
    alfa = alfa.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.9))  # tira a franja clara da borda
    rgba = rgba.copy()
    rgba.putalpha(alfa)
    return rgba.crop(caixa)


def _area(rgba: Image.Image) -> float:
    return sum(rgba.getchannel("A").resize((96, 96), Image.BILINEAR).getdata()) / 255


def recortar(img: Image.Image, chave: str | None = None) -> Image.Image | None:
    modo = str(cfg("marca").get("video_recorte", "auto")).lower()
    if modo == "off":
        return None
    cache = None
    if chave:
        cache = OUT_DIR / "img" / f"{slugify(chave, 60)}-recorte.png"
        if cache.exists():
            return Image.open(cache).convert("RGBA")
    img = img.convert("RGB")
    if modo == "auto" and _borda_branca(img) < 0.85:
        log.warning("foto com cenário/selo/infográfico (borda não é branca): sem recorte, vai num cartão branco")
        return None
    res = None
    if modo == "branco":
        metodos = [("branco", _por_branco)]
    elif modo == "forcar":
        metodos = [("ia", _por_ia)]
    else:
        metodos = [("ia", _por_ia), ("branco", _por_branco)]
    achados: dict[str, Image.Image] = {}
    for nome, fn in metodos:
        try:
            r = _acabar(fn(img))
        except Exception as e:  # noqa: BLE001
            log.warning("recorte '%s' não rolou: %s", nome, e)
            continue
        if r is not None:
            achados[nome] = r
            if modo != "auto":
                break
    if modo == "auto" and "ia" in achados and "branco" in achados \
            and _area(achados["ia"]) < 0.75 * _area(achados["branco"]):
        log.warning("a IA recortou bem menos que o método do branco (perdeu peças?): usando o do branco")
        res = achados["branco"]
    else:
        res = achados.get("ia") or achados.get("branco")
    if res is None:
        log.warning("sem recorte; o produto vai num cartão branco")
        return None
    log.info("recorte feito: %dx%d", *res.size)
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        res.save(cache)
    return res
