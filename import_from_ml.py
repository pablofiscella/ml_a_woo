"""
import_from_ml.py — Importa y sincroniza ML → WooCommerce.
Mercado Libre es la fuente de verdad para TODO.

Uso:
  python3 import_from_ml.py              # Importa/actualiza todo desde ML
  python3 import_from_ml.py --dry-run    # Muestra qué haría sin tocar nada
  python3 import_from_ml.py --item MLA123456789  # Un item específico
  python3 import_from_ml.py --reset-map  # Limpia el mapeo y reimporta todo
  python3 import_from_ml.py --debug      # Imprime el payload completo en errores 400
"""

import argparse
import json
import re
import sqlite3
import sys
import time
import types
from datetime import datetime
from html.parser import HTMLParser

from sync_engine import WooAPI, MercadoLibreAPI, init_db, load_config, log_event


# ──────────────────────────────────────────────────────────────────────────────
# HTML helpers
# ──────────────────────────────────────────────────────────────────────────────

class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.result = []
    def handle_data(self, d):
        self.result.append(d)
    def get_text(self):
        return " ".join(self.result)

def strip_html(html: str) -> str:
    s = _HTMLStripper()
    s.feed(html or "")
    return s.get_text().strip()


# ──────────────────────────────────────────────────────────────────────────────
# Patch WooAPI
# ──────────────────────────────────────────────────────────────────────────────

def _patch_woo_api(woo: WooAPI):
    def create_product(self, data):
        return self._post("products", data)
    def update_product(self, woo_id, data):
        return self._put(f"products/{woo_id}", data)
    def create_category(self, name, parent_id=0):
        return self._post("products/categories", {"name": name, "parent": parent_id})
    def get_categories(self):
        cats, page = [], 1
        while True:
            batch = self._get("products/categories", {"per_page": 100, "page": page})
            if not batch:
                break
            cats.extend(batch)
            page += 1
        return cats
    def get_by_sku(self, sku):
        results = self._get("products", {"sku": sku, "per_page": 1})
        return results[0] if results else None

    woo.create_product  = types.MethodType(create_product,  woo)
    woo.update_product  = types.MethodType(update_product,  woo)
    woo.create_category = types.MethodType(create_category, woo)
    woo.get_categories  = types.MethodType(get_categories,  woo)
    woo.get_by_sku      = types.MethodType(get_by_sku,      woo)


# ──────────────────────────────────────────────────────────────────────────────
# Caché de categorías
# ──────────────────────────────────────────────────────────────────────────────

_woo_cat_cache: dict = {}
_ml_cat_cache:  dict = {}

def _ensure_cat_cache(woo: WooAPI):
    global _woo_cat_cache
    if not _woo_cat_cache:
        cats = woo.get_categories()
        _woo_cat_cache = {c["name"].lower(): c["id"] for c in cats}

def get_or_create_woo_category(woo: WooAPI, name: str) -> int:
    _ensure_cat_cache(woo)
    key = name.lower()
    if key in _woo_cat_cache:
        return _woo_cat_cache[key]
    result = woo.create_category(name)
    _woo_cat_cache[key] = result["id"]
    return result["id"]

def ml_category_name(ml: MercadoLibreAPI, category_id: str) -> str:
    if category_id in _ml_cat_cache:
        return _ml_cat_cache[category_id]
    try:
        data = ml._get(f"/categories/{category_id}")
        path = data.get("path_from_root", [])
        name = " > ".join(p["name"] for p in path) if path else data.get("name", category_id)
        _ml_cat_cache[category_id] = name
        return name
    except Exception:
        return category_id


def _classify_title(title: str) -> str:
    """Determina la categoria propia del negocio segun el titulo del producto."""
    t = title.lower()
    has_lampara  = "lampara" in t or "velador" in t
    has_escudo   = "escudo" in t
    has_nombre   = "nombre" in t
    has_figura   = "figura" in t
    has_accion   = "accion" in t or "acción" in t
    has_vaso     = "vaso" in t
    has_cuadro   = "cuadro" in t
    has_mate     = "mate" in t
    has_soporte  = "soporte" in t
    has_auricular = "auricular" in t or "auriculares" in t

    if "llavero" in t or "llaveros" in t:
        return "Llaveros"
    has_personaliz = "personaliz" in t
    if has_lampara and has_escudo:
        return "Lamparas de Futbol"
    if (has_nombre or has_personaliz) and has_lampara:
        return "Lámparas Personalizables"
    if has_escudo:
        return "Escudos"
    if has_lampara:
        return "Lamparas"
    if has_figura and has_accion:
        return "Figuras de Accion"
    if has_figura:
        return "Figuras"
    if has_vaso:
        return "Vasos"
    if has_cuadro:
        return "Cuadros"
    if has_mate:
        return "Mates"
    if has_soporte and has_auricular:
        return "Soporte Auriculares"
    return 


# ──────────────────────────────────────────────────────────────────────────────
# Sanitización — acá estaba la mayoría de los errores 400
# ──────────────────────────────────────────────────────────────────────────────

def _safe_price(val) -> str:
    try:
        return f"{float(str(val).replace(',', '.')):.2f}"
    except Exception:
        return "0.00"

def _safe_stock(val) -> int:
    try:
        return max(0, int(val))
    except Exception:
        return 0

def _validate_image_url(url: str) -> bool:
    if not url or not url.startswith("http"):
        return False
    if "localhost" in url or "127.0.0" in url:
        return False
    return True

def _fix_image_url(url: str) -> str:
    """Fuerza HTTPS y maxima resolucion."""
    url = url.replace("http://", "https://")
    url = re.sub(r"-[A-Z]\.", "-F.", url)
    return url

def _clean_text(val) -> str:
    if not val:
        return ""
    return re.sub(r"\s+", " ", str(val).replace("\x00", "")).strip()

def _safe_attributes(raw_attrs: list) -> list:
    """
    Filtra atributos que causan 400 y saca los internos/logisticos.
    Solo quedan atributos utiles para el comprador.
    """
    skip_ids = {
        # Tecnicos/internos de ML
        "SELLER_SKU", "GTIN", "EAN", "UPC",
        "WARRANTY_TYPE", "WARRANTY_TIME",
        "WEIGHT", "LENGTH", "WIDTH", "HEIGHT",
        "ITEM_CONDITION",
        # Logistica del vendedor (no interesan al comprador)
        "SELLER_PACKAGE_HEIGHT", "SELLER_PACKAGE_WIDTH",
        "SELLER_PACKAGE_LENGTH", "SELLER_PACKAGE_WEIGHT",
        "GIFTABLE", "PACKAGE_TYPE",
        # Impuestos y datos fiscales
        "INTERNAL_TAX", "TAX", "INTERNAL_TAX_RATE",
        "VAT_RATE", "IVA",
        # Otros internos
        "GTIN_EMPTY_REASON", "ALPHANUMERIC_MODEL",
    }
    # Nombres de atributos a ignorar (por nombre, no por ID)
    skip_names = {
        "regalable", "tipo de paquete del seller",
        "motivo de gtin vacío", "motivo de gtin vacio",
        "impuesto interno", "iva",
        "altura del paquete del seller", "largo del paquete del seller",
        "peso del paquete del seller", "ancho del paquete del seller",
        "color filtrable",  # duplicado de color
    }
    seen_names = set()
    result = []
    for attr in raw_attrs:
        if attr.get("id") in skip_ids:
            continue
        name = _clean_text(attr.get("name", ""))
        val  = attr.get("value_name")
        if not val and attr.get("value_struct"):
            vs = attr["value_struct"]
            val = f"{vs.get('number','')} {vs.get('unit','')}".strip()
        val = _clean_text(val)
        if not name or not val:
            continue
        key = name.lower()
        if key in seen_names:
            continue
        if key in skip_names:
            continue
        seen_names.add(key)
        result.append({
            "name":      name,
            "options":   [val],
            "visible":   True,
            "variation": False,
        })
    return result

def _safe_sku(sku: str, ml_id: str) -> str:
    """Si no hay SKU usamos el ML ID para garantizar unicidad."""
    cleaned = re.sub(r"[^\w\-]", "", str(sku or "").strip())
    return cleaned if cleaned else f"ML-{ml_id}"

def _build_description(plain_text: str, item: dict) -> str:
    """Solo el texto de ML. Sin atributos internos/logisticos en la descripcion."""
    parts = []
    if plain_text and plain_text.strip():
        parts.append(plain_text.strip())

    warranty_time = next((a.get("value_name","") or "" for a in item.get("attributes",[]) if a.get("id")=="WARRANTY_TIME"), "")
    warranty_type = next((a.get("value_name","") or "" for a in item.get("attributes",[]) if a.get("id")=="WARRANTY_TYPE"), "")
    if warranty_time or warranty_type:
        parts.append(f"Garantia: {warranty_type} {warranty_time}".strip())

    return "\n\n".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# Construcción del payload
# ──────────────────────────────────────────────────────────────────────────────

def ml_item_to_woo(item: dict, description_text: str,
                   woo: WooAPI, ml: MercadoLibreAPI) -> dict:
    ml_id = item["id"]

    images = []
    for p in item.get("pictures", [])[:3]:
        url = _fix_image_url(p.get("url", ""))
        if _validate_image_url(url):
            images.append({"src": url})

    raw_sku = next(
        (a.get("value_name", "") or "" for a in item.get("attributes", []) if a.get("id") == "SELLER_SKU"),
        ""
    )
    sku = _safe_sku(raw_sku, ml_id)

    price          = item.get("price", 0)
    original_price = item.get("original_price") or price
    # Aplicar 18% de descuento sobre el precio de ML
    discounted     = float(original_price) * 0.82
    regular_price  = _safe_price(discounted)
    sale_price     = ""  # sin precio tachado""

    stock      = _safe_stock(item.get("available_quantity", 0))
    woo_status = "publish" if item.get("status") == "active" else "draft"
    attributes = _safe_attributes(item.get("attributes", []))

    categories = []
    ml_cat_id  = item.get("category_id", "")

    # Categoria propia del negocio (tiene prioridad)
    custom_cat = _classify_title(item.get("title", ""))
    if custom_cat:
        try:
            cat_id = get_or_create_woo_category(woo, custom_cat)
            categories.append({"id": cat_id})
        except Exception as e:
            print(f"    ⚠ Categoría propia ignorada: {e}")
    elif ml_cat_id:
        # Fallback: usar categoria de ML si no matchea ninguna propia
        try:
            cat_name = ml_category_name(ml, ml_cat_id)
            leaf     = cat_name.split(" > ")[-1]
            cat_id   = get_or_create_woo_category(woo, leaf)
            categories.append({"id": cat_id})
        except Exception as e:
            print(f"    ⚠ Categoría ML ignorada: {e}")

    # Dimensiones del PRODUCTO (no del paquete del seller)
    # ML usa: HEIGHT=alto, WIDTH=ancho, LENGTH/DEPTH=largo, WEIGHT=peso
    # Woo espera: length, width, height en cm y weight en kg
    dim_map = {
        "HEIGHT":            ("height", "cm"),
        "WIDTH":             ("width",  "cm"),
        "LENGTH":            ("length", "cm"),
        "DEPTH":             ("length", "cm"),
        "TOTAL_HEIGHT":      ("height", "cm"),
        "TOTAL_WIDTH":       ("width",  "cm"),
        "TOTAL_DEPTH":       ("length", "cm"),
        "WEIGHT":            ("weight", "kg"),
        "NET_WEIGHT":        ("weight", "kg"),
    }
    weight     = ""
    dimensions = {}
    for attr in item.get("attributes", []):
        mapping = dim_map.get(attr.get("id", ""))
        if not mapping:
            continue
        woo_field, expected_unit = mapping
        vs  = attr.get("value_struct") or {}
        num = vs.get("number")
        unit = (vs.get("unit") or expected_unit).lower()
        if num is None:
            continue
        try:
            num = float(num)
        except Exception:
            continue
        if num <= 0:
            continue
        # Convertir unidades si es necesario
        if woo_field == "weight":
            # Woo quiere kg
            if unit == "g":
                num = num / 1000
            val = f"{num:.3f}".rstrip("0").rstrip(".")
            weight = val
        else:
            # Woo quiere cm
            if unit == "m":
                num = num * 100
            elif unit == "mm":
                num = num / 10
            val = f"{num:.1f}".rstrip("0").rstrip(".")
            # Solo sobreescribir si no tenemos ya este campo (prioridad: campo específico > total)
            if woo_field not in dimensions:
                dimensions[woo_field] = val

    warranty_time = next((a.get("value_name","") or "" for a in item.get("attributes",[]) if a.get("id")=="WARRANTY_TIME"), "")
    warranty_type = next((a.get("value_name","") or "" for a in item.get("attributes",[]) if a.get("id")=="WARRANTY_TYPE"), "")

    payload = {
        "name":              _clean_text(item.get("title", "Sin título")),
        "type":              "simple",
        "status":            woo_status,
        "regular_price":     regular_price,
        "sale_price":        sale_price,
        "description":       _build_description(description_text, item),
        "short_description": _clean_text(item.get("title", "")),
        "sku":               sku,
        "manage_stock":      True,
        "stock_quantity":    stock,
        "stock_status":      "instock" if stock > 0 else "outofstock",
        "images":            images,
        "attributes":        attributes,
        "categories":        categories,
        "meta_data": [
            {"key": "_ml_item_id",      "value": ml_id},
            {"key": "_ml_listing_type", "value": item.get("listing_type_id", "")},
            {"key": "_ml_permalink",    "value": item.get("permalink", "")},
            {"key": "_ml_category_id",  "value": ml_cat_id},
            {"key": "_ml_condition",    "value": item.get("condition", "")},
            {"key": "_ml_warranty",     "value": f"{warranty_type} {warranty_time}".strip()},
            {"key": "_ml_sold_qty",     "value": str(item.get("sold_quantity", 0))},
        ],
    }

    if weight:
        payload["weight"] = weight
    if dimensions:
        payload["dimensions"] = dimensions

    return payload


# ──────────────────────────────────────────────────────────────────────────────
# Error detail helper
# ──────────────────────────────────────────────────────────────────────────────

def _woo_error_detail(exc: Exception) -> str:
    resp = getattr(exc, "response", None)
    if resp is None:
        return str(exc)
    try:
        body   = resp.json()
        msg    = body.get("message", "")
        data   = body.get("data", {})
        params = data.get("params", {}) if isinstance(data, dict) else {}
        detail = json.dumps(params, ensure_ascii=False) if params else ""
        return f"HTTP {resp.status_code} — {msg} {detail}".strip()
    except Exception:
        return f"HTTP {resp.status_code} — {resp.text[:400]}"


# ──────────────────────────────────────────────────────────────────────────────
# Importar / actualizar un item — ML siempre manda
# ──────────────────────────────────────────────────────────────────────────────

def import_item(ml_id: str, woo: WooAPI, ml: MercadoLibreAPI,
                con: sqlite3.Connection, dry_run: bool, debug: bool = False) -> str:
    try:
        item  = ml.get_item(ml_id)
        title = item.get("title", ml_id)

        try:
            desc_data   = ml._get(f"/items/{ml_id}/description")
            description = desc_data.get("plain_text", "") or desc_data.get("text", "")
        except Exception:
            description = ""

        payload = ml_item_to_woo(item, description, woo, ml)

        if dry_run:
            print(f"  [DRY] {title[:55]}")
            print(f"        SKU={payload['sku']}  precio={payload['regular_price']}  "
                  f"stock={payload['stock_quantity']}  fotos={len(payload['images'])}  "
                  f"attrs={len(payload['attributes'])}")
            return "created"

        # ¿Ya mapeado? → actualizar (sin resubir fotos)
        row = con.execute("SELECT woo_id FROM product_map WHERE ml_id=?", (ml_id,)).fetchone()
        if row:
            woo_id = row[0]
            update_payload = {k: v for k, v in payload.items() if k not in ("sku", "images")}
            woo.update_product(woo_id, update_payload)
            con.execute("UPDATE product_map SET last_synced=? WHERE woo_id=?",
                        (datetime.now().isoformat(), woo_id))
            con.commit()
            log_event(con, "ML->WOO", "product_update", ml_id, "ok", f"woo_id={woo_id}")
            print(f"  ✏ Actualizado Woo {woo_id}: {title[:55]}")
            return "updated"

        # ¿Existe por SKU? → recuperar mapeo y actualizar
        existing = woo.get_by_sku(payload["sku"])
        if existing:
            woo_id = existing["id"]
            con.execute(
                "INSERT OR REPLACE INTO product_map(woo_id, ml_id, sku, last_synced) VALUES(?,?,?,?)",
                (woo_id, ml_id, payload["sku"], datetime.now().isoformat())
            )
            con.commit()
            update_payload = {k: v for k, v in payload.items() if k not in ("sku", "images")}
            woo.update_product(woo_id, update_payload)
            log_event(con, "ML->WOO", "product_import", ml_id, "ok", f"recuperado por SKU woo_id={woo_id}")
            print(f"  ↩ Recuperado por SKU → Woo {woo_id}: {title[:55]}")
            return "updated"

        # Crear nuevo
        if debug:
            print(f"  [DEBUG] Payload:\n{json.dumps(payload, indent=2, ensure_ascii=False)[:1500]}")

        result = woo.create_product(payload)
        woo_id = result["id"]
        con.execute(
            "INSERT OR REPLACE INTO product_map(woo_id, ml_id, sku, last_synced) VALUES(?,?,?,?)",
            (woo_id, ml_id, payload["sku"], datetime.now().isoformat())
        )
        con.commit()
        log_event(con, "ML->WOO", "product_import", ml_id, "ok",
                  f"woo_id={woo_id} sku={payload['sku']} imgs={len(payload['images'])}")
        print(f"  ✓ Creado Woo {woo_id}: {title[:55]}  [{len(payload['images'])} fotos]")
        return "created"

    except Exception as e:
        detail = _woo_error_detail(e)
        log_event(con, "ML->WOO", "product_import", ml_id, "error", detail)
        print(f"  ✗ Error en {ml_id}: {detail}")
        if debug:
            try:
                p = ml_item_to_woo(ml.get_item(ml_id), "", woo, ml)
                print(f"  [DEBUG] Payload:\n{json.dumps(p, indent=2, ensure_ascii=False)[:2000]}")
            except Exception:
                pass
        return "error"


# ──────────────────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────────────────

def run_import(dry_run=False, single_item=None, reset_map=False, debug=False):
    cfg = load_config()
    con = init_db(cfg.get("db_path", "sync.db"))
    woo = WooAPI(cfg)
    ml  = MercadoLibreAPI(cfg)
    _patch_woo_api(woo)

    if reset_map:
        confirm = input("⚠ Esto borrará el mapeo existente. ¿Seguro? (s/n): ").strip().lower()
        if confirm == "s":
            con.execute("DELETE FROM product_map")
            con.commit()
            print("  Mapeo limpiado.\n")
        else:
            print("  Cancelado.")
            sys.exit(0)

    if dry_run:
        print("=" * 60)
        print("MODO DRY-RUN — No se modificará nada en WooCommerce")
        print("=" * 60 + "\n")

    if single_item:
        ml_ids = [single_item]
    else:
        print("Obteniendo todos los items de Mercado Libre...")
        ml_ids = ml.get_my_items()
        print(f"  {len(ml_ids)} items encontrados.\n")

    counts = {"created": 0, "updated": 0, "skipped": 0, "error": 0}
    errors = []

    for i, ml_id in enumerate(ml_ids, 1):
        print(f"[{i}/{len(ml_ids)}] {ml_id}")
        result = import_item(ml_id, woo, ml, con, dry_run=dry_run, debug=debug)
        counts[result] += 1
        if result == "error":
            errors.append(ml_id)
        if not dry_run:
            time.sleep(0.4)

    print("\n" + "=" * 60)
    print("RESUMEN")
    print("=" * 60)
    print(f"  ✓ Creados:      {counts['created']}")
    print(f"  ✏ Actualizados: {counts['updated']}")
    print(f"  ⏭ Omitidos:    {counts['skipped']}")
    print(f"  ✗ Errores:      {counts['error']}")
    if errors:
        print(f"\n  Items con error:")
        for eid in errors:
            print(f"    - {eid}")
        print(f"\n  Para diagnosticar un error específico:")
        print(f"    python3 import_from_ml.py --item {errors[0]} --debug")
    if dry_run:
        print("\n  (Nada fue modificado — modo dry-run)")


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Importa/sincroniza productos ML → WooCommerce. ML es la fuente de verdad."
    )
    parser.add_argument("--dry-run",   action="store_true", help="Simula sin crear nada")
    parser.add_argument("--item",      metavar="ML_ID",     help="Procesar solo este item")
    parser.add_argument("--reset-map", action="store_true", help="Limpia el mapeo antes de importar")
    parser.add_argument("--debug",     action="store_true", help="Imprime payload completo en errores 400")
    args = parser.parse_args()

    run_import(
        dry_run=args.dry_run,
        single_item=args.item,
        reset_map=args.reset_map,
        debug=args.debug,
    )
