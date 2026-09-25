"""Agente 1 — Caçador de Ofertas.

Busca produtos na Shopee (API oficial de afiliados), lê a curadoria manual (Amazon / Mercado Livre)
e mantém um estoque de ofertas candidatas em data/ofertas.json.
"""
from __future__ import annotations

import csv
import datetime as dt
import random
import zlib

from .. import mock
from ..core import DATA_DIR, cfg, dry_run, get_logger, load_json, now, save_json, tema_atual
from ..shopee import Shopee, normalize

log = get_logger("cacador")
POOL = "ofertas.json"
MAX_IDADE_H = 36  # oferta coletada há mais tempo que isso é descartada (preço pode ter mudado)


def _palavras_do_dia() -> list[str]:
    n = cfg("nicho")
    tema = tema_atual()
    base = list(n["palavras_chave"])
    random.shuffle(base)
    escolhidas = list(tema.get("palavras_chave") or [])[:2]
    for p in base:
        if len(escolhidas) >= n.get("palavras_por_execucao", 3):
            break
        if p not in escolhidas:
            escolhidas.append(p)
    return escolhidas


def _curadoria() -> list[dict]:
    path = DATA_DIR / "curadoria.csv"
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f)):
            if (row.get("ativo") or "").strip().lower() not in ("sim", "s", "1", "true"):
                continue
            if "SEU-LINK" in (row.get("link") or ""):
                continue

            def num(k):
                v = (row.get(k) or "").replace("R$", "").replace(".", "").replace(",", ".").strip()
                try:
                    return float(v) if v else None
                except ValueError:
                    return None

            plat = (row.get("plataforma") or "outro").strip().lower()
            preco, de = num("preco"), num("preco_de")
            out.append({
                "id": f"{plat}:cur{i}:{zlib.crc32(row['link'].encode())}",
                "plataforma": plat,
                "item_id": f"cur{i}",
                "titulo": row["titulo"].strip(),
                # Regra Amazon: preço só pode aparecer se vier da API/SiteStripe com data e hora.
                # Na curadoria manual da Amazon, a Ghost não mostra preço.
                "preco": None if plat == "amazon" else preco,
                "preco_de": None if plat == "amazon" else de,
                "desconto_pct": int(round((1 - preco / de) * 100)) if preco and de and plat != "amazon" else 0,
                "comissao_pct": 8.0 if plat == "amazon" else 12.0,
                "comissao_valor": (preco or 60) * 0.08,
                "vendas": 1000,
                "nota": 4.8,
                "imagem_url": (row.get("imagem_url") or "").strip() or None,
                "link": row["link"].strip(),
                "loja": plat.capitalize(),
                "fonte": "curadoria",
                "nota_chef": (row.get("nota_chef") or "").strip(),
                "keyword": "",
            })
    return out


def run() -> list[dict]:
    pool = {o["id"]: o for o in load_json(POOL, [])}
    agora = now()
    # remove ofertas velhas
    for k in list(pool):
        coletado = dt.datetime.fromisoformat(pool[k]["coletado_em"])
        if (agora - coletado).total_seconds() > MAX_IDADE_H * 3600:
            pool.pop(k)

    novas: list[dict] = []
    shopee = Shopee()
    n = cfg("nicho")
    if shopee.configured and not dry_run():
        for kw in _palavras_do_dia():
            try:
                nodes = shopee.search(kw, limit=n.get("resultados_por_palavra", 20), sort_type=2)
                nodes += shopee.search(kw, limit=10, sort_type=5)
                novas += [normalize(x, kw) for x in nodes]
                log.info("'%s': %d produtos", kw, len(nodes))
            except Exception as e:  # noqa: BLE001
                log.error("busca '%s' falhou: %s", kw, e)
    elif dry_run():
        log.info("DRY_RUN: usando ofertas de exemplo")
        novas += mock.ofertas(_palavras_do_dia())
    else:
        # Em produção NUNCA usa ofertas de exemplo: sem a API da Shopee, só entra a curadoria manual.
        log.warning("API da Shopee ainda não configurada: usando só data/curadoria.csv")

    novas += _curadoria()
    for o in novas:
        o["coletado_em"] = agora.isoformat()
        o.setdefault("links", {})
        antigo = pool.get(o["id"])
        if antigo:
            o["links"] = antigo.get("links", {})  # preserva links curtos já gerados
            o["numero"] = antigo.get("numero")
        pool[o["id"]] = o

    save_json(POOL, list(pool.values()))
    log.info("pool de ofertas: %d (novas nesta rodada: %d)", len(pool), len(novas))
    return list(pool.values())
