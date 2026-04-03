"""
map_products.py — Mapeo inicial de productos Woo ↔ ML por SKU.
Correr UNA VEZ antes de comenzar la sincronización.

Uso:
  python map_products.py               # Mapeo automático por SKU
  python map_products.py --list        # Lista productos sin mapear
  python map_products.py --manual      # Mapeo manual interactivo
"""

import json
import sqlite3
import argparse
from sync_engine import WooAPI, MercadoLibreAPI, init_db, load_config


def auto_map_by_sku(woo: WooAPI, ml: MercadoLibreAPI, con: sqlite3.Connection):
    """Intenta mapear automáticamente por SKU coincidente."""
    print("Obteniendo productos de WooCommerce...")
    woo_products = woo.get_products()
    print(f"  {len(woo_products)} productos en Woo.")

    print("Obteniendo items de Mercado Libre...")
    ml_item_ids = ml.get_my_items()
    print(f"  {len(ml_item_ids)} items en ML. Descargando detalles...")

    ml_by_sku = {}
    for ml_id in ml_item_ids:
        try:
            item = ml.get_item(ml_id)
            sku = None
            for attr in item.get("attributes", []):
                if attr.get("id") == "SELLER_SKU":
                    sku = attr.get("value_name")
                    break
            if sku:
                ml_by_sku[sku] = ml_id
        except Exception as e:
            print(f"  Error obteniendo {ml_id}: {e}")

    print(f"\n  {len(ml_by_sku)} items ML con SKU definido.")
    print("\nMapeando por SKU...\n")

    mapped, unmapped = 0, 0
    for p in woo_products:
        sku = p.get("sku", "").strip()
        if not sku:
            print(f"  ⚠ Sin SKU: [{p['id']}] {p['name']}")
            unmapped += 1
            continue

        ml_id = ml_by_sku.get(sku)
        if ml_id:
            con.execute(
                "INSERT OR REPLACE INTO product_map(woo_id, ml_id, sku, last_synced) VALUES(?,?,?,datetime('now'))",
                (p["id"], ml_id, sku)
            )
            con.commit()
            print(f"  ✓ {p['name']} — SKU {sku} → {ml_id}")
            mapped += 1
        else:
            print(f"  ✗ Sin match ML: [{p['id']}] {p['name']} (SKU: {sku})")
            unmapped += 1

    print(f"\nResultado: {mapped} mapeados, {unmapped} sin mapear.")


def list_unmapped(woo: WooAPI, con: sqlite3.Connection):
    """Lista productos Woo que no tienen mapeo a ML."""
    mapped_ids = {r[0] for r in con.execute("SELECT woo_id FROM product_map").fetchall()}
    products = woo.get_products()
    print(f"\nProductos sin mapear ({len([p for p in products if p['id'] not in mapped_ids])}):\n")
    for p in products:
        if p["id"] not in mapped_ids:
            print(f"  [{p['id']}] {p['name']}  SKU={p.get('sku','—')}  Stock={p.get('stock_quantity','?')}")


def manual_map(woo: WooAPI, ml: MercadoLibreAPI, con: sqlite3.Connection):
    """Permite mapear manualmente ingresando woo_id y ml_id."""
    print("Mapeo manual. Ingresá 'q' para salir.\n")
    while True:
        woo_id = input("WooCommerce Product ID: ").strip()
        if woo_id.lower() == "q":
            break
        ml_id = input("Mercado Libre Item ID (ej: MLA1234567890): ").strip()
        if ml_id.lower() == "q":
            break
        sku = input("SKU (opcional): ").strip()
        try:
            con.execute(
                "INSERT OR REPLACE INTO product_map(woo_id, ml_id, sku, last_synced) VALUES(?,?,?,datetime('now'))",
                (int(woo_id), ml_id, sku or None)
            )
            con.commit()
            print(f"  ✓ Mapeado: Woo {woo_id} ↔ ML {ml_id}\n")
        except Exception as e:
            print(f"  ✗ Error: {e}\n")


def show_map(con: sqlite3.Connection):
    """Muestra el mapeo actual."""
    rows = con.execute("SELECT woo_id, ml_id, sku, last_synced FROM product_map ORDER BY woo_id").fetchall()
    print(f"\n{'WOO_ID':<10} {'ML_ID':<20} {'SKU':<20} {'LAST_SYNCED'}")
    print("-" * 70)
    for r in rows:
        print(f"{r[0]:<10} {r[1]:<20} {(r[2] or '—'):<20} {r[3] or '—'}")
    print(f"\nTotal: {len(rows)} mappings.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mapeo de productos Woo ↔ ML")
    parser.add_argument("--list",   action="store_true", help="Listar sin mapear")
    parser.add_argument("--manual", action="store_true", help="Mapeo manual")
    parser.add_argument("--show",   action="store_true", help="Mostrar mapeo actual")
    args = parser.parse_args()

    cfg = load_config()
    con = init_db(cfg.get("db_path", "sync.db"))
    woo = WooAPI(cfg)
    ml  = MercadoLibreAPI(cfg)

    if args.list:
        list_unmapped(woo, con)
    elif args.manual:
        manual_map(woo, ml, con)
    elif args.show:
        show_map(con)
    else:
        auto_map_by_sku(woo, ml, con)
