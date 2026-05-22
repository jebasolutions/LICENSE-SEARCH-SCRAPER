"""
Jeba Solutions — Scheduler
Corre el scraper automáticamente cada lunes a las 6 AM
"""

import schedule
import time
import logging
from scraper import main

log = logging.getLogger(__name__)

def job():
    log.info("⏰ Scheduler: iniciando scraper programado...")
    try:
        main()
    except Exception as e:
        log.error(f"❌ Error en job programado: {e}")

# Corre cada lunes a las 6:00 AM
schedule.every().monday.at("06:00").do(job)

log.info("✅ Scheduler activo — esperando el próximo lunes a las 6 AM")
log.info("💡 Para correr ahora manualmente ejecuta: python scraper.py")

# Loop infinito — mantiene el scheduler corriendo
while True:
    schedule.run_pending()
    time.sleep(60)  # Revisa cada minuto
