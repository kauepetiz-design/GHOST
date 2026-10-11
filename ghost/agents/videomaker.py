"""Agente 5 — Editor de Vídeo (v2).

Reel / TikTok / Shorts vertical de ~17 s com estrutura FIXA em 5 batidas (a mesma em todo vídeo):

    GANCHO  >  PRODUTO (com prova)  >  DICA DE CHEF  >  PREÇO  >  CHAMADA (Telegram)

- foto do produto "girando" em 3D falso (balanço de 34° em perspectiva, sombra e brilho);
- legenda palavra por palavra, sincronizada com a voz (karaokê);
- voz em português (edge-tts, grátis) com ritmo e tom diferentes em cada batida;
- efeitos sonoros leves; música de fundo opcional (um mp3 livre de direitos em assets/music/);
- tudo dentro da "zona segura" do Reels (a interface do app cobre o topo e a parte de baixo).

Sem internet para a voz, o vídeo sai só com legendas (áudio sem fala).
Ajustes opcionais em config/marca.yaml: voz_video, video_balanco_graus (34), video_sfx (true).
"""
from __future__ import annotations

import array
import asyncio
import math
import random
import subprocess
import tempfile
import wave
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from ..core import ROOT, brl, cfg, get_logger
from . import designer, logo, redator

log = get_logger("videomaker")
W, H, FPS = 1080, 1920, 30
LEAD = 0.15                      # respiro antes da voz começar em cada batida
MINIMO = {"gancho": 2.6, "produto": 3.4, "dica": 3.4, "preco": 3.8, "cta": 3.4}
FOLGA_FINAL = {"cta": 0.6}
# (velocidade, tom) da voz em cada batida: gancho mais vivo, chamada mais calma
PROSODIA = {
    "gancho": ("+14%", "+6Hz"),
    "produto": ("+8%", "+2Hz"),
    "dica": ("+4%", "+0Hz"),
    "preco": ("+12%", "+4Hz"),
    "cta": ("+6%", "+2Hz"),
}
# posição do cartão da foto em cada batida: (centro x, centro y, lado, escala do disco)
LAYOUT = {
    "gancho": (540, 760, 760, 1.0),
    "produto": (540, 640, 560, 0.9),
    "dica": (540, 640, 500, 0.8),
    "preco": (540, 660, 520, 0.82),
}


# ------------------------------------------------------------------ utilidades gerais
@lru_cache(None)
def F(tipo: str, size: int):
    return designer._font(tipo, size)


_MD = ImageDraw.Draw(Image.new("RGB", (10, 10)))


def _cor(nome: str) -> str:
    return cfg("marca")["cores"][nome]


def _hex(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _clamp(x: float, a: float = 0.0, b: float = 1.0) -> float:
    return max(a, min(b, x))


def _out_cubic(t: float) -> float:
    t = _clamp(t)
    return 1 - (1 - t) ** 3


def _out_back(t: float, s: float = 1.9) -> float:
    t = _clamp(t) - 1
    return 1 + (s + 1) * t ** 3 + s * t ** 2


def _fade(im: Image.Image, a: float) -> Image.Image:
    im = im.copy()
    im.putalpha(im.getchannel("A").point(lambda p: int(p * _clamp(a))))
    return im


def _scaled(im: Image.Image, k: float) -> Image.Image:
    k = max(k, 0.02)
    return im.resize((max(int(im.width * k), 2), max(int(im.height * k), 2)), Image.BILINEAR)


def _paste_c(base: Image.Image, im: Image.Image, cx: float, cy: float) -> None:
    base.paste(im, (int(cx - im.width / 2), int(cy - im.height / 2)), im)


def _pilula(w: int, h: int, cor: str | tuple, s: int = 3) -> Image.Image:
    """Pílula (cantos totalmente arredondados) com borda suave."""
    cor = _hex(cor) if isinstance(cor, str) else cor
    im = Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle([0, 0, w * s - 1, h * s - 1], radius=h * s // 2, fill=cor + (255,))
    return im.resize((w, h), Image.LANCZOS)


def _circ(im: Image.Image, size: int) -> Image.Image:
    im = ImageOps.fit(im.convert("RGB"), (size, size), Image.LANCZOS)
    m = Image.new("L", (size * 3, size * 3), 0)
    ImageDraw.Draw(m).ellipse([0, 0, size * 3 - 1, size * 3 - 1], fill=255)
    out = Image.new("RGBA", (size, size))
    out.paste(im, (0, 0), m.resize((size, size), Image.LANCZOS))
    return out


def _duracao(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


# ------------------------------------------------------------------ roteiro (as 5 batidas)
def _frase(txt: str, fim: str = ".") -> str:
    txt = " ".join(str(txt).split()).strip()
    if txt and txt[-1] not in ".!?…":
        txt += fim
    return txt


def _fala(o: dict, c: dict) -> list[dict]:
    """Monta as 5 batidas. Preço e chamada são calculados (sempre corretos); as 3 primeiras vêm do redator."""
    marca = cfg("marca")
    v = c.get("video") if isinstance(c.get("video"), dict) else {}
    gancho = _frase(v.get("gancho") or c.get("gancho") or "Olha esse achado de cozinha")
    produto = _frase(v.get("produto") or c.get("titulo_curto") or str(o.get("titulo", ""))[:40])
    dica_fonte = str(v.get("dica") or c.get("dica_chef") or "")
    dica = redator._dica_curta(dica_fonte) if dica_fonte.strip() else ""

    if o.get("preco"):
        fala_preco = f"Por {brl(o['preco'])}."
        pct = int(o.get("desconto_pct") or 0)
        if o.get("preco_de") and o["preco_de"] > o["preco"] and pct >= 10:
            fala_preco += f" {pct}% abaixo do preço cheio."
    else:
        fala_preco = "Confira o preço atualizado no link."

    beats = [
        {"kind": "gancho", "texto": gancho},
        {"kind": "produto", "texto": produto},
        {"kind": "dica", "texto": dica},
        {"kind": "preco", "texto": fala_preco},
        {"kind": "cta", "texto": f"O link tá no Telegram. Procura {marca['nome']}."},
    ]
    return [b for b in beats if b["texto"]]


# ------------------------------------------------------------------ voz
async def _sintetizar(texto: str, voz: str, rate: str, pitch: str, destino: Path) -> list[tuple[float, float, str]]:
    import edge_tts

    try:
        com = edge_tts.Communicate(texto, voz, rate=rate, pitch=pitch, boundary="WordBoundary")
    except TypeError:  # versão antiga do edge-tts, sem o argumento boundary
        com = edge_tts.Communicate(texto, voz, rate=rate, pitch=pitch)
    eventos: list[tuple[float, float, str]] = []
    with open(destino, "wb") as f:
        async for ch in com.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                eventos.append((ch["offset"] / 1e7, ch["duration"] / 1e7, ch["text"]))
    return eventos


def _tts(beats: list[dict], pasta: Path) -> list[dict] | None:
    """Narração grátis: edge-tts (voz neural, com tom/ritmo por batida) -> gTTS (reserva) -> None."""
    voz = cfg("marca").get("voz_video", "pt-BR-AntonioNeural")
    try:
        async def go():
            res = []
            for i, b in enumerate(beats):
                rate, pitch = PROSODIA.get(b["kind"], ("+8%", "+0Hz"))
                p = pasta / f"voz{i}.mp3"
                ev = await _sintetizar(b["texto"], voz, rate, pitch, p)
                res.append({"arquivo": p, "eventos": ev})
            return res

        res = asyncio.run(go())
        if all(r["arquivo"].stat().st_size > 0 for r in res):
            return res
    except Exception as e:  # noqa: BLE001
        log.warning("edge-tts falhou (%s); tentando gTTS", e)
    try:
        from gtts import gTTS

        res = []
        for i, b in enumerate(beats):
            p = pasta / f"voz{i}.mp3"
            gTTS(b["texto"], lang="pt", tld="com.br").save(str(p))
            res.append({"arquivo": p, "eventos": []})
        return res
    except Exception as e:  # noqa: BLE001
        log.warning("gTTS falhou (%s); vídeo sem narração", e)
    return None


def _tempos_palavras(words: list[str], eventos: list, voz_dur: float) -> list[float]:
    """Quando cada palavra é falada (em s, dentro da batida). Usa o relógio da voz; se não vier, estima."""
    if eventos and len(eventos) == len(words):
        return [LEAD + e[0] for e in eventos]
    tot = sum(len(w) + 1 for w in words)
    t, out = LEAD, []
    for w in words:
        out.append(t)
        t += voz_dur * (len(w) + 1) / tot
    return out


# ------------------------------------------------------------------ efeitos sonoros (sintetizados, sem arquivos)
SR = 44100


def _pop() -> list[float]:
    n = int(0.12 * SR)
    return [math.sin(2 * math.pi * (620 + 500 * math.exp(-(i / SR) * 30)) * (i / SR)) * math.exp(-(i / SR) * 38) * 0.9
            for i in range(n)]


def _ding() -> list[float]:
    n = int(0.6 * SR)
    out = []
    for i in range(n):
        t = i / SR
        out.append((math.sin(2 * math.pi * 1318 * t) + 0.5 * math.sin(2 * math.pi * 1976 * t)) * math.exp(-t * 7) * 0.5)
    return out


def _whoosh() -> list[float]:
    rnd, y, n, out = random.Random(7), 0.0, int(0.38 * SR), []
    for i in range(n):
        p = i / n
        y += (0.04 + 0.5 * p) * (rnd.uniform(-1, 1) - y)
        out.append(y * (math.sin(math.pi * p) ** 1.6) * 1.4)
    return out


def _faixa_sfx(eventos: list[tuple[float, str]], total: float, destino: Path) -> None:
    sons = {"pop": (_pop(), 0.6), "ding": (_ding(), 0.5), "whoosh": (_whoosh(), 0.5)}
    n = int(total * SR)
    buf = array.array("f", [0.0]) * n
    for t, nome in eventos:
        amostras, ganho = sons[nome]
        i0 = int(t * SR)
        for k, v in enumerate(amostras):
            if i0 + k >= n:
                break
            buf[i0 + k] += v * ganho
    est = array.array("h")
    for v in buf:
        s = int(_clamp(v, -1, 1) * 32000)
        est.append(s)
        est.append(s)
    with wave.open(str(destino), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(est.tobytes())


def _musica() -> Path | None:
    pasta = ROOT / "assets" / "music"
    if pasta.is_dir():
        for p in sorted(pasta.iterdir()):
            if p.suffix.lower() in (".mp3", ".m4a", ".wav", ".ogg"):
                return p
    return None


# ------------------------------------------------------------------ perspectiva (giro 3D falso)
def _resolver(M: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    A = [row[:] + [b[i]] for i, row in enumerate(M)]
    for i in range(n):
        p = max(range(i, n), key=lambda r: abs(A[r][i]))
        A[i], A[p] = A[p], A[i]
        for r in range(i + 1, n):
            f = A[r][i] / A[i][i]
            for c in range(i, n + 1):
                A[r][c] -= f * A[i][c]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (A[i][n] - sum(A[i][j] * x[j] for j in range(i + 1, n))) / A[i][i]
    return x


def _coef(dst: list[tuple[float, float]], src: list[tuple[float, float]]) -> list[float]:
    M, b = [], []
    for (X, Y), (x, y) in zip(dst, src):
        M.append([X, Y, 1, 0, 0, 0, -x * X, -x * Y])
        b.append(x)
        M.append([0, 0, 0, X, Y, 1, -y * X, -y * Y])
        b.append(y)
    return _resolver(M, b)


@lru_cache(None)
def _mascara(size: int) -> Image.Image:
    m = Image.new("L", (size * 2, size * 2), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size * 2 - 1, size * 2 - 1], radius=int(size * 0.07), fill=255)
    return m.resize((size, size), Image.LANCZOS)


# ------------------------------------------------------------------ peças fixas (pré-calculadas uma vez)
def _bg() -> Image.Image:
    base = Image.new("RGB", (W, H), _cor("fundo"))
    v = Image.new("L", (W, H), 0)
    ImageDraw.Draw(v).ellipse([-500, -300, W + 500, H + 300], fill=255)
    v = v.filter(ImageFilter.GaussianBlur(220))
    return Image.composite(base, Image.new("RGB", (W, H), "#F1DFC6"), v)


def _disco() -> Image.Image:
    s = 1500
    big = Image.new("RGBA", (s * 2, s * 2), (0, 0, 0, 0))
    ImageDraw.Draw(big).ellipse([0, 0, s * 2 - 1, s * 2 - 1], fill=_hex(_cor("primaria")) + (255,))
    return big.resize((s, s), Image.LANCZOS)


def _sombra() -> Image.Image:
    s = Image.new("RGBA", (1100, 1100), (0, 0, 0, 0))
    ImageDraw.Draw(s).rounded_rectangle([120, 120, 980, 980], radius=60, fill=(60, 20, 10, 110))
    return s.filter(ImageFilter.GaussianBlur(26))


def _cartao(prod: Image.Image) -> Image.Image:
    S = 860
    card = Image.new("RGB", (S, S), "white")
    foto = ImageOps.contain(prod, (800, 800), Image.LANCZOS)
    card.paste(foto, ((S - foto.width) // 2, (S - foto.height) // 2))
    card = card.convert("RGBA")
    card.putalpha(_mascara(S))
    return card


def _brilho() -> Image.Image:
    """Faixa diagonal de luz (máscara L) que 'varre' o cartão quando ele gira."""
    S, bw = 860, 90
    x0 = 1290 - bw / 2 + 0.3 * S
    m = Image.new("L", (S * 3, S), 0)
    ImageDraw.Draw(m).polygon([(x0, 0), (x0 + bw, 0), (x0 + bw - 0.6 * S, S), (x0 - 0.6 * S, S)], fill=255)
    return m.filter(ImageFilter.GaussianBlur(26)).point(lambda p: int(p * 0.55))


def _gradiente() -> Image.Image:
    return Image.linear_gradient("L").transpose(Image.ROTATE_90)  # 0 na esquerda -> 255 na direita


def _selo(pct: int) -> Image.Image:
    S, s = 320, 3
    im = Image.new("RGBA", (S * s, S * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([10 * s, 10 * s, (S - 10) * s, (S - 10) * s], fill=_hex(_cor("texto")) + (255,))
    d.ellipse([30 * s, 30 * s, (S - 30) * s, (S - 30) * s], fill=(255, 200, 87, 255))
    im = im.resize((S, S), Image.LANCZOS)
    d = ImageDraw.Draw(im)
    d.text((S // 2, S // 2 - 16), f"-{pct}%", font=F("titulo", 78), fill=_cor("texto"), anchor="mm")
    d.text((S // 2, S // 2 + 52), "NO PREÇO", font=F("titulo", 30), fill=_cor("texto"), anchor="mm")
    return im.rotate(-12, resample=Image.BICUBIC)


@lru_cache(None)
def _chip(texto: str) -> Image.Image:
    size = 42
    while size > 28 and _MD.textlength(texto, font=F("medio", size)) + 170 > 960:
        size -= 2
    f = F("medio", size)
    w, h = int(_MD.textlength(texto, font=f)) + 170, 100
    im = _pilula(w, h, "#FFFFFF")
    s = 3
    ck = Image.new("RGBA", (100 * s, 100 * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(ck)
    d.ellipse([16 * s, 16 * s, 84 * s, 84 * s], fill=_hex(_cor("destaque")) + (255,))
    d.line([(34 * s, 52 * s), (46 * s, 64 * s), (68 * s, 36 * s)], fill="white", width=8 * s, joint="curve")
    ck = ck.resize((100, 100), Image.LANCZOS)
    im.paste(ck, (0, 0), ck)
    ImageDraw.Draw(im).text((104, h // 2 + 1), texto, font=f, fill=_cor("texto"), anchor="lm")
    return im


@lru_cache(None)
def _preco_pilula(txt: str) -> Image.Image:
    size = 128
    while size > 56 and _MD.textlength(txt, font=F("titulo", size)) + 110 > 980:
        size -= 6
    f = F("titulo", size)
    w, h = int(_MD.textlength(txt, font=f)) + 110, 190
    im = Image.new("RGBA", (w * 2, h * 2), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle([0, 0, w * 2 - 1, h * 2 - 1], radius=60 * 2, fill=_hex(_cor("texto")) + (255,))
    im = im.resize((w, h), Image.LANCZOS)
    ImageDraw.Draw(im).text((w // 2, h // 2 + 4), txt, font=f, fill="white", anchor="mm")
    return im


# ------------------------------------------------------------------ legenda karaokê
def _quebrar(words: list[str], f, max_w: float) -> list[list[str]]:
    linhas, cur = [[]], ""
    for w in words:
        teste = (cur + " " + w).strip()
        if _MD.textlength(teste, font=f) <= max_w or not linhas[-1]:
            linhas[-1].append(w)
            cur = teste
        else:
            linhas.append([w])
            cur = w
    return linhas


@lru_cache(None)
def _ajustar(words: tuple, max_w: int, max_h: int, start: int, minimo: int):
    size = start
    while size >= minimo:
        linhas = _quebrar(list(words), F("titulo", size), max_w)
        lh = int(size * 1.3)
        if len(linhas) * lh <= max_h:
            return size, linhas, lh
        size -= 4
    return minimo, _quebrar(list(words), F("titulo", minimo), max_w), int(minimo * 1.3)


def _legenda(base: Image.Image, words: list[str], tempos: list[float], tl: float, box, start: int, minimo: int,
             atraso: float = 0.0, pad: int = 34) -> None:
    x0, y0, x1, y1 = box
    size, linhas, lh = _ajustar(tuple(words), x1 - x0 - 2 * pad, y1 - y0 - 2 * pad, start, minimo)
    f = F("titulo", size)
    bw, bh = x1 - x0, len(linhas) * lh + 2 * pad
    a = _out_cubic((tl - atraso) / 0.28)
    if a <= 0:
        return
    camada = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(camada)
    d.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=42, fill=_hex(_cor("texto")) + (int(238 * a),))
    ativa = max([i for i, ts in enumerate(tempos) if tl >= ts], default=-1)
    esp = _MD.textlength(" ", font=f)
    gi, y = 0, pad
    for ln in linhas:
        tw = _MD.textlength(" ".join(ln), font=f)
        x = (bw - tw) / 2
        for w in ln:
            ww = _MD.textlength(w, font=f)
            if gi == ativa:
                d.rounded_rectangle([x - 14, y + int(lh * .08), x + ww + 14, y + int(lh * .92)], radius=24, fill=_cor("primaria"))
                col = (255, 255, 255, 255)
            elif gi < ativa:
                col = (255, 255, 255, int(255 * a))
            else:
                col = (150, 140, 134, int(255 * a))
            d.text((x, y + lh / 2), w, font=f, fill=col, anchor="lm")
            x += ww + esp
            gi += 1
        y += lh
    base.paste(camada, (x0, y0 + int((1 - a) * 46)), camada)


# ------------------------------------------------------------------ o vídeo quadro a quadro
class _Quadros:
    def __init__(self, o: dict, cenas: list[dict]):
        marca = cfg("marca")
        self.o, self.cenas = o, cenas
        self.total = cenas[-1]["t0"] + cenas[-1]["dur"]
        self.nome = marca["nome"].upper()
        self.arroba = marca.get("arroba", "")
        self.balanco = math.radians(float(marca.get("video_balanco_graus", 34)))
        self.bg, self.disco, self.sombra = _bg(), _disco(), _sombra()
        self._discos: dict[int, Image.Image] = {}
        self.cartao = _cartao(designer.imagem_produto(o))
        self.brilho, self.grad = _brilho(), _gradiente()
        self.avatar = logo.avatar(300)
        self.marca_pilula = self._marca()
        self.selo_img = _selo(int(o["desconto_pct"])) if self._tem_selo() else None
        self.chips = [b for b in (redator.copy(o).get("beneficios") or [])][:3]

    def _disco_em(self, diametro: float) -> Image.Image:
        chave = max(int(round(diametro / 4) * 4), 8)
        if chave not in self._discos:
            if len(self._discos) > 5:
                self._discos.clear()
            self._discos[chave] = self.disco.resize((chave, chave), Image.BILINEAR)
        return self._discos[chave]

    def _tem_selo(self) -> bool:
        o = self.o
        return bool(o.get("preco_de") and o.get("preco") and o["preco_de"] > o["preco"] and int(o.get("desconto_pct") or 0) >= 10)

    def _marca(self) -> Image.Image:
        f = F("titulo", 38)
        w, h = int(_MD.textlength(self.nome, font=f)) + 150, 96
        im = _pilula(w, h, _cor("texto"))
        lg = _circ(self.avatar, 76)
        im.paste(lg, (10, 10), lg)
        ImageDraw.Draw(im).text((100, h // 2 + 1), self.nome, font=f, fill=_cor("fundo"), anchor="lm")
        return im

    def _selo_chef(self) -> Image.Image:
        f = F("titulo", 46)
        w, h = int(_MD.textlength("DICA DE CHEF", font=f)) + 190, 112
        im = _pilula(w, h, _cor("destaque"))
        lg = _circ(self.avatar, 92)
        im.paste(lg, (10, 10), lg)
        ImageDraw.Draw(im).text((120, h // 2 + 1), "DICA DE CHEF", font=f, fill="white", anchor="lm")
        return im

    # ---- cartão girando
    def _face(self, size: int, theta: float) -> Image.Image:
        face = self.cartao.resize((size, size), Image.BILINEAR)
        rgb, alfa = face.convert("RGB"), face.getchannel("A")
        s = math.sin(theta)
        k = 0.32 * abs(s)
        if k > 0.004:  # lado que se afasta fica mais escuro
            g = self.grad.resize((size, size), Image.BILINEAR)
            if s < 0:
                g = ImageOps.mirror(g)
            rgb = Image.composite(Image.new("RGB", (size, size), (25, 12, 8)), rgb, g.point(lambda p: int(p * k)))
        u = _clamp(0.5 + theta / (2 * self.balanco))
        ox = int(1420 - 1290 * u)  # o brilho acompanha o ângulo
        brilho = self.brilho.crop((ox, 0, ox + 860, 860)).resize((size, size), Image.BILINEAR)
        rgb = Image.composite(Image.new("RGB", (size, size), "white"), rgb, brilho)
        out = rgb.convert("RGBA")
        out.putalpha(alfa)
        return out

    def _cartao_3d(self, base: Image.Image, cx: float, cy: float, size: int, t: float) -> None:
        if size < 12:
            return
        theta = self.balanco * math.sin(2 * math.pi * t / 3.4)
        face = self._face(size, theta)
        pad = int(size * 0.12)
        cw = size + 2 * pad
        meio, dist = size / 2, 2.4 * size
        dst = []
        for (x, y) in [(-meio, -meio), (meio, -meio), (meio, meio), (-meio, meio)]:
            k = dist / (dist + x * math.sin(theta))
            dst.append((cw / 2 + x * math.cos(theta) * k, cw / 2 + y * k))
        co = _coef(dst, [(0, 0), (size, 0), (size, size), (0, size)])
        tela = Image.new("RGBA", (cw, cw), (0, 0, 0, 0))
        tela.paste(face, (0, 0))
        giro = tela.transform((cw, cw), Image.PERSPECTIVE, tuple(co), Image.BILINEAR)
        f = size / 860
        sh = self.sombra.resize((int(1100 * f * (0.55 + 0.45 * abs(math.cos(theta)))), int(1100 * f)), Image.BILINEAR)
        base.paste(sh, (int(cx - sh.width / 2), int(cy - sh.height / 2 + 22 * f)), sh)
        base.paste(giro, (int(cx - cw / 2), int(cy - cw / 2)), giro)

    # ---- batidas
    def _cena(self, si: int, tl: float) -> Image.Image:
        sc, o = self.cenas[si], self.o
        kind = sc["kind"]
        img = self.bg.copy()
        ant = self.cenas[si - 1]["kind"] if si > 0 else None
        cur = LAYOUT[kind]
        if ant in LAYOUT:
            k = _out_cubic(tl / 0.4)
            lay = [LAYOUT[ant][i] + (cur[i] - LAYOUT[ant][i]) * k for i in range(4)]
        else:
            lay = list(cur)
        pop = 0.55 + 0.45 * _out_back(tl / 0.5) if si == 0 else 1.0  # já abre com algo na tela (vira a capa)
        t_glob = sc["t0"] + tl
        cx, cy = lay[0], lay[1] + 9 * math.sin(2 * math.pi * t_glob / 2.6)
        _paste_c(img, self._disco_em(1500 * 0.82 * lay[3] * min(pop, 1.15)), cx, cy + 20)
        self._cartao_3d(img, cx, cy, int(lay[2] * pop), t_glob)
        img.paste(self.marca_pilula, ((W - self.marca_pilula.width) // 2, 245), self.marca_pilula)

        w, tm = sc["words"], sc["tm"]
        if kind == "gancho":
            _legenda(img, w, tm, tl, (60, 1190, W - 60, 1530), 100, 60, atraso=-0.3)
        elif kind == "produto":
            _legenda(img, w, tm, tl, (60, 960, W - 60, 1160), 64, 44)
            for i, txt in enumerate(self.chips):
                a = _out_back((tl - (0.9 + 0.55 * i)) / 0.45)
                if a <= 0:
                    continue
                c = _fade(_chip(txt), a * 1.4)
                img.paste(c, ((W - c.width) // 2, int(1195 + i * 112 + (1 - _clamp(a, 0, 1.2)) * 70)), c)
        elif kind == "dica":
            badge = self._selo_chef()
            a = _out_back(tl / 0.5)
            bx = -badge.width + ((W - badge.width) // 2 + badge.width) * _clamp(a, 0, 1.08)
            img.paste(badge, (int(bx), 930), badge)
            _legenda(img, w, tm, tl, (60, 1085, W - 60, 1530), 64, 44, atraso=0.2)
        elif kind == "preco":
            self._preco(img, tl)
            _legenda(img, w, tm, tl, (60, 1290, W - 60, 1520), 72, 48, atraso=0.4)
        if tl < 0.18 and si > 0:  # clarão rápido na troca de batida
            img = Image.blend(img, Image.new("RGB", (W, H), "white"), 0.5 * (1 - tl / 0.18))
        return img

    def _preco(self, img: Image.Image, tl: float) -> None:
        o = self.o
        d = ImageDraw.Draw(img)
        if o.get("preco"):
            por = brl(o["preco"])
            if o.get("preco_de") and o["preco_de"] > o["preco"]:
                de = f"de {brl(o['preco_de'])}"
                a = _clamp((tl - 0.1) / 0.3)
                f = F("medio", 54)
                tw = _MD.textlength(de, font=f)
                d.text((W / 2, 975), de, font=f, fill=_hex(_cor("fundo")) + (int(255 * a),), anchor="mm")
                p = _clamp((tl - 0.45) / 0.35)
                if p > 0:
                    d.line([(W / 2 - tw / 2 - 6, 977), (W / 2 - tw / 2 - 6 + (tw + 12) * _out_cubic(p), 977)],
                           fill=_cor("texto"), width=8)
        else:
            por = "Preço no link"
        pk = _out_back((tl - 0.9) / 0.5)
        if pk > 0:
            _paste_c(img, _scaled(_preco_pilula(por), pk), W / 2, 1130)
        sk = _out_back((tl - 1.5) / 0.5)
        if sk > 0 and self.selo_img is not None:
            _paste_c(img, _scaled(self.selo_img, sk * 0.92), 830, 420)

    def _cta(self, sc: dict, tl: float) -> Image.Image:
        bg = Image.new("RGB", (W, H), _cor("primaria"))
        d = ImageDraw.Draw(bg, "RGBA")
        for r, a in ((900, 28), (640, 28), (380, 28)):
            d.ellipse([W / 2 - r, 620 - r, W / 2 + r, 620 + r], fill=(255, 255, 255, a))

        def pos(t0: float) -> float:
            return _out_back((tl - t0) / 0.45)

        _paste_c(bg, _scaled(_circ(self.avatar, 280), pos(0.15)), W / 2, 560)
        a = pos(0.45)
        if a > 0:
            d.text((W / 2, 820 + (1 - _clamp(a)) * 40), "Quer o link?", font=F("medio", 66), fill=_cor("fundo"), anchor="mm")
        a = pos(0.7)
        if a > 0:
            d.text((W / 2, 925 + (1 - _clamp(a)) * 40), "Procura no Telegram", font=F("titulo", 84), fill="white", anchor="mm")
        a = pos(1.0)
        if a > 0 and self.arroba:
            f = F("titulo", 66)
            w, h = int(_MD.textlength(self.arroba, font=f)) + 110, 130
            pil = _pilula(w, h, _cor("fundo"))
            ImageDraw.Draw(pil).text((w // 2, h // 2 + 3), self.arroba, font=f, fill=_cor("primaria"), anchor="mm")
            _paste_c(bg, _scaled(pil, _clamp(a, 0, 1.1)), W / 2, 1075)
        a = pos(1.3)
        if a > 0 and self.o.get("numero"):
            d.text((W / 2, 1215), f"oferta #{self.o['numero']}", font=F("medio", 54), fill=_cor("fundo"), anchor="mm")
        _legenda(bg, sc["words"], sc["tm"], tl, (90, 1300, W - 90, 1520), 56, 40, atraso=0.3)
        return bg

    def frame(self, t: float) -> Image.Image:
        t = min(max(t, 0.0), self.total - 0.001)
        si = max(i for i, s in enumerate(self.cenas) if t >= s["t0"])
        sc = self.cenas[si]
        tl = t - sc["t0"]
        if sc["kind"] != "cta":
            return self._cena(si, tl)
        base = self._cta(sc, tl)
        if tl < 0.5 and si > 0:  # a chamada entra como um círculo que se abre
            ant = self._cena(si - 1, self.cenas[si - 1]["dur"] - 0.001)
            m = Image.new("L", (W, H), 0)
            r = _out_cubic(tl / 0.5) * 1500
            ImageDraw.Draw(m).ellipse([W / 2 - r, 900 - r, W / 2 + r, 900 + r], fill=255)
            ant.paste(base, (0, 0), m)
            return ant
        return base


# ------------------------------------------------------------------ áudio
def _eventos_sfx(cenas: list[dict], n_chips: int, tem_selo: bool) -> list[tuple[float, str]]:
    ev: list[tuple[float, str]] = []
    for si, sc in enumerate(cenas):
        t0, k = sc["t0"], sc["kind"]
        if si > 0:
            ev.append((t0, "whoosh"))
        if k == "produto":
            ev += [(t0 + 0.9 + 0.55 * i + 0.08, "pop") for i in range(n_chips)]
        elif k == "dica":
            ev.append((t0 + 0.4, "pop"))
        elif k == "preco":
            ev += [(t0 + 0.55, "pop"), (t0 + 0.95, "ding")] + ([(t0 + 1.55, "pop")] if tem_selo else [])
        elif k == "cta":
            ev += [(t0 + 0.2, "pop"), (t0 + 1.05, "pop")]
    return ev


def _montar_audio(tmp: Path, cenas: list[dict], vozes: list[dict] | None, total: float, sfx: list) -> Path:
    def ff(*args):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)

    voz = tmp / "voz.wav"
    if vozes:
        partes = []
        for i, (sc, v) in enumerate(zip(cenas, vozes)):
            p = tmp / f"pad{i}.wav"
            ms = int(LEAD * 1000)
            ff("-i", str(v["arquivo"]), "-af", f"adelay={ms}|{ms},apad", "-t", f"{sc['dur']:.3f}",
               "-ar", str(SR), "-ac", "2", str(p))
            partes.append(p)
        lista = tmp / "lista.txt"
        lista.write_text("".join(f"file '{p.name}'\n" for p in partes))
        ff("-f", "concat", "-safe", "0", "-i", str(lista), "-c:a", "pcm_s16le", str(voz))
    else:
        ff("-f", "lavfi", "-i", f"anullsrc=r={SR}:cl=stereo", "-t", f"{total:.3f}", str(voz))

    entradas, filtros, rotulos = ["-i", str(voz)], ["[0:a]volume=1.0[v]"], ["[v]"]
    idx = 1
    if cfg("marca").get("video_sfx", True) and sfx:
        s = tmp / "sfx.wav"
        _faixa_sfx(sfx, total, s)
        entradas += ["-i", str(s)]
        filtros.append(f"[{idx}:a]volume=0.45[s]")
        rotulos.append("[s]")
        idx += 1
    musica = _musica()
    if musica:
        entradas += ["-stream_loop", "-1", "-i", str(musica)]
        filtros.append(f"[{idx}:a]volume=0.10,afade=t=in:d=0.6,afade=t=out:st={max(total - 0.9, 0):.2f}:d=0.9[m]")
        rotulos.append("[m]")
    saida = tmp / "audio.m4a"
    if len(rotulos) == 1:
        ff(*entradas, "-t", f"{total:.3f}", "-c:a", "aac", "-b:a", "160k", str(saida))
        return saida
    mix = ";".join(filtros) + f";{''.join(rotulos)}amix=inputs={len(rotulos)}:duration=first:normalize=0,alimiter=limit=0.95[a]"
    try:
        ff(*entradas, "-filter_complex", mix, "-map", "[a]", "-t", f"{total:.3f}", "-c:a", "aac", "-b:a", "160k", str(saida))
    except subprocess.CalledProcessError:  # ffmpeg sem amix normalize=0
        mix = mix.replace(":normalize=0", "")
        ff(*entradas, "-filter_complex", mix, "-map", "[a]", "-t", f"{total:.3f}", "-c:a", "aac", "-b:a", "160k", str(saida))
    return saida


# ------------------------------------------------------------------ principal
def video(o: dict) -> Path:
    c = redator.copy(o)
    destino = designer._destino(o, "reel").with_suffix(".mp4")
    beats = _fala(o, c)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        vozes = _tts(beats, tmp)

        # linha do tempo: cada batida dura o tempo da voz (ou o mínimo da animação, o que for maior)
        t0 = 0.0
        for i, b in enumerate(beats):
            words = b["texto"].split()
            voz_dur = _duracao(vozes[i]["arquivo"]) if vozes else max(len(b["texto"]) / 15.0, 1.5)
            b["dur"] = max(LEAD + voz_dur + 0.15 + FOLGA_FINAL.get(b["kind"], 0.0), MINIMO[b["kind"]])
            b["t0"] = t0
            b["words"] = words
            b["tm"] = _tempos_palavras(words, vozes[i]["eventos"] if vozes else [], voz_dur)
            t0 += b["dur"]
        total = t0

        quadros = _Quadros(o, beats)
        sfx = _eventos_sfx(beats, len(quadros.chips), quadros.selo_img is not None)
        audio = _montar_audio(tmp, beats, vozes, total, sfx)

        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
               "-r", str(FPS), "-i", "-", "-i", str(audio), "-c:v", "libx264", "-preset", "fast", "-crf", "21",
               "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", "-movflags", "+faststart", str(destino)]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        try:
            for n in range(int(total * FPS)):
                p.stdin.write(quadros.frame(n / FPS).tobytes())
        finally:
            p.stdin.close()
            ret = p.wait()
        if ret != 0:
            raise RuntimeError(f"ffmpeg terminou com erro {ret}")
    log.info("vídeo pronto: %s (%.1fs, voz=%s)", destino.name, total, "sim" if vozes else "não")
    return destino
