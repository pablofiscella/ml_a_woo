"""
WooCommerce ↔ Mercado Libre Sync Engine
Mercado Libre es la fuente de verdad para productos, stock y precios.
El scheduler corre import_from_ml en cada ciclo para mantener Woo actualizado.
Las órdenes de ML también descuentan stock en Woo.
"""

import os
import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional

import requests
from requests.auth import HTTPBasicAuth

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("sync.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ─── Config (se sobreescribe desde config.json) ────────────────────────────────
CONFIG_PATH = "config.json"

def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)

# ─── Base de datos local (evita bucles de sync) ────────────────────────────────
def init_db(db_path: str = "sync.db"):
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS product_map (
            woo_id       INTEGER PRIMARY KEY,
            ml_id        TEXT UNIQUE,
            sku          TEXT,
            last_synced  TEXT
        );
        CREATE TABLE IF NOT EXISTS sync_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ts           TEXT,
            direction    TEXT,   -- WOO->ML | ML->WOO
            entity       TEXT,   -- product | stock | price | order
            ref_id       TEXT,
            status       TEXT,   -- ok | error
            detail       TEXT
        );
        CREATE TABLE IF NOT EXISTS last_run (
            key          TEXT PRIMARY KEY,
            value        TEXT
        );
    """)
    con.commit()
    return con


def log_event(con, direction, entity, ref_id, status, detail=""):
    con.execute(
        "INSERT INTO sync_log(ts, direction, entity, ref_id, status, detail) VALUES(?,?,?,?,?,?)",
        (datetime.now().isoformat(), direction, entity, str(ref_id), status, detail)
    )
    con.commit()


def get_last_run(con, key) -> Optional[str]:
    row = con.execute("SELECT value FROM last_run WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_last_run(con, key, value):
    con.execute("INSERT OR REPLACE INTO last_run(key, value) VALUES(?,?)", (key, value))
    con.commit()


# ─── WooCommerce API ───────────────────────────────────────────────────────────
class WooAPI:
    def __init__(self, cfg: dict):
        self.base = cfg["woo_url"].rstrip("/") + "/wp-json/wc/v3"
        self.auth = HTTPBasicAuth(cfg["woo_key"], cfg["woo_secret"])
        self.session = requests.Session()

    def _get(self, endpoint, params=None):
        url = f"{self.base}/{endpoint}"
        resp = self.session.get(url, auth=self.auth, params=params or {}, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _put(self, endpoint, data):
        url = f"{self.base}/{endpoint}"
        resp = self.session.put(url, auth=self.auth, json=data, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _post(self, endpoint, data):
        url = f"{self.base}/{endpoint}"
        resp = self.session.post(url, auth=self.auth, json=data, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_products(self, modified_after: str = None, per_page=100):
        """Trae todos los productos (paginado)."""
        page, products = 1, []
        while True:
            params = {"per_page": per_page, "page": page, "status": "publish"}
            if modified_after:
                params["modified_after"] = modified_after
            batch = self._get("products", params)
            if not batch:
                break
            products.extend(batch)
            page += 1
        return products

    def get_product(self, woo_id):
        return self._get(f"products/{woo_id}")

    def update_stock(self, woo_id, qty):
        return self._put(f"products/{woo_id}", {
            "stock_quantity": qty,
            "manage_stock": True
        })

    def update_price(self, woo_id, regular_price, sale_price=None):
        data = {"regular_price": str(regular_price)}
        if sale_price:
            data["sale_price"] = str(sale_price)
        return self._put(f"products/{woo_id}", data)

    def get_orders(self, status="processing", after: str = None):
        params = {"status": status, "per_page": 50}
        if after:
            params["after"] = after
        return self._get("orders", params)


# ─── Mercado Libre API ─────────────────────────────────────────────────────────
class MercadoLibreAPI:
    BASE = "https://api.mercadolibre.com"

    def __init__(self, cfg: dict):
        self.app_id = cfg["ml_app_id"]
        self.secret  = cfg["ml_secret"]
        self.token   = cfg.get("ml_access_token", "")
        self.refresh = cfg.get("ml_refresh_token", "")
        self.seller_id = cfg.get("ml_seller_id", "")
        self.session = requests.Session()

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def refresh_token(self):
        """Renueva el access token usando el refresh token."""
        resp = requests.post(f"{self.BASE}/oauth/token", data={
            "grant_type": "refresh_token",
            "client_id": self.app_id,
            "client_secret": self.secret,
            "refresh_token": self.refresh
        })
        resp.raise_for_status()
        data = resp.json()
        self.token   = data["access_token"]
        self.refresh = data["refresh_token"]
        # Persistir en config
        cfg = load_config()
        cfg["ml_access_token"]  = self.token
        cfg["ml_refresh_token"] = self.refresh
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
        log.info("ML token renovado.")
        return self.token

    def _get(self, path, params=None):
        r = self.session.get(f"{self.BASE}{path}", headers=self._headers(), params=params, timeout=30)
        if r.status_code == 401:
            self.refresh_token()
            r = self.session.get(f"{self.BASE}{path}", headers=self._headers(), params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def _put(self, path, data):
        r = self.session.put(f"{self.BASE}{path}", headers=self._headers(), json=data, timeout=30)
        if r.status_code == 401:
            self.refresh_token()
            r = self.session.put(f"{self.BASE}{path}", headers=self._headers(), json=data, timeout=30)
        r.raise_for_status()
        return r.json()

    def _post(self, path, data):
        r = self.session.post(f"{self.BASE}{path}", headers=self._headers(), json=data, timeout=30)
        if r.status_code == 401:
            self.refresh_token()
            r = self.session.post(f"{self.BASE}{path}", headers=self._headers(), json=data, timeout=30)
        r.raise_for_status()
        return r.json()

    def get_my_items(self):
        """Lista todos los items del vendedor."""
        items, offset = [], 0
        while True:
            data = self._get(f"/users/{self.seller_id}/items/search",
                             params={"limit": 100, "offset": offset})
            ids = data.get("results", [])
            if not ids:
                break
            items.extend(ids)
            offset += len(ids)
            if offset >= data["paging"]["total"]:
                break
        return items

    def get_item(self, ml_id):
        return self._get(f"/items/{ml_id}")

    def update_item(self, ml_id, payload: dict):
        return self._put(f"/items/{ml_id}", payload)

    def create_item(self, payload: dict):
        return self._post("/items", payload)

    def get_recent_orders(self, seller_id, after_date: str = None):
        """Órdenes pagadas recientes."""
        params = {"seller": seller_id, "order.status": "paid", "sort": "date_desc"}
        if after_date:
            params["order.date_created.from"] = after_date
        return self._get("/orders/search", params=params)

    def close_item(self, ml_id):
        """Pausa la publicación (stock 0)."""
        return self._put(f"/items/{ml_id}", {"status": "paused"})

    def activate_item(self, ml_id):
        return self._put(f"/items/{ml_id}", {"status": "active"})


# ─── Sync: WooCommerce → Mercado Libre ────────────────────────────────────────
def woo_to_ml_sync(woo: WooAPI, ml: MercadoLibreAPI, con: sqlite3.Connection, cfg: dict):
    """
    - Si el producto ya existe en ML (por SKU mapeado) → actualiza stock y precio.
    - Si es nuevo en Woo y no está en ML → lo publica (si cfg["auto_publish"] = true).
    """
    last = get_last_run(con, "woo_to_ml_last")
    log.info(f"WOO→ML: buscando cambios desde {last or 'siempre'}...")

    products = woo.get_products(modified_after=last)
    log.info(f"  {len(products)} productos a evaluar.")

    for p in products:
        woo_id = p["id"]
        sku    = p.get("sku", "")
        stock  = p.get("stock_quantity") or 0
        price  = p.get("regular_price", "0")
        name   = p["name"]

        # Buscar mapeo existente
        row = con.execute("SELECT ml_id FROM product_map WHERE woo_id=?", (woo_id,)).fetchone()

        if row:
            ml_id = row[0]
            try:
                payload = {"price": float(price), "available_quantity": int(stock)}
                if stock == 0:
                    ml.close_item(ml_id)
                    payload.pop("available_quantity", None)
                else:
                    ml.activate_item(ml_id)
                    ml.update_item(ml_id, payload)
                con.execute("UPDATE product_map SET last_synced=? WHERE woo_id=?",
                            (datetime.now().isoformat(), woo_id))
                con.commit()
                log_event(con, "WOO->ML", "product", woo_id, "ok", f"ml_id={ml_id} stock={stock} price={price}")
                log.info(f"  ✓ {name} → ML {ml_id}: stock={stock}, precio={price}")
            except Exception as e:
                log_event(con, "WOO->ML", "product", woo_id, "error", str(e))
                log.error(f"  ✗ {name}: {e}")

        elif cfg.get("auto_publish_new", False) and sku:
            # Publicar nuevo producto en ML
            try:
                item = build_ml_item(p, cfg)
                result = ml.create_item(item)
                ml_id  = result["id"]
                con.execute(
                    "INSERT OR REPLACE INTO product_map(woo_id, ml_id, sku, last_synced) VALUES(?,?,?,?)",
                    (woo_id, ml_id, sku, datetime.now().isoformat())
                )
                con.commit()
                log_event(con, "WOO->ML", "new_product", woo_id, "ok", f"ml_id={ml_id}")
                log.info(f"  ✚ Publicado en ML: {name} → {ml_id}")
            except Exception as e:
                log_event(con, "WOO->ML", "new_product", woo_id, "error", str(e))
                log.error(f"  ✗ No se pudo publicar {name}: {e}")

    set_last_run(con, "woo_to_ml_last", datetime.utcnow().isoformat() + "Z")


def build_ml_item(woo_product: dict, cfg: dict) -> dict:
    """Construye el payload para crear un item en ML a partir de un producto Woo."""
    images = [{"source": img["src"]} for img in woo_product.get("images", [])[:12]]
    return {
        "title": woo_product["name"],
        "category_id": cfg.get("ml_default_category", "MLA1055"),  # Personalizar
        "price": float(woo_product.get("regular_price", "0")),
        "currency_id": cfg.get("ml_currency", "ARS"),
        "available_quantity": int(woo_product.get("stock_quantity") or 0),
        "buying_mode": "buy_it_now",
        "condition": "new",
        "listing_type_id": cfg.get("ml_listing_type", "gold_special"),
        "description": {"plain_text": woo_product.get("short_description", "")[:2000]},
        "pictures": images,
        "sale_terms": [{"id": "WARRANTY_TYPE", "value_name": "Garantía del vendedor"}],
    }


# ─── Sync: Mercado Libre → WooCommerce (solo órdenes/ventas) ──────────────────
def ml_orders_to_woo(woo: WooAPI, ml: MercadoLibreAPI, con: sqlite3.Connection, cfg: dict):
    """
    Trae órdenes pagadas de ML y descuenta stock en WooCommerce.
    """
    last = get_last_run(con, "ml_orders_last")
    log.info(f"ML→WOO órdenes: desde {last or 'última hora'}...")

    after = last or (datetime.utcnow() - timedelta(hours=2)).isoformat() + "Z"
    try:
        data = ml.get_recent_orders(cfg["ml_seller_id"], after_date=after)
    except Exception as e:
        log.error(f"Error al obtener órdenes ML: {e}")
        return

    orders = data.get("results", [])
    log.info(f"  {len(orders)} órdenes ML a procesar.")

    for order in orders:
        order_id = order["id"]
        # Evitar procesar la misma orden dos veces
        already = con.execute("SELECT id FROM sync_log WHERE direction='ML->WOO' AND ref_id=? AND status='ok'",
                               (str(order_id),)).fetchone()
        if already:
            continue

        for item in order.get("order_items", []):
            ml_id = item["item"]["id"]
            qty_sold = item["quantity"]

            row = con.execute("SELECT woo_id FROM product_map WHERE ml_id=?", (ml_id,)).fetchone()
            if not row:
                log.warning(f"  ⚠ ML item {ml_id} no mapeado a WooCommerce.")
                continue

            woo_id = row[0]
            try:
                prod = woo.get_product(woo_id)
                current_stock = int(prod.get("stock_quantity") or 0)
                new_stock = max(0, current_stock - qty_sold)
                woo.update_stock(woo_id, new_stock)
                log_event(con, "ML->WOO", "order", order_id, "ok",
                          f"ml_id={ml_id} woo_id={woo_id} -{qty_sold} stock={new_stock}")
                log.info(f"  ✓ Venta ML orden {order_id}: woo_id={woo_id} stock {current_stock}→{new_stock}")
            except Exception as e:
                log_event(con, "ML->WOO", "order", order_id, "error", str(e))
                log.error(f"  ✗ Error actualizando stock para orden {order_id}: {e}")

    set_last_run(con, "ml_orders_last", datetime.utcnow().isoformat() + "Z")


# ─── Runner principal ──────────────────────────────────────────────
def run_sync():
    """
    ML es la fuente de verdad.
    Cada ciclo:
      1. Sincroniza todos los cambios de ML a WooCommerce (productos, stock, precios).
      2. Procesa ordenes de ML para descontar stock en Woo.
    """
    cfg = load_config()
    con = init_db(cfg.get("db_path", "sync.db"))
    woo = WooAPI(cfg)
    ml  = MercadoLibreAPI(cfg)

    log.info("=" * 60)
    log.info("INICIO DE CICLO  (ML -> WOO)")
    log.info("=" * 60)

    # 1. ML -> WooCommerce: productos, stock y precios
    try:
        from import_from_ml import run_import
        log.info("Sincronizando productos ML -> Woo...")
        run_import(dry_run=False, single_item=None, reset_map=False, debug=False)
    except Exception as e:
        log.error(f"Error en sync ML->WOO productos: {e}")

    # 2. Ordenes ML -> descontar stock en WooCommerce
    try:
        ml_orders_to_woo(woo, ml, con, cfg)
    except Exception as e:
        log.error(f"Error en ML->WOO ordenes: {e}")

    log.info("FIN DE CICLO\n")


if __name__ == "__main__":
    run_sync()
