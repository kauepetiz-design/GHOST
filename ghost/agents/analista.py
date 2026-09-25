"""Agente 8 — Analista.

Toda segunda-feira puxa as conversões da Shopee (por canal, via subId), cruza com o que foi
publicado e manda um relatório curto para o seu Telegram, com recomendações automáticas.
"""
from __future__ import annotations

import collections
import datetime as dt

from ..core import brl, cfg, dry_run, get_logger, load_json, now, save_json
from ..shopee import Shopee

log = get_logger("analista")


def _canal_de(utm: str | None) -> str:
    mapa = {v: k for k, v in cfg("canais").get("sub_ids", {}).items()}
    if not utm:
        return "sem_canal"
    return mapa.get(utm.split("-")[0], "outro")


def relatorio(dias: int = 7) -> str:
    fim = now()
    ini = fim - dt.timedelta(days=dias)
    hist = [h for h in load_json("publicados.json", []) if dt.datetime.fromisoformat(h["quando"]) >= ini]
    posts = collections.Counter(h["canal"] for h in hist)

    vendas = collections.Counter()
    comissao = collections.defaultdict(float)
    produtos = collections.Counter()
    shopee = Shopee()
    fonte = "simulação"
    if shopee.configured and not dry_run():
        try:
            conv = shopee.conversions(int(ini.timestamp()), int(fim.timestamp()))
            fonte = "Shopee API"
            for c in conv:
                canal = _canal_de(c.get("utmContent"))
                vendas[canal] += 1
                comissao[canal] += float(c.get("totalCommission") or 0)
                for o in c.get("orders") or []:
                    for it in o.get("items") or []:
                        produtos[it.get("itemName", "?")[:50]] += int(it.get("qty") or 1)
        except Exception as e:  # noqa: BLE001
            log.error("relatório Shopee falhou: %s", e)
            fonte = f"erro na Shopee API ({e})"

    total = sum(comissao.values())
    hist_m = load_json("metricas.json", [])
    anterior = hist_m[-1]["comissao_total"] if hist_m else None
    hist_m.append({"semana_fim": fim.date().isoformat(), "comissao_total": round(total, 2),
                   "vendas": sum(vendas.values()), "posts": dict(posts)})
    save_json("metricas.json", hist_m[-52:])

    linhas = [f"📊 <b>Relatório semanal Ghost</b> ({ini:%d/%m} a {fim:%d/%m})", f"Fonte: {fonte}", ""]
    linhas.append(f"💰 Comissão Shopee: <b>{brl(total)}</b> em {sum(vendas.values())} pedidos")
    if anterior is not None:
        delta = total - anterior
        linhas.append(f"{'📈' if delta >= 0 else '📉'} vs. semana anterior: {brl(delta)}")
    linhas += ["", "<b>Por canal</b> (posts → pedidos → comissão)"]
    for canal in sorted(set(posts) | set(vendas)):
        linhas.append(f"• {canal}: {posts.get(canal, 0)} → {vendas.get(canal, 0)} → {brl(comissao.get(canal, 0))}")
    if produtos:
        linhas += ["", "<b>Mais vendidos</b>"] + [f"• {n} ({q})" for n, q in produtos.most_common(5)]

    # recomendações simples e automáticas
    rec = []
    if total == 0 and sum(posts.values()) > 30:
        rec.append("Nenhuma venda ainda: é normal nas primeiras semanas. Foque em crescer o Telegram (divulgue o canal no Instagram e nos Stories).")
    melhor = max(comissao, key=comissao.get) if comissao else None
    if melhor:
        rec.append(f"O canal que mais rende é <b>{melhor}</b>. Vale reforçar a divulgação dele.")
    if produtos:
        top = produtos.most_common(1)[0][0]
        rec.append(f"Faça um vídeo gravado por você sobre \"{top}\": produto campeão com rosto humano vende mais.")
    rec.append("Amazon e Mercado Livre não têm API de relatório para afiliados: confira os painéis (2 min).")
    linhas += ["", "<b>Próximos passos</b>"] + [f"→ {r}" for r in rec]
    return "\n".join(linhas)
