"""Agente 2 — Curador.

Filtra as ofertas do Caçador (qualidade, preço, bloqueios), dá uma nota a cada uma e escolhe
as melhores que ainda não foram publicadas naquele canal. Também controla o histórico
(data/publicados.json) e numera as ofertas (#123) para a vitrine do "link na bio".
"""
from __future__ import annotations

import datetime as dt
import math

from ..core import cfg, get_logger, load_json, now, save_json, tema_atual

log = get_logger("curador")
HIST = "publicados.json"
ESTADO = "estado.json"


def _passa_filtros(o: dict) -> bool:
    f = cfg("nicho")["filtros"]
    titulo = o["titulo"].lower()
    if any(b in titulo for b in cfg("nicho").get("bloqueio", [])):
        return False
    if o["fonte"] == "curadoria":
        return True  # você já escolheu à mão
    p = o.get("preco") or 0
    return (
        f["preco_min"] <= p <= f["preco_max"]
        and o.get("nota", 0) >= f["nota_min"]
        and o.get("vendas", 0) >= f["vendas_min"]
        and o.get("comissao_pct", 0) >= f["comissao_pct_min"]
    )


def nota(o: dict) -> float:
    w = cfg("nicho")["pesos"]
    tema = tema_atual()
    kws = [k.lower() for k in tema.get("palavras_chave") or []]
    bonus_tema = 1.0 if o.get("keyword", "").lower() in kws else 0.0
    score = (
        w["comissao_valor"] * min(o.get("comissao_valor", 0), 20) / 20
        + w["desconto"] * min(o.get("desconto_pct", 0), 60) / 60
        + w["vendas"] * min(math.log10(o.get("vendas", 0) + 1), 4.5) / 4.5
        + w["nota"] * max(o.get("nota", 0) - 4.0, 0)
        + w["tema_do_dia"] * bonus_tema
    )
    if o["fonte"] == "curadoria":
        score += 1.5  # prioridade para o que você escolheu
    return round(score, 3)


def historico() -> list[dict]:
    return load_json(HIST, [])


def _recentes(canal: str) -> set[str]:
    dias = cfg("nicho")["filtros"]["dias_sem_repetir"]
    limite = now() - dt.timedelta(days=dias)
    return {
        h["id"] for h in historico()
        if h["canal"] == canal and dt.datetime.fromisoformat(h["quando"]) >= limite
    }


def numerar(o: dict) -> int:
    """Número permanente da oferta (#N) usado na vitrine e nas legendas do Instagram."""
    estado = load_json(ESTADO, {"proximo_numero": 1, "numeros": {}})
    if o["id"] in estado["numeros"]:
        o["numero"] = estado["numeros"][o["id"]]
    else:
        o["numero"] = estado["proximo_numero"]
        estado["numeros"][o["id"]] = o["numero"]
        estado["proximo_numero"] += 1
        save_json(ESTADO, estado)
    return o["numero"]


def escolher(pool: list[dict], canal: str, n: int = 1) -> list[dict]:
    ja = _recentes(canal)
    boas = [o for o in pool if _passa_filtros(o) and o["id"] not in ja]
    # variedade: produto que saiu em QUALQUER canal nas últimas 24h perde prioridade
    ontem = now() - dt.timedelta(hours=24)
    recentes_geral = {h["id"] for h in historico() if dt.datetime.fromisoformat(h["quando"]) >= ontem}
    recentes_geral |= {oid for item in load_json("fila.json", []) for oid in item.get("ofertas", [])}
    for o in boas:
        o["score"] = nota(o) - (2.0 if o["id"] in recentes_geral else 0.0)
    boas.sort(key=lambda o: o["score"], reverse=True)
    # evita 2 produtos da mesma palavra-chave seguidos
    escolhidas, kws = [], set()
    for o in boas:
        if len(escolhidas) >= n:
            break
        if o.get("keyword") and o["keyword"] in kws and len(boas) > n * 2:
            continue
        kws.add(o.get("keyword"))
        escolhidas.append(o)
    for o in escolhidas:
        numerar(o)
    log.info("%s: %d candidatas aprovadas, escolhidas %d", canal, len(boas), len(escolhidas))
    return escolhidas


def registrar(o: dict, canal: str, extra: dict | None = None) -> None:
    hist = historico()
    limite = now() - dt.timedelta(days=90)
    hist = [h for h in hist if dt.datetime.fromisoformat(h["quando"]) >= limite]
    hist.append({
        "id": o["id"], "canal": canal, "quando": now().isoformat(), "numero": o.get("numero"),
        "titulo": o["titulo"][:90], "plataforma": o["plataforma"], **(extra or {}),
    })
    save_json(HIST, hist)
