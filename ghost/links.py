"""Gera um link de afiliado por canal (subId) para o Analista saber de onde veio cada venda."""
from __future__ import annotations

from .core import cfg, dry_run, get_logger, load_json, save_json
from .shopee import Shopee

log = get_logger("links")
_shopee: Shopee | None = None


def link(o: dict, canal: str) -> str:
    global _shopee
    o.setdefault("links", {})
    if canal in o["links"]:
        return o["links"][canal]
    url = o["link"]
    if o["plataforma"] == "shopee" and o.get("fonte") == "api" and not dry_run():
        _shopee = _shopee or Shopee()
        sub = cfg("canais").get("sub_ids", {}).get(canal, canal)
        try:
            url = _shopee.short_link(o.get("link_produto") or o["link"], [sub, f"n{o.get('numero', 0)}"])
        except Exception as e:  # noqa: BLE001
            log.warning("não gerou link curto (%s); usando offerLink", e)
    o["links"][canal] = url
    _persistir(o)
    return url


def _persistir(o: dict) -> None:
    pool = load_json("ofertas.json", [])
    for p in pool:
        if p["id"] == o["id"]:
            p["links"] = o["links"]
            p["numero"] = o.get("numero")
    save_json("ofertas.json", pool)
