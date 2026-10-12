"""Agente 5b — Editor de Vídeo, modelo "cena" (produto recortado numa bancada de cozinha, texto em fade).

Reel / TikTok / Shorts vertical de ~15 s, uma cena só:

    GANCHO (já na tela)  >  cede lugar ao nome do PRODUTO  >  que cruza para a DICA DE CHEF  >  PREÇO  >  CHAMADA (pílula do topo)
    (v2: só UM bloco de texto fica na tela por vez, então o produto ganha muito mais altura e vira o protagonista)

- texto SEM caixa: letra grande, contorno escuro grosso e sombra suave, sobre um degradê escuro bem leve no topo
  (legível por qualquer pessoa, até no sol); cada peça entra em fade suave e rápido (0,3 s) quando a voz fala;
- fundo = foto de cozinha vertical (assets/fundos/*.jpg), uma por oferta, escolhida pelo id da oferta;
  o nome do arquivo define o reflexo: inox_*.jpg (reflexo forte) ou madeira_*.jpg (reflexo suave);
- o produto é RECORTADO (veja recorte.py) e fica em pé na bancada, com reflexo e sombras de contato que seguem o contorno;
  a câmera dá um zoom lento na cena toda; sem recorte possível, o produto vai num cartão branco;
- mesma voz, mesmos efeitos sonoros e mesma linha do tempo do videomaker.py (reaproveita as funções dele);
- mesma assinatura: video(o) -> Path, então troca 1 por 1 com videomaker.video(o).

Ajustes rápidos: FADE (duração do fade), ZOOM_CENA (zoom lento), FUNDO_PADRAO["base"] (altura em que o produto pisa).
Config opcional em config/marca.yaml: video_fundo: inox_2 (força um fundo), video_recorte: auto|branco|off.
"""
from __future__ import annotations

import subprocess
import tempfile
import zlib
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat

from ..core import ROOT, brl, cfg, get_logger
from . import designer, logo, recorte, redator
from . import videomaker as vm

log = get_logger("videofaixas")
W, H, FPS = vm.W, vm.H, vm.FPS

FADE = 0.30            # duração do fade de cada peça (s): rápido, mas gradual
ATRASO_TEXTO = 0.10    # o texto entra um pouquinho depois da faixa
ZOOM_CENA = 1.08       # zoom lento de câmera na cena toda (início -> fim do vídeo)
# onde ficam os fundos de cozinha; a 2ª pasta cobre o caso de a pasta ter sido criada dentro de assets/fonts por engano
FUNDOS_DIRS = [ROOT / "assets" / "fundos", ROOT / "assets" / "fonts" / "assets" / "fundos"]
# por fundo: base = y onde o produto "pisa" na bancada (em 1080x1920); reflexo = força do reflexo (0 a 1);
# reflexo_blur = quão fosco é o reflexo; base_frac = só para fotos HORIZONTAIS: altura da foto (0 a 1) onde o produto pisa.
# Os valores vêm do prefixo do nome do arquivo (inox_*, madeira_*); exceções por nome ficam em FUNDOS.
FUNDO_PADRAO = {"base": 1470, "reflexo": 0.22, "reflexo_blur": 4, "base_frac": 0.80}
PREFIXOS = {
    "inox": {"reflexo": 0.50, "reflexo_blur": 2},
    "madeira": {"reflexo": 0.20, "reflexo_blur": 5},
}
FUNDOS: dict[str, dict] = {}
MAX_LARG_PRODUTO = 980   # largura máxima do produto (de 1080): quanto maior, mais ele "enche" a bancada

# zona segura do Reels: a interface do app cobre o topo e a base; os ícones ficam na direita
X_ESQ, X_DIR = 60, 980
TOPO_MARCA, TOPO_TEXTO = 232, 350
ESCURO = (20, 11, 6)
PAD = 30   # margem das camadas de texto, para a sombra desfocada
BASE_PRECO = 1630
MAX_ALTURA_PRODUTO = 800   # altura máxima do produto (de 1920): o resto da tela continua sendo cozinha


# ------------------------------------------------------------------ animação
def _suave(x: float) -> float:
    """Ease-in-out (smoothstep): começa e termina devagar, sem tranco."""
    x = vm._clamp(x)
    return x * x * (3 - 2 * x)


def _alfa(t: float, t0: float, dur: float = FADE) -> float:
    return _suave((t - t0) / dur)


def _colar(base: Image.Image, camada: Image.Image, pos: tuple[int, int], a: float) -> None:
    """Cola uma camada RGBA com opacidade 0..1 (a camada já traz o próprio alfa)."""
    if a <= 0.004:
        return
    if a >= 0.996:
        base.paste(camada, pos, camada)
        return
    mascara = camada.getchannel("A").point(lambda p: int(p * a))
    base.paste(camada, pos, mascara)


# ------------------------------------------------------------------ texto
def _linhas(texto: str, f, max_w: int) -> list[str]:
    linhas, atual = [], ""
    for p in texto.split():
        teste = f"{atual} {p}".strip()
        if vm._MD.textlength(teste, font=f) <= max_w or not atual:
            atual = teste
        else:
            linhas.append(atual)
            atual = p
    if atual:
        linhas.append(atual)
    return linhas


def _ajustar(texto: str, tipo: str, max_w: int, max_lin: int, inicio: int, minimo: int):
    """Maior fonte (de inicio até minimo) em que o texto cabe em max_lin linhas."""
    size = inicio
    while True:
        f = vm.F(tipo, size)
        linhas = _linhas(texto, f, max_w)
        if len(linhas) <= max_lin or size <= minimo:
            if len(linhas) > max_lin:  # não coube nem no mínimo: corta com reticências
                linhas = linhas[:max_lin]
                linhas[-1] = linhas[-1].rstrip(" .,;:!?") + "…"
            return f, linhas, int(size * 1.28)
        size -= 2


def _camada_texto(linhas: list[str], f, lh: int, largura: int, cor: str, altura: int | None = None,
                  extra_baixo: int = 0) -> Image.Image:
    """Texto centralizado com contorno escuro grosso + sombra suave: legível sobre qualquer fundo, sem caixa.
    A camada tem PAD de margem em volta (para a sombra); quem cola deve subtrair PAD da posição."""
    h = altura or lh * len(linhas)
    im = Image.new("RGBA", (largura, h + extra_baixo + 2 * PAD), (0, 0, 0, 0))
    sombra = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d, ds = ImageDraw.Draw(im), ImageDraw.Draw(sombra)
    contorno = max(int(f.size / 9), 5)
    y0 = PAD + (h - lh * len(linhas)) / 2
    for i, linha in enumerate(linhas):
        xy = (largura / 2, y0 + i * lh + lh / 2)
        ds.text((xy[0], xy[1] + 5), linha, font=f, fill=(0, 0, 0, 255), anchor="mm", stroke_width=contorno + 3,
                stroke_fill=(0, 0, 0, 255))
        d.text(xy, linha, font=f, fill=cor, anchor="mm", stroke_width=contorno, stroke_fill=ESCURO + (255,))
    sombra = sombra.filter(ImageFilter.GaussianBlur(11))
    sombra.putalpha(sombra.getchannel("A").point(lambda p: int(p * 0.85)))
    return Image.alpha_composite(sombra, im)


def _escurecer_topo(fundo: Image.Image, forca: float = 0.46, ate: int = 1060) -> Image.Image:
    """Degradê escuro bem leve no topo (sem caixa) para o texto ler bem; some antes de chegar no produto."""
    m = Image.linear_gradient("L").rotate(180).resize((W, ate)).point(lambda p: int(255 * forca * (p / 255) ** 1.35))
    out = fundo.copy()
    out.paste(Image.new("RGB", (W, ate), ESCURO), (0, 0), m)
    return out


def _retangulo(w: int, h: int, cor: str, raio: int = 10, s: int = 3) -> Image.Image:
    im = Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle([0, 0, w * s - 1, h * s - 1], radius=raio * s, fill=vm._hex(cor) + (255,))
    return im.resize((w, h), Image.LANCZOS)


def _sem_ponto(txt: str) -> str:
    return " ".join(str(txt).split()).rstrip(" .")


# ------------------------------------------------------------------ cena (fundo de cozinha + produto recortado)
def _sem_barras(img: Image.Image) -> Image.Image:
    """Corta as bordas lisas (faixas pretas, linhas brancas) que prints de tela costumam trazer."""
    g = img.convert("L")
    w, h = g.size

    def lisa(box) -> bool:
        st = ImageStat.Stat(g.crop(box))
        m, d = st.mean[0], st.stddev[0]
        return (m < 18 and d < 14) or (m > 248 and d < 4)

    t, b, l, r = 0, h, 0, w
    while t < h // 4 and lisa((0, t, w, t + 1)):
        t += 1
    while b > h * 3 // 4 and lisa((0, b - 1, w, b)):
        b -= 1
    while l < w // 4 and lisa((l, 0, l + 1, h)):
        l += 1
    while r > w * 3 // 4 and lisa((r - 1, 0, r, h)):
        r -= 1
    t, b, l, r = t + 2, b - 2, l + 2, r - 2   # folga: a borda costuma vir "suja" por 1-2 px
    return img.crop((l, t, r, b))


def _estender(foto: Image.Image, base_frac: float, base_y: int) -> Image.Image:
    """Foto horizontal (16:9) -> cena 1080x1920. A foto ocupa a largura toda, com a linha `base_frac` da altura
    (onde o produto pisa na mesa) em `base_y`; acima e abaixo ela é prolongada, desfocada e escurecida,
    e as bordas da foto se dissolvem nessa parte (sem emenda visível)."""
    foto = _sem_barras(foto.convert("RGB"))
    ph = int(foto.height * W / foto.width)
    foto = foto.resize((W, ph), Image.LANCZOS)
    y0 = base_y - int(base_frac * ph)
    y1 = y0 + ph
    fe = min(int(ph * 0.10), 90)                       # largura da dissolvência nas bordas da foto
    escuro = (18, 12, 8)
    tela = Image.new("RGB", (W, H), escuro)

    def prolongar(faixa: Image.Image, altura: int) -> Image.Image:
        return faixa.resize((W, max(altura, 1)), Image.BILINEAR).filter(ImageFilter.GaussianBlur(26))

    if y0 > 0:  # em cima: estica as primeiras linhas, desfoca e escurece rumo à borda do vídeo
        alt = min(y0 + fe, H)
        topo = prolongar(foto.crop((0, 0, W, max(int(ph * 0.04), 3))), alt)
        sombra = Image.linear_gradient("L").rotate(180).resize((W, alt)).point(lambda p: int(255 * (p / 255) ** 0.8 * 0.82))
        tela.paste(Image.composite(Image.new("RGB", (W, alt), escuro), topo, sombra), (0, 0))
    if y1 < H:  # embaixo: prolonga a mesa e escurece (também ajuda a ler a legenda do app)
        ini = max(y1 - fe, 0)
        baixo = prolongar(foto.crop((0, ph - max(int(ph * 0.03), 3), W, ph)), H - ini)
        sombra = Image.linear_gradient("L").resize((W, H - ini)).point(lambda p: int(255 * (p / 255) ** 0.7 * 0.90))
        tela.paste(Image.composite(Image.new("RGB", (W, H - ini), escuro), baixo, sombra), (0, ini))
    mascara = Image.new("L", (W, ph), 255)
    if y0 > 0:
        mascara.paste(Image.linear_gradient("L").resize((W, fe)), (0, 0))
    if y1 < H:
        mascara.paste(Image.linear_gradient("L").rotate(180).resize((W, fe)), (0, ph - fe))
    tela.paste(foto, (0, y0), mascara)
    return tela


def fundos_disponiveis() -> list[Path]:
    for pasta in FUNDOS_DIRS:
        achados = sorted(p for p in pasta.glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")) \
            if pasta.is_dir() else []
        if achados:
            return achados
    return []


def _fundo_padrao(o: dict) -> tuple[Image.Image | None, dict]:
    arquivos = fundos_disponiveis()
    if not arquivos:
        return None, FUNDO_PADRAO
    forcado = str(cfg("marca").get("video_fundo") or "")
    escolhido = next((p for p in arquivos if p.stem == forcado), None) \
        or arquivos[zlib.crc32(str(o.get("id", "")).encode()) % len(arquivos)]
    prefixo = next((v for k, v in PREFIXOS.items() if escolhido.stem.lower().startswith(k)), {})
    par = {**FUNDO_PADRAO, **prefixo, **FUNDOS.get(escolhido.stem, {}), "nome": escolhido.stem}
    foto = Image.open(escolhido).convert("RGB")
    if foto.width / foto.height > 0.75:  # horizontal: encaixa na cena vertical
        return _estender(foto, par["base_frac"], par["base"]), par
    img = ImageOps.fit(foto, (W, H), Image.LANCZOS)
    if W / foto.width > 1.2:  # foto menor que o vídeo: devolve um pouco da nitidez perdida na ampliação
        img = img.filter(ImageFilter.UnsharpMask(radius=1.6, percent=55, threshold=2))
    return img, par


def _montar_cena(fundo: Image.Image, prod: Image.Image, base_y: int, reflexo: float, cx: int,
                 desfoque: float = 4) -> Image.Image:
    """Produto em pé na bancada: reflexo, sombra de contato e um leve toque da cor do ambiente."""
    w, h = prod.size
    x0, y0 = cx - w // 2, base_y - h
    cena = fundo.convert("RGBA")

    # integração de cor e luz: o produto assume um pouco da cor e do brilho do ambiente (a luz "envolve" a borda)
    faixa = fundo.crop((max(cx - 300, 0), base_y - 160, min(cx + 300, W), base_y + 120)).resize((1, 1), Image.BOX)
    cor_amb = faixa.getpixel((0, 0))
    rgb = prod.convert("RGB")
    alfa = prod.getchannel("A")
    lum_p = sum(ImageChops.multiply(rgb.convert("L"), alfa).getdata()) / max(sum(alfa.getdata()), 1) * 255
    lum_c = sum(cor_amb) / 3
    fator = min(max(0.85 + 0.15 * (lum_c / max(lum_p, 1)), 0.82), 1.05)   # cena escura -> produto um pouco mais escuro
    rgb = rgb.point(lambda v: min(int(v * fator), 255))
    ambiente = Image.new("RGB", (w, h), cor_amb)
    rgb = Image.blend(rgb, ambiente, 0.08)
    borda = ImageChops.subtract(alfa, alfa.filter(ImageFilter.MinFilter(7))).filter(ImageFilter.GaussianBlur(1.6))
    rgb = Image.composite(Image.blend(rgb, ambiente, 0.45), rgb, borda)
    prod = Image.merge("RGBA", (*rgb.split(), alfa))
    alfa_prod = alfa

    # reflexo na bancada (some em ~40% da altura); a camada tem margem para o desfoque não ser cortado na borda
    if reflexo > 0.02:
        rh = int(h * 0.42)
        mg = int(desfoque * 3) + 4
        ref = ImageOps.flip(prod).crop((0, 0, w, rh))
        fade = Image.linear_gradient("L").rotate(180).resize((w, rh))        # 255 no topo -> 0 embaixo
        ref.putalpha(ImageChops.multiply(ref.getchannel("A"), fade.point(lambda p: int(p * reflexo))))
        grande = Image.new("RGBA", (w + 2 * mg, rh + 2 * mg), (0, 0, 0, 0))
        grande.paste(ref, (mg, mg))
        cena.alpha_composite(grande.filter(ImageFilter.GaussianBlur(desfoque)), (x0 - mg, base_y + 3 - mg))

    # sombras de contato: uma larga e suave sob o conjunto + as que seguem o CONTORNO do produto
    # (toda borda "de baixo" ganha uma faixa escura; assim cada peça de um conjunto pisa na mesa, mesmo em alturas diferentes)
    escuro = Image.new("RGBA", (W, H), (22, 10, 5, 255))
    sombra = Image.new("L", (W, H), 0)
    ImageDraw.Draw(sombra).ellipse([cx - int(w * 0.60), base_y - int(w * 0.06), cx + int(w * 0.66), base_y + int(w * 0.09)], fill=70)
    cena.paste(escuro, (0, 0), sombra.filter(ImageFilter.GaussianBlur(max(w * 0.04, 12))))
    for fracao, blur, forca in ((0.10, 16, 0.38), (0.040, 6, 0.80), (0.014, 2, 0.90)):
        d = max(int(h * fracao), 4)
        mg = blur * 3 + 4                                  # margem: o desfoque não pode ser cortado na borda do produto
        tam = (w + 2 * mg, h + d + 2 * mg)
        baixo, topo = Image.new("L", tam, 0), Image.new("L", tam, 0)
        baixo.paste(alfa_prod, (mg, mg + d))
        topo.paste(alfa_prod, (mg, mg))
        faixa = ImageChops.subtract(baixo, topo).filter(ImageFilter.GaussianBlur(blur))
        m = Image.new("L", (W, H), 0)
        m.paste(faixa.point(lambda v, f=forca: int(v * f)), (x0 - mg, y0 - mg))
        cena.paste(escuro, (0, 0), m)

    cena.alpha_composite(prod, (x0, y0))
    return cena.convert("RGB")


class _Faixas:
    def __init__(self, o: dict, beats: list[dict]):
        marca = cfg("marca")
        self.o = o
        self.t = {b["kind"]: b["t0"] for b in beats}
        self.total = beats[-1]["t0"] + beats[-1]["dur"]
        # os textos das faixas são os MESMOS que a voz fala (menos o ponto final)
        txt = {b["kind"]: b["texto"] for b in beats}
        self.cor_prim, self.cor_fundo, self.cor_txt, self.cor_dest = (
            vm._cor("primaria"), vm._cor("fundo"), vm._cor("texto"), vm._cor("destaque"))
        self.arroba = marca.get("arroba", "")

        self.avatar = logo.avatar(300)

        # ---- UM espaço de texto no topo, compartilhado: gancho -> produto -> dica de chef (um por vez)
        larg = X_DIR - X_ESQ
        f, linhas, lh = _ajustar(_sem_ponto(txt.get("gancho", "")), "titulo", larg - 20, 2, 86, 58)
        bloco_g = lh * len(linhas) + 30                      # texto + traço da marca
        fa, la, lha = _ajustar(_sem_ponto(txt.get("produto", "")), "titulo", larg - 20, 2, 62, 44)
        bloco_a = lha * len(la)
        bloco_b = 0
        if txt.get("dica"):
            fb, lb, lhb = _ajustar(_sem_ponto(txt["dica"]), "titulo", larg - 20, 3, 54, 42)
            bloco_b = 64 + lhb * len(lb)
        h_txt = max(bloco_g, bloco_a, bloco_b)
        tam = (larg, h_txt + 2 * PAD)

        self.gancho_img = Image.new("RGBA", tam, (0, 0, 0, 0))
        y_g = (h_txt - bloco_g) // 2
        self.gancho_img.paste(_camada_texto(linhas, f, lh, larg, self.cor_fundo), (0, y_g))
        ImageDraw.Draw(self.gancho_img).rounded_rectangle(
            [larg // 2 - 90, PAD + y_g + lh * len(linhas) + 10, larg // 2 + 90, PAD + y_g + lh * len(linhas) + 20], radius=5,
            fill=vm._hex(self.cor_prim))
        self.prod_img = _camada_texto(la, fa, lha, larg, self.cor_fundo, altura=h_txt)
        self.dica_img = None
        if bloco_b:
            etq = vm.F("titulo", 30)
            w_e = int(vm._MD.textlength("DICA DE CHEF", font=etq)) + 56
            y_b = (h_txt - bloco_b) // 2
            self.dica_img = Image.new("RGBA", tam, (0, 0, 0, 0))
            self.dica_img.paste(_camada_texto(lb, fb, lhb, larg, self.cor_fundo, altura=bloco_b - 64), (0, y_b + 64))
            pil = vm._pilula(w_e, 50, self.cor_prim)
            ImageDraw.Draw(pil).text((w_e // 2, 26), "DICA DE CHEF", font=etq, fill=self.cor_fundo, anchor="mm")
            self.dica_img.paste(pil, ((larg - w_e) // 2, PAD + y_b), pil)
        self.texto_pos = (X_ESQ, TOPO_TEXTO - PAD)
        base_cre = TOPO_TEXTO + h_txt

        # ---- cena: fundo de cozinha + produto recortado em pé na bancada
        foto = designer.imagem_produto(o)
        fundo, par = _fundo_padrao(o)
        if fundo is None:  # sem fundos de cozinha o resultado fica feio: melhor deixar o modelo clássico assumir
            raise RuntimeError("sem imagens em assets/fundos")
        fundo = _escurecer_topo(fundo)
        prod = recorte.recortar(foto, o.get("id"))
        self.recorte_ok = prod is not None          # False = foto com cenário/selo: vai num cartão branco
        self.fundo_nome = par.get("nome", "")
        self.foto_tam = foto.size
        max_h = min(max(par["base"] - (base_cre + 24), 380), MAX_ALTURA_PRODUTO)
        if prod is not None:
            k = min(MAX_LARG_PRODUTO / prod.width, max_h / prod.height, 2.2)
            prod = prod.resize((max(int(prod.width * k), 2), max(int(prod.height * k), 2)), Image.LANCZOS)
        else:  # sem recorte: foto num cartão branco arredondado
            lado = int(min(MAX_LARG_PRODUTO, max_h) * 0.92)
            prod = vm._cartao(foto).resize((lado, lado), Image.LANCZOS)
        self.cx, self.base_y = W // 2, par["base"]
        self.cena = _montar_cena(fundo, prod, self.base_y, par["reflexo"], self.cx, par["reflexo_blur"])
        self.ancora = (self.cx, self.base_y - prod.height * 0.85)  # ponto parado: perto do topo, então o zoom cresce para baixo (sem invadir o texto)

        # ---- preço (canto inferior direito, como no modelo)
        self._preco(BASE_PRECO)

        # ---- chamada: a pílula da marca, lá no topo, vira "Link no Telegram: @..." na última batida
        self.marca_pilula = self._pilula("Achei na Cozinha".upper(), self.cor_txt, self.cor_fundo)
        self.cta_pilula = self._pilula(f"Link no Telegram: {self.arroba}" if self.arroba else "Link no Telegram",
                                       self.cor_prim, self.cor_fundo)

    def _pilula(self, texto: str, cor_pilula: str, cor_texto: str) -> Image.Image:
        f = vm.F("titulo", 36)
        w, h = int(vm._MD.textlength(texto, font=f)) + 140, 90
        im = vm._pilula(w, h, cor_pilula)
        lg = vm._circ(self.avatar, 72)
        im.paste(lg, (9, 9), lg)
        ImageDraw.Draw(im).text((94, h // 2 + 1), texto, font=f, fill=cor_texto, anchor="lm")
        return im

    def _preco(self, base_y: int) -> None:
        """Preço numa linha só, logo abaixo do produto: "de R$ x" (etiqueta) + "por R$ y" (caixa da marca)."""
        o = self.o
        self.por_box = self.por_txt = self.de_box = self.de_txt = None
        tem_de = bool(o.get("preco") and o.get("preco_de") and o["preco_de"] > o["preco"])
        por = f"por {brl(o['preco'])}" if o.get("preco") else "Preço no link"
        de = f"de {brl(o['preco_de'])}" if tem_de else ""
        tam = 76 if o.get("preco") else 60
        while True:  # encolhe a letra se o preço for muito grande, para a linha caber na tela
            f_por, f_de = vm.F("titulo", tam), vm.F("medio", int(tam * 0.58))
            w_por = int(vm._MD.textlength(por, font=f_por)) + 70
            w_de = int(vm._MD.textlength(de, font=f_de)) + 56 if de else 0
            total = w_por + (w_de + 16 if de else 0)
            if total <= 940 or tam <= 52:
                break
            tam -= 4
        h = int(tam * 1.38)
        x0 = (W - total) // 2
        if de:
            hd = int(h * 0.66)
            self.de_box = _retangulo(w_de, hd, self.cor_fundo, 6)
            self.de_txt = Image.new("RGBA", (w_de, hd), (0, 0, 0, 0))
            ImageDraw.Draw(self.de_txt).text((w_de // 2, hd // 2 + 2), de, font=f_de, fill=self.cor_txt, anchor="mm")
            self.de_pos = (x0, base_y - hd)
            x0 += w_de + 16
        self.por_box = _retangulo(w_por, h, self.cor_prim, 6)
        self.por_txt = Image.new("RGBA", (w_por, h), (0, 0, 0, 0))
        ImageDraw.Draw(self.por_txt).text((w_por // 2, h // 2 + 3), por, font=f_por, fill=self.cor_fundo, anchor="mm")
        self.por_pos = (x0, base_y - h)

    # ---- um quadro
    def frame(self, t: float) -> Image.Image:
        t = min(max(t, 0.0), self.total - 0.001)
        p = t / self.total
        z = 1 + (ZOOM_CENA - 1) * p
        ax, ay = self.ancora   # ponto que fica parado na tela; o resto se afasta dele
        img = self.cena.resize((W, H), Image.BILINEAR,
                               box=(ax - ax / z, ay - ay / z, ax + (W - ax) / z, ay + (H - ay) / z))

        # topo: pílula da marca; na última batida ela cruza para a chamada do Telegram
        t_cta = self.t.get("cta", self.total)
        a_cta = _alfa(t, t_cta + 0.10)
        for pil, a in ((self.marca_pilula, 1 - a_cta), (self.cta_pilula, a_cta)):
            _colar(img, pil, ((W - pil.width) // 2, TOPO_MARCA), a)

        # um bloco de texto por vez: o gancho (já na tela no 1º quadro, vira a capa) cede lugar ao produto, que cruza para a dica
        t_prod = self.t.get("produto")
        t_dica = self.t.get("dica") if self.dica_img is not None else None
        t1 = t_prod if t_prod is not None else t_dica
        _colar(img, self.gancho_img, self.texto_pos, 1.0 if t1 is None else 1 - _alfa(t, t1))
        if t_prod is not None:
            a_p = _alfa(t, t_prod + 0.05)
            if t_dica is not None:
                a_p *= 1 - _alfa(t, t_dica)
            _colar(img, self.prod_img, self.texto_pos, a_p)
        if t_dica is not None:
            _colar(img, self.dica_img, self.texto_pos, _alfa(t, t_dica + 0.05))

        # preço: "de" primeiro, "por" em seguida; caixa e texto em fades levemente defasados
        t_pre = self.t.get("preco", self.total)
        if self.de_box is not None:
            _colar(img, self.de_box, self.de_pos, _alfa(t, t_pre + 0.10))
            _colar(img, self.de_txt, self.de_pos, _alfa(t, t_pre + 0.10 + ATRASO_TEXTO))
        _colar(img, self.por_box, self.por_pos, _alfa(t, t_pre + 0.45))
        _colar(img, self.por_txt, self.por_pos, _alfa(t, t_pre + 0.45 + ATRASO_TEXTO))

        return img

    def sfx(self) -> list[tuple[float, str]]:
        ev = []
        if "produto" in self.t:
            ev.append((self.t["produto"] + 0.05, "pop"))
        if self.dica_img is not None and "dica" in self.t:
            ev.append((self.t["dica"] + 0.05, "pop"))
        if "preco" in self.t:
            ev += [(self.t["preco"] + 0.45, "pop"), (self.t["preco"] + 0.85, "ding")]
        if "cta" in self.t:
            ev.append((self.t["cta"] + 0.10, "pop"))
        return ev


# ------------------------------------------------------------------ prévia (conferência semanal)
def previa(o: dict) -> tuple[Image.Image, dict]:
    """Quadro final do Reel, sem renderizar o vídeo (uns 5 s), + dados para o resumo de conferência."""
    c = redator.copy(o)
    beats = vm._fala(o, c)
    t0 = 0.0
    for b in beats:
        b["dur"], b["t0"] = vm.MINIMO[b["kind"]], t0
        t0 += b["dur"]
    fx = _Faixas(o, beats)
    t = fx.t.get("preco", fx.total * 0.7) + 1.0       # dica e preço já visíveis
    info = {"recorte": fx.recorte_ok, "fundo": fx.fundo_nome, "foto_px": fx.foto_tam}
    return fx.frame(t), info


# ------------------------------------------------------------------ principal
def video(o: dict) -> Path:
    c = redator.copy(o)
    destino = designer._destino(o, "reel").with_suffix(".mp4")
    beats = vm._fala(o, c)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        vozes = vm._tts(beats, tmp)

        # linha do tempo: cada batida dura o tempo da voz (ou o mínimo da animação, o que for maior)
        t0 = 0.0
        for i, b in enumerate(beats):
            voz_dur = vm._duracao(vozes[i]["arquivo"]) if vozes else max(len(b["texto"]) / 15.0, 1.5)
            b["dur"] = max(vm.LEAD + voz_dur + 0.15 + vm.FOLGA_FINAL.get(b["kind"], 0.0), vm.MINIMO[b["kind"]])
            b["t0"] = t0
            t0 += b["dur"]
        total = t0

        faixas = _Faixas(o, beats)
        audio = vm._montar_audio(tmp, beats, vozes, total, faixas.sfx() if cfg("marca").get("video_sfx", True) else [])

        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
               "-r", str(FPS), "-i", "-", "-i", str(audio), "-c:v", "libx264", "-preset", "fast", "-crf", "21",
               "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", "-movflags", "+faststart", str(destino)]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        try:
            for n in range(int(total * FPS)):
                p.stdin.write(faixas.frame(n / FPS).tobytes())
        finally:
            p.stdin.close()
            ret = p.wait()
        if ret != 0:
            raise RuntimeError(f"ffmpeg terminou com erro {ret}")
    log.info("vídeo (faixas) pronto: %s (%.1fs, voz=%s)", destino.name, total, "sim" if vozes else "não")
    return destino
