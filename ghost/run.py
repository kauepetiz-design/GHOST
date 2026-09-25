"""Gerente (CEO) da Ghost — orquestra os agentes. Cada comando é uma rotina chamada pelo GitHub Actions.

    python -m ghost.run ofertas          # Telegram (+ fila do Threads) — a cada 2h
    python -m ghost.run instagram auto   # post, reel ou carrossel conforme hora/dia
    python -m ghost.run fila             # publica no Instagram/Threads o que está na fila
    python -m ghost.run resumo           # resumo diário + texto pronto para o WhatsApp
    python -m ghost.run semanal          # relatório do Analista + limpeza
    python -m ghost.run renovar-tokens   # renova tokens da Meta (60 dias)
    python -m ghost.run vitrine          # só regenera a página
    python -m ghost.run demo             # roda tudo em modo simulado e salva em out/
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import traceback

from .agents import analista, cacador, curador, designer, meta, redator, telegram, videomaker, vitrine
from .core import MEDIA_DIR, OUT_DIR, cfg, dry_run, get_logger, load_json, now, tema_atual
from .links import link

log = get_logger("gerente")


def _rel(path) -> str:
    """Caminho relativo a docs/ (é assim que a mídia fica no GitHub Pages)."""
    return path.relative_to(MEDIA_DIR.parent).as_posix()


def _threads_liberado(intervalo_h: float) -> bool:
    """Threads recebe ~4 posts/dia: só posta se o último foi há mais de `intervalo_h` horas."""
    if dry_run():
        return True
    if any(i["canal"] == "threads" for i in load_json("fila.json", [])):
        return False
    ultimos = [dt.datetime.fromisoformat(h["quando"]) for h in curador.historico() if h["canal"] == "threads"]
    return not ultimos or (now() - max(ultimos)).total_seconds() > intervalo_h * 3600


# ------------------------------------------------------------------ rotinas
def ofertas() -> None:
    canais = cfg("canais")
    tema = tema_atual()
    pool = cacador.run()

    if canais["telegram"]["ativo"]:
        n = canais["telegram"]["ofertas_por_execucao"] * int(tema.get("intensidade", 1))
        for o in curador.escolher(pool, "telegram", n):
            arte = designer.post(o)
            url = link(o, "telegram")
            telegram.oferta(arte, redator.telegram(o), url, canais["telegram"].get("botao", "🛒 Ver oferta"))
            curador.registrar(o, "telegram")
            vitrine.adicionar(o, link(o, "vitrine"))

    if canais["threads"]["ativo"] and _threads_liberado(canais["threads"].get("intervalo_min_horas", 3.5)):
        for o in curador.escolher(pool, "threads", canais["threads"].get("por_execucao", 1)):
            arte = designer.post(o, "th")
            meta.enfileirar({"canal": "threads", "tipo": "imagem", "ofertas": [o["id"]],
                             "numeros": [o.get("numero")], "arquivos": [_rel(arte)],
                             "legenda": redator.threads(o, link(o, "threads")), "criado": now().isoformat()})
            vitrine.adicionar(o, link(o, "vitrine"))
    vitrine.gerar()


def instagram(tipo: str = "auto") -> None:
    ig = cfg("canais")["instagram"]
    if not ig["ativo"]:
        return
    agora = now()
    if tipo == "auto":
        if agora.weekday() == ig.get("carrossel_dia_semana", 6) and agora.hour < 14:
            tipo = "carrossel"
        elif agora.hour >= 14 and ig.get("reel_diario", True):
            tipo = "reel"
        else:
            tipo = "post"
    pool = cacador.run()

    if tipo == "carrossel":
        # top 5 da semana: o que teve melhor nota entre as ofertas disponíveis
        escolhidas = curador.escolher(pool, "instagram_carrossel", 5)
        if len(escolhidas) < 3:
            log.info("poucas ofertas para carrossel; publicando post")
            return instagram("post")
        artes = designer.carrossel(escolhidas)
        for o, a in zip(escolhidas, artes[1:]):
            vitrine.adicionar(o, link(o, "vitrine"))
        meta.enfileirar({"canal": "instagram", "tipo": "carrossel", "ofertas": [o["id"] for o in escolhidas],
                         "numeros": [o.get("numero") for o in escolhidas], "arquivos": [_rel(a) for a in artes],
                         "legenda": redator.instagram_carrossel(escolhidas), "criado": agora.isoformat()})
    else:
        canal = "instagram_reel" if tipo == "reel" else "instagram"
        escolhidas = curador.escolher(pool, canal, 1)
        if not escolhidas:
            log.warning("nenhuma oferta nova para o Instagram")
            return
        o = escolhidas[0]
        legenda = redator.instagram(o)
        if tipo == "reel":
            arq = videomaker.video(o)
            # o mesmo vídeo vai para você postar no TikTok e no YouTube Shorts (1 minuto no celular)
            telegram.video_para_dono(arq, "🎬 Vídeo do dia pronto para TikTok e Shorts.\n\nLegenda:\n" + legenda[:900])
        else:
            arq = designer.post(o, "ig")
        vitrine.adicionar(o, link(o, "vitrine"))
        meta.enfileirar({"canal": "instagram", "tipo": tipo, "ofertas": [o["id"]], "numeros": [o.get("numero")],
                         "arquivos": [_rel(arq)], "legenda": legenda, "criado": agora.isoformat()})
    vitrine.gerar()


def fila() -> None:
    pool = {o["id"]: o for o in load_json("ofertas.json", [])}

    def ok(item, pid):
        canal = item["canal"] if item["canal"] == "threads" else {
            "carrossel": "instagram_carrossel", "reel": "instagram_reel"}.get(item["tipo"], "instagram")
        for oid, num in zip(item["ofertas"], item.get("numeros", [])):
            o = pool.get(oid, {"id": oid, "titulo": "?", "plataforma": oid.split(":")[0]})
            o["numero"] = num
            curador.registrar(o, canal, {"post_id": pid})

    def falhou(item, erro):
        telegram.para_dono(f"⚠️ Não consegui publicar no {item['canal']} ({item['tipo']}) depois de 3 tentativas.\n"
                           f"Erro: {erro}\nSe for token expirado, rode o workflow 'Renovar tokens'.")

    n = meta.processar_fila(ok, falhou)
    log.info("fila: %d publicados", n)


def resumo() -> None:
    hoje = now().date()
    hist = [h for h in curador.historico() if dt.datetime.fromisoformat(h["quando"]).date() == hoje]
    por_canal = {}
    for h in hist:
        por_canal[h["canal"]] = por_canal.get(h["canal"], 0) + 1
    pool = load_json("ofertas.json", [])
    n = cfg("canais")["whatsapp"].get("ofertas_no_resumo", 5)
    top = curador.escolher(pool, "whatsapp", n)
    wa = redator.whatsapp([(o, link(o, "whatsapp")) for o in top])
    for o in top:
        curador.registrar(o, "whatsapp")
    fila_pend = len(load_json("fila.json", []))
    msg = (f"☀️ Resumo de {hoje:%d/%m} | tema: {tema_atual().get('nome')}\n"
           f"Publicações hoje: {', '.join(f'{k}: {v}' for k, v in por_canal.items()) or 'nenhuma'}\n"
           f"Fila pendente: {fila_pend}\n\n"
           "👇 Copie a mensagem abaixo e cole no seu canal do WhatsApp:")
    telegram.para_dono(msg)
    telegram.para_dono(wa)


def semanal() -> None:
    telegram.para_dono(analista.relatorio(), html=True)
    removidas = meta.limpar_midia_antiga()
    log.info("pastas de mídia antigas removidas: %d", removidas)


def renovar_tokens() -> None:
    novos = meta.renovar_tokens()
    OUT_DIR.mkdir(exist_ok=True)
    with open(OUT_DIR / "tokens.env", "w") as f:
        for k, v in novos.items():
            print(f"::add-mask::{v}")  # esconde o token nos logs do GitHub
            f.write(f"{k}={v}\n")
    log.info("tokens renovados: %s", ", ".join(novos) or "nenhum")


def demo() -> None:
    """Executa o dia inteiro em modo simulado: nada é publicado, tudo fica em out/ e docs/."""
    os.environ["DRY_RUN"] = "1"
    ofertas()
    instagram("post")
    instagram("reel")
    instagram("carrossel")
    fila()
    resumo()
    semanal()
    log.info("demo concluída: veja out/ e docs/")


ROTINAS = {
    "ofertas": ofertas, "instagram": instagram, "fila": fila, "resumo": resumo, "semanal": semanal,
    "renovar-tokens": renovar_tokens, "vitrine": vitrine.gerar, "demo": demo,
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ROTINAS:
        print(__doc__)
        return 1
    nome, args = argv[0], argv[1:]
    try:
        ROTINAS[nome](*args)
        return 0
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        try:
            telegram.para_dono(f"🚨 Ghost: a rotina '{nome}' falhou.\n{type(e).__name__}: {e}")
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
