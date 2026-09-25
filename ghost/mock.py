"""Ofertas de exemplo para testar o pipeline inteiro sem nenhuma credencial (DRY_RUN=1)."""
from __future__ import annotations

import random
import zlib

from PIL import Image, ImageDraw, ImageFilter

from .core import OUT_DIR

CATALOGO = [
    ("Termômetro Culinário Digital Espeto À Prova D'água Cozinha Churrasco", 34.90, 59.90),
    ("Balança De Cozinha Digital 10kg Alta Precisão Inox", 27.50, 45.00),
    ("Kit 6 Potes Herméticos Vidro Tampa Bambu Organizador", 89.90, 139.90),
    ("Mixer De Mão 3 em 1 Inox 800W Com Batedor e Triturador", 129.90, 199.90),
    ("Afiador De Facas Profissional 3 Estágios Diamante Cerâmica", 24.90, 49.90),
    ("Espátula De Silicone Kit 5 Peças Resistente 230°C", 19.90, 32.90),
    ("Forma De Silicone Para Air Fryer Reutilizável 20cm", 16.90, 29.90),
    ("Frigideira De Ferro Fundido 26cm Pré-Curada", 99.90, 149.90),
    ("Porta Temperos Giratório 16 Potes Inox", 69.90, 119.90),
    ("Fatiador De Legumes Mandoline Ajustável 5 Lâminas", 39.90, 79.90),
]

CORES = ["#E4572E", "#3A7D44", "#2E86AB", "#F6AE2D", "#8E5572", "#33658A"]


def _imagem(nome: str, seed: int) -> str:
    """Desenha uma 'foto de produto' falsa só para os testes visuais."""
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"mock_{seed}.jpg"
    if path.exists():
        return str(path)
    rnd = random.Random(seed)
    img = Image.new("RGB", (800, 800), "#F2F2F2")
    d = ImageDraw.Draw(img)
    cor = rnd.choice(CORES)
    for _ in range(3):
        x, y = rnd.randint(150, 450), rnd.randint(150, 450)
        w, h = rnd.randint(150, 320), rnd.randint(150, 320)
        forma = rnd.choice(["ellipse", "rounded"])
        if forma == "ellipse":
            d.ellipse([x, y, x + w, y + h], fill=cor, outline="#1F1A17", width=6)
        else:
            d.rounded_rectangle([x, y, x + w, y + h], radius=40, fill=cor, outline="#1F1A17", width=6)
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    img.save(path, quality=90)
    return str(path)


def ofertas(palavras: list[str]) -> list[dict]:
    out = []
    for i, (titulo, preco, de) in enumerate(CATALOGO):
        seed = zlib.crc32(titulo.encode()) % 10**6
        out.append({
            "id": f"shopee:mock{seed}",
            "plataforma": "shopee",
            "item_id": f"mock{seed}",
            "titulo": titulo,
            "preco": preco,
            "preco_de": de,
            "desconto_pct": int(round((1 - preco / de) * 100)),
            "comissao_pct": [6.0, 8.0, 10.0, 12.0][i % 4],
            "comissao_valor": round(preco * [0.06, 0.08, 0.10, 0.12][i % 4], 2),
            "vendas": [320, 1500, 5400, 880, 12000][i % 5],
            "nota": [4.7, 4.8, 4.9][i % 3],
            "imagem_url": None,
            "imagem_local": _imagem(titulo, seed),
            "link": f"https://s.shopee.com.br/exemplo{seed}",
            "loja": "Loja Exemplo",
            "fonte": "mock",
            "keyword": palavras[i % len(palavras)] if palavras else "",
        })
    return out
