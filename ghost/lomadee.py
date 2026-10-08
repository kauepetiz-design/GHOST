"""Cliente da API de Afiliados da Lomadee (REST).

Autenticação: header  x-api-key: <LOMADEE_API_KEY>   (chave de afiliado, escopos products:read, brands:read e shortener:write)
Docs: https://docs.lomadee.com.br/api-reference/introduction.md

- buscar(palavra)  -> lista de ofertas já no formato do pool da Ghost
- short_link(o)    -> link curto COM rastreio de afiliado (só é chamado para ofertas que serão publicadas)

Só entram produtos das lojas aprovadas (LOJAS_APROVADAS ou secret/variável LOMADEE_LOJAS, separadas por vírgula)
e que sejam de cozinha/casa (lista de termos abaixo).
"""
from __future__ import annotations

import re
import unicodedata

from .core import cfg, env, get_logger, http, retry

log = get_logger("lomadee")
BASE = "https://api.lomadee.com.br"
COMISSAO_PADRAO_PCT = 5.0  # a API não informa comissão; valor só entra na nota de ranking
LOJAS_APROVADAS = ["Shopee", "Mundo Vem", "Desconto Aqui"]
_ORGS: dict[str, str] | None = None  # id -> nome (cache por execução)

# O produto só entra se o título tiver ao menos um destes termos (palavra inteira, singular ou plural).
TERMOS_COZINHA = [
    "panela", "frigideira", "cacarola", "assadeira", "forma", "tabua", "faca", "talher", "faqueiro", "colher",
    "concha", "espatula", "escumadeira", "pegador", "batedor", "fouet", "peneira", "ralador", "descascador",
    "abridor", "copo", "taca", "xicara", "caneca", "prato", "tigela", "bowl", "travessa", "jarra", "garrafa",
    "pote", "marmita", "lancheira", "organizador", "porta tempero", "porta condimento", "escorredor", "pano de prato",
    "avental", "luva termica", "cafeteira", "chaleira", "liquidificador", "mixer", "processador", "batedeira",
    "air fryer", "fritadeira", "sanduicheira", "grill", "torradeira", "balanca de cozinha", "termometro culinario",
    "galheiro", "saleiro", "açucareiro", "acucareiro", "moedor", "fatiador", "cortador", "utensilio", "cozinha",
    "jogo americano", "descanso de panela", "formas", "forminha", "dispenser", "chapa", "espremedor", "garrafa termica",
    "caixa organizadora", "lixeira", "escorredor de louca", "pia", "tabua de corte", "kit churrasco", "churrasco",
]
BLOQUEIO_EXTRA = ["skincare", "cicatriz", "creme", "gel ", "pele", "cabelo", "batom", "maquiagem", "perfume",
                  "shampoo", "suplemento", "vitamina", "capinha", "celular", "pet ", "cachorro", "gato "]


# Buscas amplas que se alternam a cada rodada (as palavras do nicho são específicas demais para o catálogo das lojas)
BUSCAS_GERAIS = [
    "panela", "frigideira", "faca", "tábua", "assadeira", "forma", "utensílio cozinha", "colher", "espátula", "tigela",
    "copo", "taça", "jarra", "garrafa", "pote", "marmita", "lancheira", "organizador cozinha", "escorredor", "peneira",
    "ralador", "batedor", "avental", "pano de prato", "talher", "prato", "travessa", "cafeteira", "chaleira", "balança",
]
_CHAMADAS = {"n": 0}


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


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


def _lojas() -> dict[str, str]:
    """Resolve os IDs das lojas aprovadas pelo nome (GET /affiliate/brands?search=...)."""
    global _ORGS
    if _ORGS is not None:
        return _ORGS
    nomes = [n.strip() for n in (env("LOMADEE_LOJAS") or "").split(",") if n.strip()] or LOJAS_APROVADAS
    achadas: dict[str, str] = {}
    for nome in nomes:
        def go(nome=nome):
            r = http().get(f"{BASE}/affiliate/brands", headers=_headers(),
                           params={"search": nome, "limit": 20, "page": 1}, timeout=30)
            return _check(r, "Lomadee marcas")

        d = retry(go, tries=3, what="Lomadee marcas")
        alvo = _norm(nome)
        for b in d.get("data") or []:
            n = _norm(b.get("name", ""))
            if n == alvo or n.startswith(alvo):
                achadas[b["id"]] = b.get("name", nome)
        if not any(_norm(v).startswith(alvo) for v in achadas.values()):
            log.warning("loja '%s' não encontrada na Lomadee (confira o nome exato no painel)", nome)
    log.info("lojas Lomadee usadas: %s", ", ".join(sorted(achadas.values())) or "nenhuma")
    _ORGS = achadas
    return achadas


def _produtos(search: str, limit: int, page: int = 1) -> list[dict]:
    orgs = _lojas()
    if not orgs:
        return []
    params = {
        "search": search,
        "limit": max(1, min(limit, 100)),
        "page": page,
        "isAvailable": "true",
        "organizationIds": ",".join(orgs),
    }

    def go():
        r = http().get(f"{BASE}/affiliate/products", headers=_headers(), params=params, timeout=30)
        return _check(r, "Lomadee produtos")

    d = retry(go, tries=3, what="Lomadee produtos")
    return d.get("data") or []


def _da_cozinha(titulo: str) -> bool:
    t = " " + _norm(titulo) + " "
    if any(b in t for b in BLOQUEIO_EXTRA):
        return False
    bloq = [str(b).lower() for b in (cfg("nicho").get("bloqueio") or [])]
    if any(b in titulo.lower() for b in bloq):
        return False
    return any(re.search(rf"\b{re.escape(term)}s?\b", t) for term in map(_norm, TERMOS_COZINHA))


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
                # Observado na prática: a API devolve o valor em REAIS (a doc diz centavos, mas não bate).
                preco, lista = float(v), float(pr.get("listPrice") or 0)
                break
        if preco:
            break
    f = cfg("nicho")["filtros"]
    if not preco or not (f["preco_min"] <= preco <= f["preco_max"]):
        return None
    img = ((p.get("images") or [{}])[0].get("url")
           or next((i.get("url") for op in opcoes for i in (op.get("images") or []) if i.get("url")), None))
    nome = re.sub(r"\s+", " ", (p.get("name") or "")).strip()
    url = (p.get("url") or "").strip()
    if not (img and nome and url.startswith("http")) or not _da_cozinha(nome):
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


def buscar(kw: str, limite: int = 100) -> list[dict]:
    """Busca a palavra do nicho + uma busca ampla que muda a cada chamada."""
    import time

    geral = BUSCAS_GERAIS[(int(time.time() // 3600) * 3 + _CHAMADAS["n"]) % len(BUSCAS_GERAIS)]
    _CHAMADAS["n"] += 1
    vistos_ids, out = set(), []
    for termo in dict.fromkeys([kw, geral]):
        for o in _buscar_um(termo, limite):
            if o["id"] not in vistos_ids:
                vistos_ids.add(o["id"])
                out.append(o)
    return out


def _buscar_um(kw: str, limite: int) -> list[dict]:
    brutos = _produtos(kw, limite)
    if brutos:
        try:
            pr0 = (brutos[0].get("options") or [{}])[0].get("pricing") or [{}]
            log.info("amostra de preço bruto: %s -> %r", (brutos[0].get("name") or "")[:40], pr0[0].get("price"))
        except Exception:  # noqa: BLE001
            pass
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
