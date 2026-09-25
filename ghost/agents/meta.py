"""Agente 6b — Publicador Meta (Instagram + Threads).

Instagram API com login do Instagram (conta Profissional: Criador ou Empresa):
    IG_USER_ID, IG_ACCESS_TOKEN        (token de longa duração, 60 dias — o Gerente renova sozinho)
Threads API:
    THREADS_USER_ID, THREADS_ACCESS_TOKEN

As mídias precisam estar numa URL pública (GitHub Pages) no momento da publicação, por isso
os posts entram numa fila (data/fila.json) e são publicados depois do deploy da vitrine.
"""
from __future__ import annotations

import time

from ..core import MEDIA_DIR, dry_run, env, get_logger, http, load_json, public_base_url, save_json

log = get_logger("meta")
IG = "https://graph.instagram.com/" + (env("META_API_VERSION") or "v23.0")
TH = "https://graph.threads.net/v1.0"
FILA = "fila.json"


class MetaError(RuntimeError):
    pass


# ------------------------------------------------------------------ fila
def enfileirar(item: dict) -> None:
    fila = load_json(FILA, [])
    fila.append(item)
    save_json(FILA, fila)


def url_de(rel: str) -> str:
    base = public_base_url()
    if not base:
        raise MetaError("PUBLIC_BASE_URL não configurada: o Instagram precisa de uma URL pública para as mídias")
    return f"{base}/{rel}"


def esperar_url(url: str, limite_s: int = 300) -> None:
    t0 = time.time()
    while time.time() - t0 < limite_s:
        try:
            if http().head(url, timeout=15, allow_redirects=True).status_code == 200:
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(10)
    raise MetaError(f"mídia não ficou pública a tempo: {url}")


def _req(method: str, url: str, **params) -> dict:
    r = http().request(method, url, params=params if method == "GET" else None,
                       data=params if method == "POST" else None, timeout=60)
    j = r.json()
    if "error" in j:
        raise MetaError(j["error"].get("message", str(j["error"])))
    return j


# ------------------------------------------------------------------ Instagram
def _ig_aguardar(container: str, token: str, limite_s: int = 600) -> None:
    t0 = time.time()
    while time.time() - t0 < limite_s:
        st = _req("GET", f"{IG}/{container}", fields="status_code", access_token=token).get("status_code")
        if st == "FINISHED":
            return
        if st in ("ERROR", "EXPIRED"):
            raise MetaError(f"container {container}: {st}")
        time.sleep(8)
    raise MetaError("Instagram demorou demais para processar a mídia")


def ig_publicar(tipo: str, urls: list[str], legenda: str) -> str:
    uid, tok = env("IG_USER_ID"), env("IG_ACCESS_TOKEN")
    if not (uid and tok):
        raise MetaError("IG_USER_ID / IG_ACCESS_TOKEN ausentes")
    if tipo == "post":
        cid = _req("POST", f"{IG}/{uid}/media", image_url=urls[0], caption=legenda, access_token=tok)["id"]
    elif tipo == "reel":
        cid = _req("POST", f"{IG}/{uid}/media", media_type="REELS", video_url=urls[0], caption=legenda,
                   share_to_feed="true", access_token=tok)["id"]
    elif tipo == "carrossel":
        filhos = []
        for u in urls[:10]:
            f = _req("POST", f"{IG}/{uid}/media", image_url=u, is_carousel_item="true", access_token=tok)["id"]
            filhos.append(f)
        for f in filhos:
            _ig_aguardar(f, tok)
        cid = _req("POST", f"{IG}/{uid}/media", media_type="CAROUSEL", children=",".join(filhos),
                   caption=legenda, access_token=tok)["id"]
    else:
        raise ValueError(tipo)
    _ig_aguardar(cid, tok)
    return _req("POST", f"{IG}/{uid}/media_publish", creation_id=cid, access_token=tok)["id"]


# ------------------------------------------------------------------ Threads
def threads_publicar(texto: str, imagem_url: str | None = None) -> str:
    uid, tok = env("THREADS_USER_ID"), env("THREADS_ACCESS_TOKEN")
    if not (uid and tok):
        raise MetaError("THREADS_USER_ID / THREADS_ACCESS_TOKEN ausentes")
    p = {"text": texto, "access_token": tok}
    if imagem_url:
        p |= {"media_type": "IMAGE", "image_url": imagem_url}
    else:
        p["media_type"] = "TEXT"
    cid = _req("POST", f"{TH}/{uid}/threads", **p)["id"]
    time.sleep(30)  # recomendação da Meta antes de publicar
    return _req("POST", f"{TH}/{uid}/threads_publish", creation_id=cid, access_token=tok)["id"]


# ------------------------------------------------------------------ processar fila
def processar_fila(ao_publicar=None, ao_falhar=None) -> int:
    """Publica tudo que está na fila. ao_publicar(item, id_post) / ao_falhar(item, erro)."""
    fila = load_json(FILA, [])
    restantes, feitos = [], 0
    for item in fila:
        if not all((MEDIA_DIR.parent / a).exists() for a in item["arquivos"]):
            log.warning("mídia de %s/%s não existe mais neste deploy; item descartado", item["canal"], item["tipo"])
            continue
        try:
            if dry_run():
                log.info("[simulado] %s/%s: %s", item["canal"], item["tipo"], item["arquivos"])
                pid = "simulado"
            else:
                urls = [url_de(a) for a in item["arquivos"]]
                for u in urls:
                    esperar_url(u)
                if item["canal"] == "instagram":
                    pid = ig_publicar(item["tipo"], urls, item["legenda"])
                else:
                    pid = threads_publicar(item["legenda"], urls[0] if urls else None)
            feitos += 1
            if ao_publicar:
                ao_publicar(item, pid)
        except Exception as e:  # noqa: BLE001
            item["tentativas"] = item.get("tentativas", 0) + 1
            log.error("falha ao publicar %s/%s: %s", item["canal"], item["tipo"], e)
            if item["tentativas"] < 3:
                restantes.append(item)
            elif ao_falhar:
                ao_falhar(item, e)
    save_json(FILA, restantes)
    return feitos


# ------------------------------------------------------------------ tokens
def renovar_tokens() -> dict:
    """Renova os tokens de 60 dias. Retorna {NOME_DO_SECRET: novo_token}."""
    novos = {}
    if env("IG_ACCESS_TOKEN"):
        j = _req("GET", "https://graph.instagram.com/refresh_access_token",
                 grant_type="ig_refresh_token", access_token=env("IG_ACCESS_TOKEN"))
        novos["IG_ACCESS_TOKEN"] = j["access_token"]
    if env("THREADS_ACCESS_TOKEN"):
        j = _req("GET", f"{TH.rsplit('/', 1)[0]}/refresh_access_token",
                 grant_type="th_refresh_token", access_token=env("THREADS_ACCESS_TOKEN"))
        novos["THREADS_ACCESS_TOKEN"] = j["access_token"]
    return novos


def limpar_midia_antiga(dias: int = 21) -> int:
    import datetime as dt
    import shutil

    limite = dt.date.today() - dt.timedelta(days=dias)
    n = 0
    for pasta in MEDIA_DIR.glob("20*-*-*"):
        try:
            if dt.date.fromisoformat(pasta.name) < limite:
                shutil.rmtree(pasta)
                n += 1
        except ValueError:
            continue
    return n
