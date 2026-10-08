"""Cliente da API de Afiliados da Lomadee (REST).

Autenticação: header  x-api-key: <LOMADEE_API_KEY>   (chave de afiliado, escopos products:read e shortener:write)
Docs: https://docs.lomadee.com.br/api-reference/introduction.md

- buscar(palavra)  -> lista de ofertas já no formato do pool da Ghost
- short_link(o)    -> link curto COM rastreio de afiliado (só é chamado para ofertas que serão publicadas)
"""
from __future__ import annotations

import re

from .core import cfg, env, get_logger, http, retry

log = get_logger("lomadee")
BASE = "https://api.lomadee.com.br"
COMISSAO_PADRAO_PCT = 5.0  # a API não informa comissão; valor só entra na nota de ranking


class LomadeeError(RuntimeError):
    pass


def configured() -> bool:
    return bool(env("LOMADEE_API_KEY"))


def _headers() -> dict:
    return {"x-api-key": env("LOMADEE_API_KEY") or "", "Accept": "application/json"}


def _check(r, what: str):
    if r.status_code == 401:
        raise LomadeeError(f"{what}: 401 chave ausente/inválida ou de tipo errado (precisa ser chave de AFILIADO)")
    if r.status_code == 403:
        raise LomadeeError(f"{what}: 403 chave sem o escopo necessário")
    if r.status_code == 429:
        raise LomadeeError(f"{what}: 429 limite de 60 req/min excedido (Retry-After={r.headers.get('Retry-After')})")
    if not r.ok:
        raise LomadeeError(f"{what}: HTTP {r.status_code} {r.text[:200]}")
    return r.json()


def _produtos(search: str, limit: int, page: int = 1) -> list[dict]:
    f = cfg("nicho")["filtros"]
    params = {
        "search": search,
        "limit": max(1, min(limit, 100)),
        "page": page,
        "isAvailable": "true",
        # faixa de preço em centavos, formato from:to
        "price": f"{int(f['preco_min'] * 100)}:{int(f['preco_max'] * 100)}",
    }

    def go():
        r = http().get(f"{BASE}/affiliate/products", headers=_headers(), params=params, timeout=30)
        return _check(r, "Lomadee produtos")

    d = retry(go, tries=3, what="Lomadee produtos")
    return d.get("data") or []


def _normalizar(p: dict, kw: str) -> dict | None:
    if p.get("available") is False:
        return None
    opcoes = p.get("options") or []
    preco = lista = None
    for op in opcoes:
        if op.get("available") is False:
            continue
        for pr in op.get("pricing") or []:
            v = pr.get("price")
            if v:
                preco, lista = v / 100, (pr.get("listPrice") or 0) / 100
                break
        if preco:
            break
    if not preco:
        return None
    img = ((p.get("images") or [{}])[0].get("url")
           or next((i.get("url") for op in opcoes for i in (op.get("images") or []) if i.get("url")), None))
    nome = re.sub(r"\s+", " ", (p.get("name") or "")).strip()
    url = (p.get("url") or "").strip()
    if not (img and nome and url.startswith("http")):
        return None
    de = lista if lista and lista > preco else None
    return {
        "id": f"lomadee:{p['id']}",
        "plataforma": "lomadee",
        "item_id": str(p["id"]),
        "titulo": nome[:110],
        "preco": round(preco, 2),
        "preco_de": round(de, 2) if de else None,
        "desconto_pct": int(round((1 - preco / de) * 100)) if de else 0,
        "comissao_pct": COMISSAO_PADRAO_PCT,
        "comissao_valor": round(preco * COMISSAO_PADRAO_PCT / 100, 2),
        "vendas": None,
        "nota": None,
        "imagem_url": img,
        "link": url,
        "link_produto": url,
        "organization_id": p.get("organizationId"),
        "loja": "Loja parceira",
        # "curadoria" = o curador não exige nota/vendas (a API da Lomadee não informa)
        "fonte": "curadoria",
        "nota_chef": "",
        "keyword": kw,
    }


def buscar(kw: str, limite: int | None = None) -> list[dict]:
    limite = limite or cfg("nicho").get("resultados_por_palavra", 20)
    brutos = _produtos(kw, limite)
    vistos, out = set(), []
    for p in brutos:
        o = _normalizar(p, kw)
        if not o:
            continue
        chave = re.sub(r"[^a-z0-9]", "", o["titulo"].lower())[:40]
        if chave in vistos:
            continue
        vistos.add(chave)
        out.append(o)
    log.info("'%s': %d produtos (%d aproveitados)", kw, len(brutos), len(out))
    return out


def short_link(o: dict) -> str:
    """Link curto rastreado (type=Custom). Usa a URL da página do produto."""
    body = {"organizationId": o["organization_id"], "type": "Custom", "url": o["link_produto"]}

    def go():
        r = http().post(f"{BASE}/affiliate/shortener/url", headers={**_headers(), "Content-Type": "application/json"},
                        json=body, timeout=30)
        return _check(r, "Lomadee encurtador")

    d = retry(go, tries=2, what="Lomadee encurtador")
    for canal in d if isinstance(d, list) else [d]:
        if canal.get("message"):
            log.warning("Lomadee bloqueou a geração do link: %s", canal["message"])
        urls = canal.get("shortUrls") or []
        if urls:
            return urls[0]
    raise LomadeeError("encurtador não devolveu link (shortUrls vazio)")
