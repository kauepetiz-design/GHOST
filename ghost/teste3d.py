"""Teste 3D da Ghost (não faz parte do fluxo de publicação).

Descobre quais modelos de IMAGEM (Nano Banana) e de VÍDEO (Veo) a sua chave do Gemini enxerga,
gera material de UM produto do data/curadoria.csv e manda tudo para o seu Telegram, junto com
tempo, consumo (tokens) e qualquer erro de cota/faturamento. Nada é publicado nos canais.

Como roda: Actions > "Teste 3D" > Run workflow.  Variáveis (preenchidas pelo workflow):
  T3D_INDICE         número do produto no curadoria.csv (0 = primeiro ativo)
  T3D_IMAGEM         "true" para gerar 3 ângulos do produto (3 chamadas)
  T3D_VIDEO          "true" para gerar 1 vídeo de giro com Veo (PODE COBRAR)
  T3D_MODELO_IMAGEM  nome exato do modelo (vazio = o mais barato que a chave enxergar)
  T3D_MODELO_VIDEO   idem para vídeo
  T3D_PRODUTO_URL    opcional: URL de uma foto qualquer, em vez do CSV
"""
from __future__ import annotations

import base64
import csv
import io
import json
import time
from pathlib import Path

from PIL import Image

from .agents import telegram
from .core import DATA_DIR, OUT_DIR, env, get_logger, http

log = get_logger("teste3d")
API = "https://generativelanguage.googleapis.com/v1beta"
PASTA = OUT_DIR / "teste3d"

ANGULOS = [
    ("3/4 esquerda", "a three-quarter view from the left (about 45 degrees)"),
    ("lateral", "a pure side profile view (90 degrees)"),
    ("3/4 direita", "a three-quarter view from the right (about 45 degrees)"),
]
PROMPT_ANGULO = (
    "Using the attached product photo as the ONLY reference, render the SAME product ({titulo}) "
    "seen from {angulo}. Keep the exact shape, proportions, colors, materials, handles, buttons, "
    "printed text and logos identical to the photo. Pure white seamless studio background, soft "
    "natural shadow under the product, centered, square image. Do not add, remove or redesign any "
    "part. Do not invent text or logos."
)
PROMPT_VIDEO = (
    "Smooth, slow 360-degree turntable rotation of the exact product in the image, on a clean "
    "seamless white background. Static camera, the product rotates around its vertical axis, soft "
    "studio lighting, realistic soft shadow. Keep the product shape, colors, text and logos "
    "identical to the image. No hands, no new objects, no text overlays, no music."
)


# ------------------------------------------------------------------ utilidades
def _headers() -> dict:
    return {"x-goog-api-key": env("GEMINI_API_KEY") or "", "Content-Type": "application/json"}


def _erro(r) -> str:
    try:
        j = r.json()
        return f"HTTP {r.status_code}: {j.get('error', {}).get('message', j)}"[:400]
    except Exception:  # noqa: BLE001
        return f"HTTP {r.status_code}: {r.text[:300]}"


def _foto_tg(p: Path, legenda: str) -> None:
    chat = env("TELEGRAM_OWNER_CHAT_ID") or "dono"
    try:
        with open(p, "rb") as f:
            telegram._api("sendPhoto", {"chat_id": chat, "caption": legenda[:1024]}, {"photo": f})
    except Exception as e:  # noqa: BLE001
        log.warning("sendPhoto falhou (%s); enviando como documento", e)
        with open(p, "rb") as f:
            telegram._api("sendDocument", {"chat_id": chat, "caption": legenda[:1024]}, {"document": f})


def _escolher_produto() -> tuple[str, str]:
    """Devolve (titulo, url_da_imagem)."""
    url = env("T3D_PRODUTO_URL")
    if url:
        return "produto de teste", url
    arq = DATA_DIR / "curadoria.csv"
    with open(arq, encoding="utf-8-sig", newline="") as f:
        linhas = [r for r in csv.DictReader(f) if (r.get("ativo") or "").strip().lower() in ("sim", "s", "1", "true")]
    if not linhas:
        raise RuntimeError("curadoria.csv sem produtos ativos")
    i = int(env("T3D_INDICE", "0") or 0) % len(linhas)
    r = linhas[i]
    return r["titulo"].strip(), r["imagem_url"].strip()


def _baixar_foto(url: str) -> bytes:
    r = http().get(url, timeout=40)
    r.raise_for_status()
    im = Image.open(io.BytesIO(r.content)).convert("RGB")
    im.thumbnail((1024, 1024))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue()


# ------------------------------------------------------------------ descoberta de modelos
def listar_modelos() -> list[dict]:
    modelos, token = [], None
    while True:
        params = {"pageSize": 200, **({"pageToken": token} if token else {})}
        r = http().get(f"{API}/models", headers=_headers(), params=params, timeout=40)
        if r.status_code != 200:
            raise RuntimeError("não consegui listar modelos — " + _erro(r))
        j = r.json()
        modelos += j.get("models", [])
        token = j.get("nextPageToken")
        if not token:
            return modelos


def _nome(m: dict) -> str:
    return m["name"].split("/", 1)[-1]


def candidatos(modelos: list[dict]) -> tuple[list[str], list[str]]:
    imagem = [_nome(m) for m in modelos
              if "image" in _nome(m) and "generateContent" in m.get("supportedGenerationMethods", [])]
    video = [_nome(m) for m in modelos
             if "veo" in _nome(m) and "predictLongRunning" in m.get("supportedGenerationMethods", [])]
    # mais barato primeiro: "flash"/"lite"/"fast" antes de "pro"
    chave = lambda n: (("pro" in n), not any(k in n for k in ("flash", "lite", "fast")), n)  # noqa: E731
    return sorted(imagem, key=chave), sorted(video, key=chave)


# ------------------------------------------------------------------ imagem (Nano Banana)
def gerar_imagem(modelo: str, prompt: str, foto: bytes) -> tuple[bytes | None, dict]:
    corpo = {
        "contents": [{"parts": [{"text": prompt},
                                {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(foto).decode()}}]}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }
    t0 = time.time()
    r = http().post(f"{API}/models/{modelo}:generateContent", headers=_headers(), json=corpo, timeout=180)
    if r.status_code == 400 and "modalit" in r.text.lower():  # alguns modelos só aceitam IMAGE
        corpo["generationConfig"]["responseModalities"] = ["IMAGE"]
        r = http().post(f"{API}/models/{modelo}:generateContent", headers=_headers(), json=corpo, timeout=180)
    info = {"segundos": round(time.time() - t0, 1)}
    if r.status_code != 200:
        info["erro"] = _erro(r)
        return None, info
    j = r.json()
    info["uso"] = j.get("usageMetadata", {})
    for cand in j.get("candidates", []):
        for p in cand.get("content", {}).get("parts", []):
            d = p.get("inlineData") or p.get("inline_data")
            if d and d.get("data"):
                return base64.b64decode(d["data"]), info
    info["erro"] = "resposta sem imagem: " + json.dumps(j)[:300]
    return None, info


def teste_imagem(modelo: str, titulo: str, foto: bytes) -> list[str]:
    linhas = []
    for nome, desc in ANGULOS:
        prompt = PROMPT_ANGULO.format(titulo=titulo[:80], angulo=desc)
        png, info = gerar_imagem(modelo, prompt, foto)
        if png:
            p = PASTA / f"angulo_{nome.replace('/', '-').replace(' ', '_')}.png"
            p.write_bytes(png)
            uso = info.get("uso", {})
            linhas.append(f"✅ {nome}: {info['segundos']}s, tokens {uso.get('totalTokenCount', '?')}")
            _foto_tg(p, f"🧪 {modelo} · {nome}\nCompare com a foto original: forma, cor, alça, texto, logo.")
        else:
            linhas.append(f"❌ {nome}: {info.get('erro')}")
            if "429" in info.get("erro", "") or "quota" in info.get("erro", "").lower():
                linhas.append("   ↳ cota/faturamento: provável que este modelo exija faturamento ativo na API.")
                break  # não adianta insistir
    return linhas


# ------------------------------------------------------------------ vídeo (Veo)
def gerar_video(modelo: str, prompt: str, foto: bytes, limite_s: int = 600) -> tuple[Path | None, dict]:
    img = {"bytesBase64Encoded": base64.b64encode(foto).decode(), "mimeType": "image/jpeg"}
    corpo = {"instances": [{"prompt": prompt, "image": img}],
             "parameters": {"aspectRatio": "9:16", "durationSeconds": 4}}
    t0 = time.time()
    url = f"{API}/models/{modelo}:predictLongRunning"
    r = http().post(url, headers=_headers(), json=corpo, timeout=120)
    if r.status_code == 400:  # parâmetro que o modelo não aceita: tenta o mínimo
        corpo["parameters"] = {"aspectRatio": "9:16"}
        r = http().post(url, headers=_headers(), json=corpo, timeout=120)
    info: dict = {}
    if r.status_code != 200:
        info["erro"] = _erro(r)
        return None, info
    op = r.json().get("name")
    while time.time() - t0 < limite_s:
        time.sleep(10)
        s = http().get(f"{API}/{op}", headers=_headers(), timeout=60)
        if s.status_code != 200:
            info["erro"] = _erro(s)
            return None, info
        j = s.json()
        if j.get("done"):
            info["segundos"] = round(time.time() - t0, 1)
            if j.get("error"):
                info["erro"] = json.dumps(j["error"])[:300]
                return None, info
            resp = j.get("response", {}).get("generateVideoResponse", {})
            amostras = resp.get("generatedSamples") or []
            if not amostras:
                info["erro"] = "sem vídeo: " + json.dumps(resp.get("raiMediaFilteredReasons", resp))[:300]
                return None, info
            uri = amostras[0]["video"]["uri"]
            v = http().get(uri, headers={"x-goog-api-key": env("GEMINI_API_KEY") or ""}, timeout=300, allow_redirects=True)
            if v.status_code != 200:
                info["erro"] = _erro(v)
                return None, info
            p = PASTA / "giro_veo.mp4"
            p.write_bytes(v.content)
            info["mb"] = round(len(v.content) / 1e6, 1)
            return p, info
    info["erro"] = f"demorou mais de {limite_s}s"
    return None, info


def teste_video(modelo: str, foto: bytes) -> list[str]:
    p, info = gerar_video(modelo, PROMPT_VIDEO, foto)
    if p:
        telegram.video_para_dono(p, f"🧪 {modelo}\nGiro 360° gerado a partir da foto. Compare com o produto real.")
        return [f"✅ vídeo: {info['segundos']}s de espera, {info['mb']} MB"]
    return [f"❌ vídeo: {info.get('erro')}"]


# ------------------------------------------------------------------ principal
def main() -> None:
    PASTA.mkdir(parents=True, exist_ok=True)
    rel = ["🧪 Teste 3D da Ghost"]
    try:
        if not env("GEMINI_API_KEY"):
            raise RuntimeError("secret GEMINI_API_KEY ausente no workflow")
        titulo, url = _escolher_produto()
        rel.append(f"Produto: {titulo[:70]}")
        modelos = listar_modelos()
        img_mods, vid_mods = candidatos(modelos)
        rel.append("Modelos de imagem que a chave vê: " + (", ".join(img_mods) or "nenhum"))
        rel.append("Modelos de vídeo (Veo) que a chave vê: " + (", ".join(vid_mods) or "nenhum"))
        foto = _baixar_foto(url)
        (PASTA / "original.jpg").write_bytes(foto)
        _foto_tg(PASTA / "original.jpg", "📷 Foto original (referência para comparar)")

        if env("T3D_IMAGEM", "true") == "true":
            m = env("T3D_MODELO_IMAGEM") or (img_mods[0] if img_mods else "")
            if m:
                rel.append(f"\n🖼 IMAGEM com {m}")
                rel += teste_imagem(m, titulo, foto)
            else:
                rel.append("\n🖼 IMAGEM: nenhum modelo de imagem disponível para a chave")
        if env("T3D_VIDEO", "false") == "true":
            m = env("T3D_MODELO_VIDEO") or (vid_mods[0] if vid_mods else "")
            if m:
                rel.append(f"\n🎬 VÍDEO com {m}")
                rel += teste_video(m, foto)
            else:
                rel.append("\n🎬 VÍDEO: nenhum modelo Veo disponível para a chave")
    except Exception as e:  # noqa: BLE001
        log.exception("teste falhou")
        rel.append(f"\n❌ Falhou: {e}")
    rel.append("\n💰 Custo: confira o uso no Google AI Studio (painel de uso/faturamento) antes e depois deste teste.")
    telegram.para_dono("\n".join(rel))
    print("\n".join(rel))


if __name__ == "__main__":
    main()
