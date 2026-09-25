"""Agente 7 — Vitrine (o "link na bio").

Gera docs/index.html, uma página leve com as ofertas numeradas (#123) que o Instagram cita.
Publicada de graça pelo GitHub Pages.
"""
from __future__ import annotations

import html
import json

from ..core import DOCS_DIR, brl, cfg, env, get_logger, load_json, now, save_json
from . import redator

log = get_logger("vitrine")
ARQ = "vitrine.json"


def adicionar(o: dict, url: str) -> None:
    """A vitrine usa a foto do próprio marketplace (CDN), que não some quando o site é republicado."""
    imagem_rel = o.get("imagem_url") if str(o.get("imagem_url", "")).startswith("http") else "placeholder.jpg"
    itens = [i for i in load_json(ARQ, []) if i["numero"] != o.get("numero")]
    itens.insert(0, {
        "numero": o.get("numero"),
        "titulo": redator.copy(o)["titulo_curto"],
        "preco": o.get("preco"),
        "preco_de": o.get("preco_de"),
        "plataforma": o["plataforma"],
        "imagem": imagem_rel,
        "link": url,
        "atualizado": now().strftime("%d/%m/%Y %H:%M"),
    })
    save_json(ARQ, itens[: cfg("canais")["vitrine"].get("max_ofertas", 60)])


CSS = """
:root{--p:%(primaria)s;--f:%(fundo)s;--t:%(texto)s;--d:%(destaque)s}
*{box-sizing:border-box}body{margin:0;background:var(--f);color:var(--t);font-family:Poppins,system-ui,sans-serif}
header{background:var(--t);color:var(--f);padding:22px 16px;text-align:center}
header h1{margin:0;font-size:22px;letter-spacing:.5px}header p{margin:6px 0 0;font-size:13px;opacity:.8}
.cta{display:block;margin:14px auto 0;max-width:420px;background:var(--p);color:#fff;text-decoration:none;
padding:12px;border-radius:12px;font-weight:600}
.busca{max-width:640px;margin:16px auto;padding:0 16px}
.busca input{width:100%%;padding:14px;border-radius:12px;border:2px solid #e5dccf;font-size:16px}
main{max-width:640px;margin:0 auto;padding:0 16px 40px;display:grid;gap:12px}
.card{display:flex;gap:12px;background:#fff;border-radius:16px;padding:10px;text-decoration:none;color:inherit;
box-shadow:0 2px 10px rgba(0,0,0,.06)}
.card img{width:96px;height:120px;object-fit:cover;border-radius:10px;flex-shrink:0}
.n{font-weight:700;color:var(--p);font-size:14px}.t{font-weight:600;margin:2px 0 6px;line-height:1.25}
.pr{font-weight:700}.de{text-decoration:line-through;opacity:.5;font-size:13px;margin-right:6px}
.info{font-size:11px;opacity:.6;margin-top:4px}.bt{display:inline-block;margin-top:6px;background:var(--d);color:#fff;
padding:6px 12px;border-radius:8px;font-size:13px;font-weight:600}
footer{font-size:12px;opacity:.7;text-align:center;padding:0 16px 30px;max-width:640px;margin:0 auto}
"""


def gerar() -> None:
    m = cfg("marca")
    itens = load_json(ARQ, [])
    tg = env("TELEGRAM_CHANNEL_URL") or cfg("canais").get("telegram", {}).get("url", "")
    e = html.escape
    cards = []
    for i in itens:
        preco = ""
        if i.get("preco"):
            de = f'<span class="de">{brl(i["preco_de"])}</span>' if i.get("preco_de") else ""
            preco = f'<div>{de}<span class="pr">{brl(i["preco"])}</span></div>' \
                    f'<div class="info">Preço em {e(i["atualizado"])}, pode mudar.</div>'
        else:
            preco = '<div class="info">Confira o preço atualizado no site.</div>'
        cards.append(
            f'<a class="card" data-n="{i["numero"]}" href="{e(i["link"])}" target="_blank" rel="sponsored noopener">'
            f'<img src="{e(i["imagem"])}" alt="" loading="lazy"><div><div class="n">#{i["numero"]} · '
            f'{e(i["plataforma"].capitalize())}</div><div class="t">{e(i["titulo"])}</div>{preco}'
            f'<span class="bt">Ver oferta</span></div></a>'
        )
    pagina = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(m['nome'])} | Achados</title>
<meta name="description" content="{e(m['slogan'])}"><link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700&display=swap" rel="stylesheet">
<style>{CSS % m['cores']}</style></head><body>
<header><h1>{e(m['nome'])}</h1><p>{e(m['slogan'])}</p>
{f'<a class="cta" href="{e(tg)}">📲 Ofertas todo dia no Telegram</a>' if tg else ''}</header>
<div class="busca"><input id="q" inputmode="numeric" placeholder="Digite o número da oferta (ex.: 12)"></div>
<main id="lista">{''.join(cards) or '<p>As primeiras ofertas chegam em breve.</p>'}</main>
<footer>{e(m['aviso_afiliado'])} {e(m['aviso_preco'])}<br>Atualizado em {now().strftime('%d/%m/%Y %H:%M')}.</footer>
<script>
const q=document.getElementById('q');q.addEventListener('input',()=>{{const v=q.value.replace(/\\D/g,'');
document.querySelectorAll('.card').forEach(c=>{{c.style.display=!v||c.dataset.n===v?'flex':'none'}})}});
const h=location.hash.replace('#','');if(h){{q.value=h;q.dispatchEvent(new Event('input'))}}
</script></body></html>"""
    DOCS_DIR.mkdir(exist_ok=True)
    (DOCS_DIR / "index.html").write_text(pagina, encoding="utf-8")
    (DOCS_DIR / ".nojekyll").write_text("")
    if not (DOCS_DIR / "placeholder.jpg").exists():
        from .designer import imagem_produto
        imagem_produto({"id": "placeholder"}).resize((400, 400)).save(DOCS_DIR / "placeholder.jpg", quality=85)
    (DOCS_DIR / "ofertas.json").write_text(json.dumps(itens, ensure_ascii=False), encoding="utf-8")
    log.info("vitrine gerada com %d ofertas", len(itens))
