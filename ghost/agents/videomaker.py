"""Agente 5 — Editor de Vídeo.

Transforma uma oferta em um vídeo vertical de ~15 s (Reels / TikTok / Shorts): 4 cenas com a foto
do produto em zoom lento, legendas grandes e narração em português (edge-tts, gratuito).
Sem internet para a voz, o vídeo sai só com legendas (áudio mudo).
"""
from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path

from ..core import cfg, get_logger
from . import designer, redator

log = get_logger("videomaker")
FPS = 24


def _duracao(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def _tts(frases: list[str], pasta: Path) -> list[Path] | None:
    """Narração grátis: edge-tts (voz neural) -> gTTS (reserva) -> None (vídeo só com legendas)."""
    voz = cfg("marca").get("voz_video", "pt-BR-AntonioNeural")
    try:
        import edge_tts

        async def go():
            arquivos = []
            for i, f in enumerate(frases):
                p = pasta / f"voz{i}.mp3"
                await edge_tts.Communicate(f, voz, rate="+8%").save(str(p))
                arquivos.append(p)
            return arquivos

        arquivos = asyncio.run(go())
        if all(a.stat().st_size > 0 for a in arquivos):
            return arquivos
    except Exception as e:  # noqa: BLE001
        log.warning("edge-tts falhou (%s); tentando gTTS", e)
    try:
        from gtts import gTTS

        arquivos = []
        for i, f in enumerate(frases):
            p = pasta / f"voz{i}.mp3"
            gTTS(f, lang="pt", tld="com.br").save(str(p))
            arquivos.append(p)
        return arquivos
    except Exception as e:  # noqa: BLE001
        log.warning("gTTS falhou (%s); vídeo sem narração", e)
    return None


def video(o: dict) -> Path:
    c = redator.copy(o)
    cenas = [str(x) for x in c["roteiro"][:4]]
    destino = designer._destino(o, "reel").with_suffix(".mp4")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        vozes = _tts(cenas, tmp)
        duracoes = [(_duracao(v) + 0.35) if vozes else 3.6 for v in (vozes or cenas)]

        # quadros
        n = 0
        for i, (texto, dur) in enumerate(zip(cenas, duracoes)):
            frames = max(int(dur * FPS), 1)
            for k in range(frames):
                zoom = 1.0 + 0.07 * (k / frames)
                designer.quadro_vertical(o, texto, i, len(cenas), zoom).save(tmp / f"f{n:05d}.jpg", quality=88)
                n += 1

        # áudio
        audio = tmp / "audio.m4a"
        if vozes:
            lista = tmp / "lista.txt"
            partes = []
            for i, (v, dur) in enumerate(zip(vozes, duracoes)):
                p = tmp / f"pad{i}.wav"
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(v), "-af", "apad",
                                "-t", f"{dur:.3f}", "-ar", "44100", "-ac", "2", str(p)], check=True)
                partes.append(p)
            lista.write_text("".join(f"file '{p.name}'\n" for p in partes))
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lista),
                            "-c:a", "aac", "-b:a", "160k", str(audio)], check=True, cwd=tmp)
        else:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            "anullsrc=r=44100:cl=stereo", "-t", f"{sum(duracoes):.2f}",
                            "-c:a", "aac", str(audio)], check=True)

        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(tmp / "f%05d.jpg"),
            "-i", str(audio), "-c:v", "libx264", "-preset", "medium", "-crf", "22", "-pix_fmt", "yuv420p",
            "-c:a", "copy", "-shortest", "-movflags", "+faststart", str(destino),
        ], check=True)
    log.info("vídeo pronto: %s (%.1fs)", destino.name, sum(duracoes))
    return destino

