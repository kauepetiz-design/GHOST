"""Testes rápidos: rode `python -m pytest -q` antes de mexer em produção."""
import hashlib
import json
import os

os.environ["DRY_RUN"] = "1"

from ghost import shopee  # noqa: E402
from ghost.core import brl, clean_title, tema_atual  # noqa: E402
import datetime as dt  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402


def test_brl():
    assert brl(1234.5) == "R$ 1.234,50"


def test_clean_title():
    t = clean_title("Mixer De Mão 3 em 1 Inox 800W Com Batedor e Triturador [PROMOÇÃO] Frete Grátis", 38)
    assert len(t) <= 38 and not t.lower().endswith(" com")


def test_tema_black_friday():
    assert tema_atual(dt.datetime(2026, 11, 28, 10, tzinfo=ZoneInfo("America/Sao_Paulo")))["nome"] == "Black Friday"
    assert tema_atual(dt.datetime(2026, 10, 10, 10, tzinfo=ZoneInfo("America/Sao_Paulo")))["nome"] == "10.10 Shopee"


def test_assinatura_shopee(monkeypatch):
    capt = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"generateShortLink": {"shortLink": "https://s.shopee.com.br/x"}}}

    def fake_post(url, data, headers, timeout):
        capt["data"], capt["headers"] = data.decode(), headers
        return Resp()

    s = shopee.Shopee("123", "segredo")
    monkeypatch.setattr(s.s, "post", fake_post)
    assert s.short_link("https://shopee.com.br/p-i.1.2", ["tg"]) == "https://s.shopee.com.br/x"
    auth = capt["headers"]["Authorization"]
    ts = auth.split("Timestamp=")[1].split(",")[0]
    esperado = hashlib.sha256(f"123{ts}{capt['data']}segredo".encode()).hexdigest()
    assert auth.endswith(f"Signature={esperado}")
    assert json.loads(capt["data"])["query"].startswith("mutation")


def test_normalize():
    o = shopee.normalize({"itemId": 9, "productName": "Faca", "priceMin": "80", "priceDiscountRate": 20,
                          "commissionRate": "0.1", "commission": "8", "sales": 500, "ratingStar": "4.9"})
    assert o["preco_de"] == 100.0 and o["comissao_pct"] == 10.0 and o["id"] == "shopee:9"
