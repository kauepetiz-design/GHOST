"""Cérebro de texto dos agentes: Gemini (camada gratuita) com Groq como reserva.

Sem nenhuma chave configurada, os agentes usam modelos de texto prontos (templates) — a empresa
continua funcionando, só com textos menos variados.
"""
from __future__ import annotations

import json
import re

from .core import env, get_logger, http

log = get_logger("llm")


def _extrair_json(txt: str) -> dict:
    txt = txt.strip()
    txt = re.sub(r"^```(?:json)?|```$", "", txt, flags=re.M).strip()
    ini, fim = txt.find("{"), txt.rfind("}")
    return json.loads(txt[ini:fim + 1])


def _gemini(prompt: str) -> dict:
    key = env("GEMINI_API_KEY")
    modelos = [m for m in (env("GEMINI_MODEL"), "gemini-flash-latest", "gemini-2.5-flash") if m]
    last = None
    for model in dict.fromkeys(modelos):
        try:
            r = http().post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9},
                },
                timeout=60,
            )
            r.raise_for_status()
            txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return _extrair_json(txt)
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("Gemini %s falhou: %s", model, e)
    raise RuntimeError(last)


def _groq(prompt: str) -> dict:
    r = http().post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {env('GROQ_API_KEY')}"},
        json={
            "model": env("GROQ_MODEL") or "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.9,
        },
        timeout=60,
    )
    r.raise_for_status()
    return _extrair_json(r.json()["choices"][0]["message"]["content"])


def disponivel() -> bool:
    return bool(env("GEMINI_API_KEY") or env("GROQ_API_KEY"))


def gerar_json(prompt: str) -> dict | None:
    for nome, fn, chave in (("gemini", _gemini, "GEMINI_API_KEY"), ("groq", _groq, "GROQ_API_KEY")):
        if not env(chave):
            continue
        try:
            return fn(prompt)
        except Exception as e:  # noqa: BLE001
            log.warning("%s indisponível: %s", nome, e)
    return None
