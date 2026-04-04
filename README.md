<<<<<<< HEAD
# WooCommerce ↔ Mercado Libre Sync
### WooCommerce es la fuente de verdad

---

## Flujo de datos

```
[WooCommerce]  ──── stock/precio/productos ────►  [Mercado Libre]
[Mercado Libre] ──── órdenes pagadas ──────────►  [WooCommerce] (descuenta stock)
```

---

## Setup en Proxmox (LXC Ubuntu/Debian)

### 1. Crear contenedor LXC

En Proxmox, crear un LXC con Ubuntu 22.04:
- RAM: 256MB es suficiente
- Disco: 4GB
- Red: acceso a internet + red local

### 2. Instalar dependencias

```bash
apt update && apt install -y python3 python3-pip python3-venv git

cd /opt
mkdir woo-ml-sync && cd woo-ml-sync

python3 -m venv venv
source venv/bin/activate
pip install requests flask
```

### 3. Copiar archivos

```bash
# Copiar todos los .py y archivos al directorio /opt/woo-ml-sync/
```

### 4. Configurar

```bash
cp config.example.json config.json
nano config.json   # Completar credenciales
```

#### Obtener credenciales WooCommerce:
1. En WordPress: **WooCommerce → Ajustes → Avanzado → REST API**
2. Crear clave con permisos **Lectura/Escritura**
3. Copiar Consumer Key y Consumer Secret

#### Obtener credenciales Mercado Libre:
1. Ir a https://developers.mercadolibre.com.ar
2. Crear una app → obtener `App ID` y `Secret Key`
3. Autorizarla con OAuth:
   ```
   https://auth.mercadolibre.com.ar/authorization?response_type=code&client_id=TU_APP_ID&redirect_uri=https://localhost
   ```
4. Intercambiar el `code` por tokens:
   ```bash
   curl -X POST https://api.mercadolibre.com/oauth/token \
     -d 'grant_type=authorization_code' \
     -d 'client_id=TU_APP_ID' \
     -d 'client_secret=TU_SECRET' \
     -d 'code=EL_CODE_RECIBIDO' \
     -d 'redirect_uri=https://localhost'
   ```
5. Guardar `access_token` y `refresh_token` en config.json
6. Tu `seller_id` aparece en la respuesta como `user_id`

### 5. Mapear productos existentes

```bash
source /opt/woo-ml-sync/venv/bin/activate
cd /opt/woo-ml-sync

# Mapeo automático por SKU (recomendado):
python map_products.py

# Ver qué quedó mapeado:
python map_products.py --show

# Mapeo manual si no tenés SKUs:
python map_products.py --manual
```

### 6. Probar la sincronización

```bash
python sync_engine.py
# Revisar sync.log para ver qué pasó
```

### 7. Instalar como servicio systemd

```bash
# Crear usuario dedicado
useradd -r -s /bin/false syncuser
chown -R syncuser:syncuser /opt/woo-ml-sync

# Instalar servicio
cp woo-ml-sync.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable woo-ml-sync
systemctl start woo-ml-sync

# Ver estado
systemctl status woo-ml-sync
journalctl -u woo-ml-sync -f   # logs en tiempo real
```

### 8. Iniciar dashboard

```bash
# Manualmente:
python dashboard.py

# O agregar otro servicio systemd para el dashboard:
# Acceder en: http://IP-DEL-LXC:8080
```

---

## Configuración config.json explicada

| Campo | Descripción |
|-------|-------------|
| `woo_url` | URL de tu tienda (sin trailing slash) |
| `woo_key` | Consumer Key de WooCommerce REST API |
| `woo_secret` | Consumer Secret |
| `ml_app_id` | ID de tu app en ML Developers |
| `ml_secret` | Secret Key de tu app ML |
| `ml_access_token` | Token OAuth (se renueva automático) |
| `ml_refresh_token` | Refresh token OAuth |
| `ml_seller_id` | Tu User ID en ML |
| `ml_currency` | ARS para Argentina |
| `ml_default_category` | Categoría ML para productos nuevos |
| `ml_listing_type` | `gold_special`, `gold_pro`, `bronze`, etc. |
| `auto_publish_new` | `true`: publica en ML productos nuevos de Woo |
| `sync_interval_minutes` | Cada cuántos minutos sincronizar |

---

## Archivos del proyecto

```
woo-ml-sync/
├── sync_engine.py          # Motor principal de sync
├── map_products.py         # Mapeo inicial Woo ↔ ML
├── scheduler.py            # Loop continuo
├── dashboard.py            # Web UI de monitoreo
├── config.json             # Credenciales (no commitear!)
├── config.example.json     # Template
├── sync.db                 # Base de datos SQLite (se crea sola)
├── sync.log                # Log de operaciones
└── woo-ml-sync.service     # Servicio systemd
```

---

## Preguntas frecuentes

**¿Qué pasa si el token de ML vence?**
Se renueva automáticamente usando el refresh_token y se guarda en config.json.

**¿Cómo evito que un cambio en ML vuelva a impactar en Woo en loop?**
La dirección ML→Woo solo procesa **órdenes/ventas**, nunca cambios de stock o precio desde ML. El script registra en SQLite qué órdenes ya procesó.

**¿Puedo agregar más de un vendedor o tienda?**
Sí, duplicar el directorio con su propio config.json y correr otro scheduler como servicio separado.

**¿Qué pasa con las variantes de productos?**
La versión actual maneja productos simples. Para variantes (talle/color), hay que extender `sync_engine.py` para iterar `p['variations']`.
=======
# ml_a_woo
Repositori con scripts para migrar de Mercado libre a woocommerce y mantenerlo actualizado
>>>>>>> 0482d5038f570aadb69e7fe6033ee4b31a14a78d
