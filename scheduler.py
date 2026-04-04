"""
scheduler.py — Corre sync_engine en loop según el intervalo en config.json
Ideal para correr como servicio systemd en el LXC de Proxmox.
"""

import time
import logging
from sync_engine import run_sync, load_config

log = logging.getLogger(__name__)

if __name__ == "__main__":
    print("Scheduler iniciado. Ctrl+C para detener.")
    while True:
        cfg = load_config()
        interval = cfg.get("sync_interval_minutes", 15) * 60
        try:
            run_sync()
        except Exception as e:
            log.error(f"Error en ciclo: {e}")
        print(f"Próxima sincronización en {interval // 60} minutos...")
        time.sleep(interval)
