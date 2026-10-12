"""Gerente (CEO) da Ghost — orquestra os agentes. Cada comando é uma rotina chamada pelo GitHub Actions.

    python -m ghost.run ofertas          # Telegram (+ fila do Threads) — a cada 2h
    python -m ghost.run instagram auto   # post, reel ou carrossel conforme a agenda de horários
    python -m ghost.run decidir          # diz se o Instagram tem algo a publicar agora (post/reel/carrossel/nada)
    python -m ghost.run fila             # publica no Instagram/Threads o que está na fila
    python -m ghost.run resumo           # resumo diário + texto pronto para o WhatsApp
    python -m ghost.run semanal          # relatório do Analista + limpeza
    python -m ghost.run voz              # teste de 10 s da voz do Reel (áudio + diagnóstico no seu Telegram)
    python -m ghost.run conferencia 10   # segunda: foto do anúncio x arte final dos próximos produtos (no seu Telegram)
    python -m ghost.run renovar-tokens   # renova tokens da Meta (60 dias)
    python -m ghost.run vitrine          # só regenera a página
    python -m ghost.run demo             # roda tudo em modo simulado e salva em out/
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import traceback

from .agents import analista, cacador, conferencia as conf, curador, designer, meta, redator, telegram, videomaker, vitrine
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


def _publicacoes_hoje(agora: dt.datetime) -> tuple[int, int]:
    """Quantos posts (feed/carrossel) e Reels do Instagram já saíram, ou estão na fila, hoje."""
    hoje = agora.date()
    posts: set[str] = set()
    reels: set[str] = set()
    for h in curador.historico():
        if dt.datetime.fromisoformat(h["quando"]).date() != hoje:
            continue
        chave = str(h.get("post_id") or h["id"])  # carrossel: várias ofertas, um único post_id
        if h["canal"] in ("instagram", "instagram_carrossel"):
            posts.add(chave)
        elif h["canal"] == "instagram_reel":
            reels.add(chave)
    for i in load_json("fila.json", []):
        if i.get("canal") == "instagram" and dt.datetime.fromisoformat(i["criado"]).date() == hoje:
            (reels if i.get("tipo") == "reel" else posts).add("fila:" + i["criado"])
    return len(posts), len(reels)


def proximo_instagram(ig: dict, agora: dt.datetime | None = None) -> str:
    """O que o Instagram deve publicar AGORA, pela agenda de horários (config/canais.yaml).

    Os horários (horas_post / horas_reel, fuso de Brasília) são metas do dia. A cada rodada o robô
    confere se algum horário já passou sem a publicação correspondente e, se sim, publica uma.
    Se uma rodada atrasar ou falhar, a seguinte compensa; nunca publica duas vezes o mesmo horário.
    Retorna "post", "reel", "carrossel" ou "nada".
    """
    agora = agora or now()
    horas_post = sorted(ig.get("horas_post", [11]))
    horas_reel = sorted(ig.get("horas_reel", [18])) if ig.get("reel_diario", True) else []
    posts, reels = _publicacoes_hoje(agora)
    pendentes = []
    if posts < len(horas_post) and horas_post[posts] <= agora.hour:
        pendentes.append((horas_post[posts], "post"))
    if reels < len(horas_reel) and horas_reel[reels] <= agora.hour:
        pendentes.append((horas_reel[reels], "reel"))
    if not pendentes:
        return "nada"
    tipo = min(pendentes)[1]  # atende primeiro o horário mais antigo
    if tipo == "post" and posts == 0 and agora.weekday() == ig.get("carrossel_dia_semana", 6):
        return "carrossel"  # no dia do carrossel, o primeiro post do dia é o "Top 5"
    return tipo


def decidir() -> None:
    """Usada pelo workflow do Instagram: imprime post, reel, carrossel ou nada."""
    ig = cfg("canais")["instagram"]
    print(proximo_instagram(ig) if ig.get("ativo") else "nada")


def instagram(tipo: str = "auto") -> None:
    ig = cfg("canais")["instagram"]
    if not ig["ativo"]:
        return
    if tipo == "verificar":  # teste de conexão com a Meta, sem publicar nada
        r = meta.verificar()
        log.info("verificar: %s", r)
        telegram.para_dono("🔌 Teste de conexão Meta\n" + "\n".join(f"{k}: {v}" for k, v in r.items()))
        return
    if tipo == "teste":  # ensaio completo com produto de exemplo, só no Telegram do dono
        return teste()
    agora = now()
    if tipo == "auto":
        tipo = proximo_instagram(ig, agora)
        if tipo == "nada":
            log.info("instagram: nada pendente na agenda neste horário")
            return
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
            telegram.video_para_dono(arq, "🎬 Vídeo do dia pronto para TikTok e Shorts. " + videomaker.rotulo_voz() + "\n\nLegenda:\n"
                                     + redator.tiktok(o)[:700]
                                     + "\n\n📌 Comentário para fixar:\n" + redator.comentario_fixado(o))
        else:
            arq = designer.post(o, "ig")
        vitrine.adicionar(o, link(o, "vitrine"))
        meta.enfileirar({"canal": "instagram", "tipo": tipo, "ofertas": [o["id"]], "numeros": [o.get("numero")],
                         "arquivos": [_rel(arq)], "legenda": legenda, "criado": agora.isoformat()})
    vitrine.gerar()


def teste() -> None:
    """Ensaio geral com um produto de EXEMPLO: gera textos (IA), artes e o Reel e manda tudo
    só para o Telegram do dono. Nada vai para os canais públicos nem para a vitrine."""
    import random

    from . import mock
    from .core import env

    o = random.choice(mock.ofertas(["cozinha"]))
    o["numero"] = 0
    chat = env("TELEGRAM_OWNER_CHAT_ID")

    def foto(arq, legenda: str, html: bool = False) -> None:
        d = {"chat_id": chat or "dono", "caption": legenda[:1024]}
        if html:
            d["parse_mode"] = "HTML"
        with open(arq, "rb") as f:
            telegram._api("sendPhoto", d, {"photo": f})

    telegram.para_dono("🧪 Teste da Ghost com um produto de EXEMPLO (nada foi publicado nos canais).\n"
                       "Vão chegar aqui: a oferta do Telegram, o post do Instagram, o do Threads e o Reel.")
    foto(designer.post(o), "<b>📲 Assim sai no canal do Telegram:</b>\n\n" + redator.telegram(o), html=True)
    foto(designer.post(o, "ig"), "📸 INSTAGRAM\n\n" + redator.instagram(o))
    foto(designer.post(o, "th"), "🧵 THREADS\n\n" + redator.threads(o, o["link"]))
    arq_reel = videomaker.video(o)
    telegram.video_para_dono(arq_reel, "🎬 REEL / TikTok / Shorts " + videomaker.rotulo_voz() + "\n\n" + redator.tiktok(o)[:700])
    r = meta.verificar()
    telegram.para_dono(f"✅ Teste concluído (texto feito por: {o['copy'].get('origem', '?')}).\n"
                       + "\n".join(f"{k}: {v}" for k, v in r.items()))


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


NOMES_CANAL = {"telegram": "Telegram", "threads": "Threads", "instagram": "Instagram (posts)", "instagram_reel": "Reels"}


def _estoque(pool: list[dict], agora: dt.datetime | None = None) -> dict[str, tuple[int, float]]:
    """Por canal: quantos produtos ainda podem sair (fora da regra de dias sem repetir) e para quantos dias dá.

    O ritmo diário vem da configuração: Telegram ~8 rodadas/dia, Threads ~4, Instagram pelos horários da agenda.
    """
    canais = cfg("canais")
    ig = canais["instagram"]
    ritmo = {
        "telegram": 8 * canais["telegram"].get("ofertas_por_execucao", 1),
        "threads": 4,
        "instagram": len(ig.get("horas_post", [])) or 1,
        "instagram_reel": len(ig.get("horas_reel", [])) if ig.get("reel_diario", True) else 0,
    }
    ativo = {"telegram": canais["telegram"]["ativo"], "threads": canais["threads"]["ativo"],
             "instagram": ig["ativo"], "instagram_reel": ig["ativo"]}
    out: dict[str, tuple[int, float]] = {}
    for canal, por_dia in ritmo.items():
        if not por_dia or not ativo[canal]:
            continue
        ja = curador._recentes(canal)
        livres = sum(1 for o in pool if curador._passa_filtros(o) and o["id"] not in ja)
        out[canal] = (livres, livres / por_dia)
    return out


def _texto_estoque(est: dict[str, tuple[int, float]], total: int) -> str:
    linhas = [f"• {NOMES_CANAL[c]}: {livres} produtos novos (~{dias:.0f} dias)" for c, (livres, dias) in est.items()]
    return f"{total} produtos no CSV. Ainda podem sair, pela regra de dias sem repetir:\n" + "\n".join(linhas)


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
    telegram.para_dono(msg if top else msg.split("\n\n👇")[0] + "\n\nSem ofertas novas para o WhatsApp hoje.")
    if top:
        telegram.para_dono(wa)
    limite = cfg("canais").get("estoque", {}).get("alerta_dias", 3)
    est = _estoque(pool)
    if any(dias < limite for _, dias in est.values()):
        telegram.para_dono(f"📦 Estoque de ofertas baixo (alerta abaixo de {limite} dias).\n"
                           + _texto_estoque(est, len(pool))
                           + "\n\nHora de reabastecer o data/curadoria.csv: os produtos atuais + os novos.")


def voz() -> None:
    """Teste rápido da voz do Reel (uns 20 s, sem gerar vídeo): tenta a voz oficial de 3 jeitos e a lista de vozes do
    serviço, e manda o resultado (e o áudio que funcionou) no seu Telegram."""
    import asyncio
    import importlib.metadata as md
    import tempfile
    import time
    from pathlib import Path

    voz_cfg = cfg("marca").get("voz_video", "pt-BR-AntonioNeural")
    linhas = [f"🎙️ Teste de voz: {voz_cfg}",
              f"edge-tts {md.version('edge-tts')} · python {sys.version.split()[0]} · aiohttp {md.version('aiohttp')}"]
    try:
        import edge_tts

        vozes = asyncio.run(edge_tts.list_voices())
        existe = any(v.get("ShortName") == voz_cfg for v in vozes)
        linhas.append(f"✅ serviço de voz respondeu ({len(vozes)} vozes); '{voz_cfg}' {'existe' if existe else 'NÃO existe'}")
    except Exception as e:  # noqa: BLE001
        linhas.append(f"❌ o serviço de voz não respondeu à lista de vozes: {type(e).__name__}: {e}")
    texto = "Achado que vale cada centavo."
    rate, pitch = videomaker.PROSODIA["gancho"]
    enviado = False
    with tempfile.TemporaryDirectory() as tmp:
        for modo in videomaker.MODOS_VOZ:
            arq, t0 = Path(tmp) / f"{modo}.mp3", time.time()
            try:
                ev = asyncio.run(videomaker._sintetizar(texto, voz_cfg, rate, pitch, arq, modo=modo))
                tam = arq.stat().st_size
                linhas.append(f"{'✅' if tam else '❌'} modo '{modo}': {tam} bytes em {time.time() - t0:.1f}s, "
                              f"{len(ev)} palavras marcadas" + ("" if tam else " (ÁUDIO VAZIO, sem erro)"))
                if tam and not enviado:
                    chat = os.environ.get("TELEGRAM_OWNER_CHAT_ID") or "dono"
                    with open(arq, "rb") as f:
                        telegram._api("sendAudio", {"chat_id": chat, "caption": f"🎙️ {voz_cfg}, modo '{modo}'"}, {"audio": f})
                    enviado = True
            except Exception as e:  # noqa: BLE001
                linhas.append(f"❌ modo '{modo}': {type(e).__name__}: {e}")
    linhas.append("\nResumo: " + ("a voz oficial funcionou." if enviado else "a voz oficial NÃO funcionou de nenhum jeito."))
    telegram.para_dono("\n".join(linhas))


def semanal() -> None:
    telegram.para_dono(analista.relatorio(), html=True)
    pool = load_json("ofertas.json", [])
    telegram.para_dono("📦 Estoque de ofertas\n" + _texto_estoque(_estoque(pool), len(pool))
                       + "\n\nSe algum canal estiver abaixo de 5 dias, hoje à noite é dia de reabastecer o CSV.")
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
    "ofertas": ofertas, "instagram": instagram, "decidir": decidir, "fila": fila, "resumo": resumo, "semanal": semanal,
    "renovar-tokens": renovar_tokens, "vitrine": vitrine.gerar, "demo": demo, "conferencia": conf.executar, "voz": voz,
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
