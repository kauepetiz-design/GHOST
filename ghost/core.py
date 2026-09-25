"""Núcleo da Ghost: configuração, armazenamento, logs e utilitários compartilhados pelos agentes."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"          # publicado no GitHub Pages (vitrine + mídia pública)
MEDIA_DIR = DOCS_DIR / "media"
OUT_DIR = ROOT / "out"            # arquivos temporários (não versionados)
FONTS_DIR = ROOT / "assets" / "fonts"
TZ = ZoneInfo("America/Sao_Paulo")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


log = get_logger("ghost")


# ---------------------------------------------------------------- ambiente
def env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name, default)
    return v.strip() if isinstance(v, str) else v


def dry_run() -> bool:
    """Em DRY_RUN nada é publicado nem chamado em APIs pagas/reais — tudo vai para out/."""
    return env("DRY_RUN", "0") in ("1", "true", "yes")


def now() -> dt.datetime:
    fake = env("GHOST_NOW")  # permite simular datas nos testes: 2026-11-27T10:00
    if fake:
        return dt.datetime.fromisoformat(fake).replace(tzinfo=TZ)
    return dt.datetime.now(TZ)


# ---------------------------------------------------------------- config
_cache: dict[str, Any] = {}


def cfg(name: str) -> dict:
    if name not in _cache:
        with open(CONFIG_DIR / f"{name}.yaml", encoding="utf-8") as f:
            _cache[name] = yaml.safe_load(f) or {}
    return _cache[name]


def public_base_url() -> str:
    """URL pública do GitHub Pages, ex.: https://usuario.github.io/ghost-afiliados"""
    url = env("PUBLIC_BASE_URL") or cfg("canais").get("vitrine", {}).get("url_publica", "")
    return url.rstrip("/")


# ---------------------------------------------------------------- armazenamento JSON
def load_json(name: str, default: Any) -> Any:
    p = DATA_DIR / name
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.warning("arquivo %s corrompido; usando padrão", name)
        return default


def save_json(name: str, obj: Any) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DATA_DIR / f".{name}.tmp"
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(DATA_DIR / name)


# ---------------------------------------------------------------- texto
def slugify(text: str, maxlen: int = 40) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return t[:maxlen].strip("-") or "item"


def brl(v: float | None) -> str:
    if v is None:
        return ""
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def clean_title(t: str, maxlen: int = 70) -> str:
    """Títulos de marketplace são enormes e cheios de palavras-chave; encurta para algo legível."""
    t = re.sub(r"[\[\(【].*?[\]\)】]", " ", t)
    t = re.sub(r"\b(frete gr[aá]tis|promo[cç][aã]o|oferta|original|envio imediato|pronta entrega)\b", " ", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" -|,")
    if len(t) > maxlen:
        t = t[:maxlen].rsplit(" ", 1)[0]
    # não termina em palavra de ligação ("Mixer 3 em 1 Com" -> "Mixer 3 em 1")
    soltas = {"com", "de", "da", "do", "para", "p/", "e", "em", "a", "o", "à", "ao", "na", "no", "sem", "kit", "c/", "-", "+", "|"}
    palavras = t.split()
    while len(palavras) > 2 and palavras[-1].lower() in soltas:
        palavras.pop()
    t = " ".join(palavras)
    return t[:1].upper() + t[1:]


# ---------------------------------------------------------------- HTTP
def http() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "ghost-afiliados/1.0"
    return s


def retry(fn, tries: int = 3, wait: float = 3.0, what: str = "chamada"):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("%s falhou (tentativa %d/%d): %s", what, i + 1, tries, e)
            time.sleep(wait * (i + 1))
    raise last  # type: ignore[misc]


# ---------------------------------------------------------------- calendário comercial
def tema_atual(quando: dt.datetime | None = None) -> dict:
    """Retorna o tema sazonal ativo (config/calendario.yaml) ou o padrão."""
    hoje = (quando or now()).date()
    cal = cfg("calendario")
    # temas de 1 dia (10.10, 11.11) vencem temas longos: ordena pelo intervalo mais curto
    candidatos = []
    for t in cal.get("temas", []):
        ini = dt.date.fromisoformat(str(t["inicio"]))
        fim = dt.date.fromisoformat(str(t["fim"]))
        if ini <= hoje <= fim:
            candidatos.append(((fim - ini).days, t))
    if candidatos:
        candidatos.sort(key=lambda x: x[0])
        return candidatos[0][1]
    return cal.get("padrao", {"nome": "padrão", "gancho": "", "palavras_chave": [], "intensidade": 1})
