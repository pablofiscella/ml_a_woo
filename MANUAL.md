# Manual de Operaciones — Sync ML → WooCommerce
**CasaTridimensional**

---

## Estructura del sistema

```
/opt/woo-ml-sync/
├── import_from_ml.py     → Importa/sincroniza productos ML → Woo
├── sync_engine.py        → Motor base (APIs, DB)
├── scheduler.py          → Loop automático (lo corre systemd)
├── dashboard.py          → Panel web en http://IP-LXC:8080
├── map_products.py       → Mapeo manual por SKU (uso puntual)
├── config.json           → Credenciales (NO compartir)
├── sync.db               → Base de datos SQLite (mapeos y logs)
└── sync.log              → Log de operaciones
```

**Flujo de datos:**
```
Mercado Libre  ──── productos/stock/precios ────►  WooCommerce
Mercado Libre  ──── ventas/órdenes ────────────►  WooCommerce (descuenta stock)
```
ML es la fuente de verdad. Todo cambio en ML se refleja en Woo automáticamente.

---

## 1. MIGRACIÓN DESDE CERO

Usar cuando: borraste todo de WooCommerce y querés importar todo desde ML.

```bash
cd /opt/woo-ml-sync

# Paso 1: Limpiar la base de datos local
sqlite3 sync.db "DELETE FROM product_map; DELETE FROM sync_log;"

# Paso 2: Importar todo desde ML (puede tardar varios minutos)
PYTHONPATH=/opt/woo-ml-sync python3 import_from_ml.py

# Paso 3: Ver resumen
sqlite3 sync.db "SELECT status, COUNT(*) FROM sync_log GROUP BY status;"
```

**Si hay errores**, ver sección 5.

---

## 2. SINCRONIZACIÓN AUTOMÁTICA (CRON/SYSTEMD)

El sistema corre automáticamente como servicio systemd. Cada X minutos
(definido en config.json → `sync_interval_minutes`) revisa ML y actualiza Woo.

### Instalar el servicio (solo la primera vez)

```bash
# Copiar el archivo de servicio
cp /opt/woo-ml-sync/woo-ml-sync.service /etc/systemd/system/

# Corregir la ruta de Python y agregar PYTHONPATH
sed -i "s|ExecStart=.*|ExecStart=/usr/bin/python3 scheduler.py|" /etc/systemd/system/woo-ml-sync.service
sed -i '/^ExecStart=/a Environment=PYTHONPATH=/opt/woo-ml-sync' /etc/systemd/system/woo-ml-sync.service

# Activar e iniciar
systemctl daemon-reload
systemctl enable woo-ml-sync
systemctl start woo-ml-sync

# Verificar que está corriendo (debe decir "active (running)")
systemctl status woo-ml-sync
```

> **Importante:** El service original usa un virtualenv que no existe. Los dos comandos `sed` de arriba
> corrigen la ruta automáticamente para usar el Python del sistema (`/usr/bin/python3`).

### Comandos del servicio

```bash
# Ver si está activo
systemctl status woo-ml-sync

# Ver logs en tiempo real
journalctl -u woo-ml-sync -f

# Reiniciar (después de cambiar config.json o los scripts)
systemctl restart woo-ml-sync

# Detener
systemctl stop woo-ml-sync

# Iniciar
systemctl start woo-ml-sync
```

### Cambiar cada cuánto sincroniza

Editá `config.json` y cambiá el valor de `sync_interval_minutes`:
```json
"sync_interval_minutes": 15
```
Después: `systemctl restart woo-ml-sync`

---

## 3. SINCRONIZACIÓN MANUAL

Usar cuando: querés forzar una sync sin esperar el ciclo automático,
o cuando el scheduler falló y querés actualizar ahora.

```bash
cd /opt/woo-ml-sync

# Sincronizar todo (solo actualiza los que ya están mapeados + crea nuevos)
PYTHONPATH=/opt/woo-ml-sync python3 import_from_ml.py

# Sincronizar un solo producto (útil para probar)
PYTHONPATH=/opt/woo-ml-sync python3 import_from_ml.py --item MLA1234567890

# Ver qué haría sin tocar nada (simulación)
PYTHONPATH=/opt/woo-ml-sync python3 import_from_ml.py --dry-run
```

---

## 4. AGREGAR UN PRODUCTO NUEVO EN ML

No hay que hacer nada. En el próximo ciclo automático el scheduler
lo detecta y lo crea en WooCommerce.

Si querés que aparezca ya:
```bash
PYTHONPATH=/opt/woo-ml-sync python3 import_from_ml.py --item MLA_ID_DEL_NUEVO
```

---

## 5. DIAGNÓSTICO DE ERRORES

### Ver errores recientes
```bash
sqlite3 /opt/woo-ml-sync/sync.db \
  "SELECT ref_id, detail FROM sync_log WHERE status='error' ORDER BY id DESC LIMIT 20;"
```

### Ver log completo
```bash
tail -100 /opt/woo-ml-sync/sync.log
# o en tiempo real:
tail -f /opt/woo-ml-sync/sync.log
```

### Reintentar solo los que fallaron
```bash
# Ver cuáles fallaron
sqlite3 /opt/woo-ml-sync/sync.db \
  "SELECT ref_id FROM sync_log WHERE status='error' GROUP BY ref_id;" > /tmp/errores.txt

# Reimportar cada uno
while read ml_id; do
  PYTHONPATH=/opt/woo-ml-sync python3 /opt/woo-ml-sync/import_from_ml.py --item "$ml_id"
done < /tmp/errores.txt
```

### Error: "SKU already present in lookup table"
Significa que hay productos en WooCommerce con ese SKU aunque no los veas.
Solución desde WordPress admin → Code Snippets → ejecutar:
```php
global $wpdb;
$wpdb->query("TRUNCATE TABLE {$wpdb->prefix}wc_product_attributes_lookup");
$wpdb->query("TRUNCATE TABLE {$wpdb->prefix}wc_product_meta_lookup");
$ids = $wpdb->get_col("SELECT ID FROM {$wpdb->posts} WHERE post_type='product'");
foreach ($ids as $id) { wp_delete_post($id, true); }
echo count($ids) . " productos eliminados";
```
Después correr migración desde cero (sección 1).

### Error: Token de ML expirado
El sistema renueva el token automáticamente. Si falla:
```bash
# Ver el error
tail -20 /opt/woo-ml-sync/sync.log

# El token se guarda en config.json automáticamente al renovarse
cat /opt/woo-ml-sync/config.json | grep ml_access_token
```

---

## 6. PANEL DE MONITOREO (DASHBOARD)

Accedé desde tu red local a: `http://IP-DEL-LXC:8080`

Para iniciarlo (si no está corriendo):
```bash
cd /opt/woo-ml-sync
PYTHONPATH=/opt/woo-ml-sync python3 dashboard.py &
```

Para que corra siempre, creá un segundo servicio:
```bash
cat > /etc/systemd/system/woo-ml-dashboard.service << 'EOF'
[Unit]
Description=Woo ML Sync Dashboard
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/woo-ml-sync
ExecStart=/usr/bin/python3 dashboard.py
Environment=PYTHONPATH=/opt/woo-ml-sync
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable woo-ml-dashboard
systemctl start woo-ml-dashboard
```

---

## 7. VER MAPEO ACTUAL (qué productos están vinculados)

```bash
# Resumen
sqlite3 /opt/woo-ml-sync/sync.db \
  "SELECT COUNT(*) as total, COUNT(CASE WHEN sku IS NOT NULL THEN 1 END) as con_sku FROM product_map;"

# Lista completa
sqlite3 /opt/woo-ml-sync/sync.db \
  "SELECT woo_id, ml_id, sku, last_synced FROM product_map ORDER BY last_synced DESC LIMIT 20;"
```

---

## 8. BACKUP

```bash
# Hacer backup de la DB y config (guardar en lugar seguro)
cp /opt/woo-ml-sync/sync.db ~/backup-sync-$(date +%Y%m%d).db
cp /opt/woo-ml-sync/config.json ~/backup-config-$(date +%Y%m%d).json
```

---

## 9. REFERENCIA RÁPIDA

| Situación | Comando |
|-----------|---------|
| Migrar todo desde cero | `sqlite3 sync.db "DELETE FROM product_map; DELETE FROM sync_log;"` + `python3 import_from_ml.py` |
| Forzar sync ahora | `python3 import_from_ml.py` |
| Sync un producto | `python3 import_from_ml.py --item MLA123` |
| Ver errores | `sqlite3 sync.db "SELECT ref_id,detail FROM sync_log WHERE status='error' ORDER BY id DESC LIMIT 10;"` |
| Ver logs | `tail -f sync.log` |
| Estado del servicio | `systemctl status woo-ml-sync` |
| Reiniciar servicio | `systemctl restart woo-ml-sync` |
| Ver logs del servicio | `journalctl -u woo-ml-sync -f` |

> **Nota:** Todos los comandos corren desde `/opt/woo-ml-sync/` con `PYTHONPATH=/opt/woo-ml-sync`
