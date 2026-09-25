"""Cliente da Open API de Afiliados da Shopee Brasil (GraphQL).

Autenticação: header
    Authorization: SHA256 Credential={AppId}, Timestamp={ts}, Signature={sha256(AppId+ts+payload+Secret)}
Credenciais: painel de afiliados > "Open API" (precisa pedir liberação ao suporte).
"""
from __future__ import annotations

import hashlib
import json
import time

from .core import env, get_logger, http, retry

log = get_logger("shopee")
ENDPOINT = "https://open-api.affiliate.shopee.com.br/graphql"

PRODUCT_FIELDS = """
      itemId productName productLink offerLink imageUrl
      priceMin priceMax priceDiscountRate sales ratingStar
      commissionRate sellerCommissionRate shopeeCommissionRate commission
      shopId shopName periodStartTime periodEndTime
"""


class ShopeeError(RuntimeError):
    pass


class Shopee:
    def __init__(self, app_id: str | None = None, secret: str | None = None):
        self.app_id = app_id or env("SHOPEE_APP_ID")
        self.secret = secret or env("SHOPEE_SECRET")
        self.s = http()

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.secret)

    def _call(self, query: str, variables: dict | None = None) -> dict:
        body = {"query": query}
        if variables:
            body["variables"] = variables
        payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        ts = str(int(time.time()))
        sig = hashlib.sha256(f"{self.app_id}{ts}{payload}{self.secret}".encode()).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"SHA256 Credential={self.app_id}, Timestamp={ts}, Signature={sig}",
        }

        def go():
            r = self.s.post(ENDPOINT, data=payload.encode("utf-8"), headers=headers, timeout=30)
            r.raise_for_status()
            data = r.json()
            if data.get("errors"):
                err = data["errors"][0]
                code = (err.get("extensions") or {}).get("code")
                if code == 10030:  # rate limit
                    time.sleep(10)
                raise ShopeeError(f"{code}: {err.get('message')}")
            return data["data"]

        return retry(go, tries=3, what="Shopee API")

    # ------------------------------------------------------------ ofertas
    def search(self, keyword: str, limit: int = 20, sort_type: int = 5, page: int = 1) -> list[dict]:
        """sortType: 1=relevância 2=mais vendidos 3=preço desc 4=preço asc 5=maior comissão."""
        # argumentos embutidos na query (evita divergência de tipos de variáveis no schema)
        q = f"""{{
  productOfferV2(keyword:{json.dumps(keyword, ensure_ascii=False)}, sortType:{int(sort_type)}, page:{int(page)}, limit:{int(limit)}){{
    nodes{{{PRODUCT_FIELDS}}}
    pageInfo{{page limit hasNextPage}}
  }}
}}"""
        d = self._call(q)
        return d["productOfferV2"]["nodes"] or []

    def short_link(self, url: str, sub_ids: list[str]) -> str:
        q = (f"mutation{{ generateShortLink(input:{{originUrl:{json.dumps(url)}, "
             f"subIds:{json.dumps(sub_ids[:5])}}}){{ shortLink }} }}")
        d = self._call(q)
        return d["generateShortLink"]["shortLink"]

    # ------------------------------------------------------------ relatórios
    def conversions(self, start_ts: int, end_ts: int, max_pages: int = 20) -> list[dict]:
        fields = """nodes{ purchaseTime clickTime conversionId totalCommission buyerType device utmContent
      orders{ orderId orderStatus items{ itemId itemName itemPrice qty itemTotalCommission } } }
    pageInfo{ hasNextPage scrollId }"""
        out, scroll = [], None
        for _ in range(max_pages):
            sc = f", scrollId:{json.dumps(scroll)}" if scroll else ""
            q = (f"{{ conversionReport(purchaseTimeStart:{int(start_ts)}, purchaseTimeEnd:{int(end_ts)}, "
                 f"limit:500{sc}){{ {fields} }} }}")
            d = self._call(q)["conversionReport"]
            out += d["nodes"] or []
            if not d["pageInfo"]["hasNextPage"]:
                break
            scroll = d["pageInfo"]["scrollId"]
        return out


def normalize(node: dict, keyword: str = "") -> dict:
    """Converte o formato da Shopee no formato único de oferta da Ghost."""
    price = float(node.get("priceMin") or 0)
    disc = int(node.get("priceDiscountRate") or 0)
    original = round(price / (1 - disc / 100), 2) if 0 < disc < 95 else None
    rate = float(node.get("commissionRate") or 0)  # vem como fração, ex.: "0.08"
    if rate > 1:  # algumas respostas já vêm em %
        rate = rate / 100
    return {
        "id": f"shopee:{node['itemId']}",
        "plataforma": "shopee",
        "item_id": str(node["itemId"]),
        "titulo": node.get("productName", ""),
        "preco": price or None,
        "preco_de": original,
        "desconto_pct": disc,
        "comissao_pct": round(rate * 100, 2),
        "comissao_valor": float(node.get("commission") or price * rate),
        "vendas": int(node.get("sales") or 0),
        "nota": float(node.get("ratingStar") or 0),
        "imagem_url": node.get("imageUrl"),
        "link_produto": node.get("productLink"),
        "link": node.get("offerLink") or node.get("productLink"),
        "loja": node.get("shopName", ""),
        "fonte": "api",
        "keyword": keyword,
        "fim_oferta": node.get("periodEndTime"),
    }
