"""Símbolo da marca (lupa com chapéu de chef) desenhado em código.

Gera o logo, a imagem de compartilhamento (og.png) e o ícone da aba (favicon.png) da vitrine,
sem precisar subir arquivos binários no repositório.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..core import FONTS_DIR, cfg

SS = 4  # desenha 4x maior e reduz: bordas suaves


def _cores():
    c = cfg("marca")["cores"]
    return c["primaria"], c["fundo"]


def _font(nome: str, size: int):
    try:
        return ImageFont.truetype(str(FONTS_DIR / nome), size)
    except OSError:
        return ImageFont.load_default()


def chapeu(d: ImageDraw.ImageDraw, cx: float, cy: float, s: float, cor: str) -> None:
    r = s * 0.17
    for dx, dy in ((-0.2, 0.0), (0.0, -0.1), (0.2, 0.0)):
        d.ellipse([cx + dx * s - r, cy + dy * s - r - s * 0.08, cx + dx * s + r, cy + dy * s + r - s * 0.08], fill=cor)
    d.rectangle([cx - s * 0.26, cy - s * 0.08, cx + s * 0.26, cy + s * 0.2], fill=cor)
    d.rounded_rectangle([cx - s * 0.28, cy + s * 0.2, cx + s * 0.28, cy + s * 0.32], radius=s * 0.03, fill=cor)


def lupa(d: ImageDraw.ImageDraw, cx: float, cy: float, s: float, cor: str, fundo: str) -> None:
    lx, ly, lr = cx - s * 0.08, cy - s * 0.08, s * 0.30
    d.line([(lx + lr * 0.72, ly + lr * 0.72), (cx + s * 0.38, cy + s * 0.38)], fill=cor, width=int(s * 0.12))
    d.ellipse([cx + s * 0.32, cy + s * 0.32, cx + s * 0.44, cy + s * 0.44], fill=cor)
    d.ellipse([lx - lr, ly - lr, lx + lr, ly + lr], fill=fundo, outline=cor, width=int(s * 0.075))
    chapeu(d, lx, ly + s * 0.01, s * 0.42, cor)


def avatar(tamanho: int = 1080) -> Image.Image:
    vermelho, creme = _cores()
    W = tamanho * SS
    img = Image.new("RGB", (W, W), vermelho)
    lupa(ImageDraw.Draw(img), W * 0.49, W * 0.49, W * 0.70, creme, vermelho)
    return img.resize((tamanho, tamanho), Image.LANCZOS)


def og(nome: str, slogan: str) -> Image.Image:
    """Imagem de compartilhamento 1200x630 (WhatsApp, Telegram, redes)."""
    vermelho, creme = _cores()
    W, H = 1200 * SS, 630 * SS
    img = Image.new("RGB", (W, H), vermelho)
    d = ImageDraw.Draw(img)
    d.ellipse([90 * SS, 150 * SS, 420 * SS, 480 * SS], fill=creme)
    lupa(d, 255 * SS, 315 * SS, 250 * SS, vermelho, creme)
    fb, fm = _font("Poppins-Bold.ttf", 70 * SS), _font("Poppins-Medium.ttf", 34 * SS)
    d.text((480 * SS, 250 * SS), nome, font=fb, fill=creme, anchor="lm")
    # quebra o slogan em até 2 linhas
    linhas, atual = [], ""
    for p in slogan.split():
        t = f"{atual} {p}".strip()
        if d.textlength(t, font=fm) <= 660 * SS:
            atual = t
        else:
            linhas.append(atual)
            atual = p
    linhas.append(atual)
    for i, ln in enumerate(linhas[:2]):
        d.text((480 * SS, (335 + i * 45) * SS), ln, font=fm, fill=creme, anchor="lm")
    return img.resize((1200, 630), Image.LANCZOS)


def gerar_arquivos(pasta: Path) -> None:
    """Cria og.png, favicon.png e logo.png na pasta da vitrine (sempre refeitos: seguem o slogan atual)."""
    m = cfg("marca")
    og(m["nome"], m["slogan"]).save(pasta / "og.png", optimize=True)
    avatar(256).resize((64, 64), Image.LANCZOS).save(pasta / "favicon.png")
    avatar(160).save(pasta / "logo.png", optimize=True)
