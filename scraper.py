"""
=============================================================
  Florida Life & Annuity License Scraper → GoHighLevel
=============================================================
¿Qué hace este script?
  1. Cada hora descarga el CSV de licencias válidas de Florida (directo, sin browser)
  2. Filtra solo licencias Life & Annuity
  3. Separa nombres: "APELLIDO, NOMBRE" → first_name / last_name
  4. Manda cada contacto a GHL con tag 'RECRUIT AUTOMATICO' y source 'LICENSE SEARCH'
 
Variables de entorno requeridas (en Railway):
  GHL_API_KEY       → tu API key de GoHighLevel
  GHL_LOCATION_ID   → el ID de tu location en GHL
=============================================================
"""
 
import os
import time
import logging
import sqlite3
import requests
import pandas as pd
import schedule
from io import StringIO
from datetime import datetime
 
# ── Logs ──────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)
 
# ── Credenciales GHL ──────────────────────────────────────────────────────────
GHL_API_KEY     = os.environ.get("GHL_API_KEY", "")
GHL_LOCATION_ID = os.environ.get("GHL_LOCATION_ID", "")
 
# ── URL directa del CSV de Florida (sin necesidad de browser) ─────────────────
CSV_URL = "https://www.myfloridacfo.com/downloads/AAS/LicenseeSearch/AllValidLicensesIndividual.csv"
 
# ── Configuración ─────────────────────────────────────────────────────────────
TAGS_GHL    = ["RECRUIT AUTOMATICO"]
SOURCE_GHL  = "LICENSE SEARCH"
DB_PATH     = "/app/procesados.db"
 
# Palabras clave para filtrar Life & Annuity
FILTRO_LIFE = ["life", "annuity", "2-14", "2-15", "2-16"]
 
 
# =============================================================================
#  BASE DE DATOS — Guarda los agentes ya enviados para no repetirlos
# =============================================================================
def iniciar_db():
    """Crea la base de datos si no existe."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS enviados (
            licencia TEXT PRIMARY KEY,
            fecha    TEXT
        )
    """)
    conn.commit()
    conn.close()
    total = contar_enviados()
    log.info(f"📂 Base de datos lista — {total:,} agentes ya procesados anteriormente")
 
def ya_fue_enviado(licencia: str) -> bool:
    """Devuelve True si este número de licencia ya fue enviado a GHL."""
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.execute("SELECT 1 FROM enviados WHERE licencia = ?", (licencia,))
    existe = cur.fetchone() is not None
    conn.close()
    return existe
 
def marcar_enviado(licencia: str):
    """Guarda el número de licencia en la base de datos."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR IGNORE INTO enviados (licencia, fecha) VALUES (?, ?)",
        (licencia, datetime.now().strftime("%Y-%m-%d"))
    )
    conn.commit()
    conn.close()
 
def contar_enviados() -> int:
    conn  = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM enviados").fetchone()[0]
    conn.close()
    return total
 
 
# =============================================================================
#  FUNCIÓN 1 — Separar nombre
#  El CSV trae: "GARCIA LOPEZ, ANA MARIA"
#  → first_name = "Ana Maria"  |  last_name = "Garcia Lopez"
# =============================================================================
def separar_nombre(nombre_completo: str):
    nombre_completo = str(nombre_completo).strip()
    if "," in nombre_completo:
        partes     = nombre_completo.split(",", 1)
        last_name  = partes[0].strip().title()
        first_name = partes[1].strip().title()
    else:
        last_name  = nombre_completo.title()
        first_name = ""
    return first_name, last_name
 
 
# =============================================================================
#  FUNCIÓN 2 — Descargar CSV directamente (sin browser)
# =============================================================================
def descargar_csv():
    log.info("📥 Descargando CSV de Florida...")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    try:
        resp = requests.get(CSV_URL, headers=headers, timeout=300, stream=True)
        resp.raise_for_status()
        contenido = resp.content.decode("utf-8", errors="ignore")
        log.info(f"✅ CSV descargado — {len(contenido):,} caracteres")
        return contenido
    except Exception as e:
        log.error(f"❌ Error al descargar CSV: {e}")
        return None
 
 
# =============================================================================
#  FUNCIÓN 3 — Procesar y enviar el CSV en pedazos (para no agotar memoria)
# =============================================================================
def procesar_y_enviar_csv(texto_csv: str):
    log.info("🧹 Procesando CSV en pedazos pequeños...")
 
    # Leer solo las primeras líneas para detectar columnas
    primera_linea = texto_csv.split("\n")[0]
    columnas_raw  = primera_linea.strip().split(",")
    columnas      = [c.strip().strip('"').lower() for c in columnas_raw]
    log.info(f"   Columnas detectadas: {columnas}")
 
    # Identificar índices de columnas importantes
    col_nombre = next((c for c in ["licensee name", "name", "agent name", "full name", "individual name"] if c in columnas), None)
    col_tipo   = next((c for c in ["license type", "type", "license_type", "lic type", "category"] if c in columnas), None)
    col_email  = next((c for c in ["email", "email address", "e-mail"] if c in columnas), None)
    col_phone  = next((c for c in ["phone", "phone number", "telephone"] if c in columnas), None)
 
    if not col_nombre:
        log.error(f"❌ No encontré columna de nombre. Columnas: {columnas}")
        return 0, 0
 
    enviados = 0
    fallidos = 0
    total_procesadas = 0
 
    # Procesar en pedazos de 5,000 filas para no usar mucha memoria
    CHUNK = 5000
    for chunk in pd.read_csv(StringIO(texto_csv), dtype=str, low_memory=False, chunksize=CHUNK):
 
        # Normalizar columnas del chunk
        chunk.columns = chunk.columns.str.strip().str.lower()
 
        # Filtrar por Life & Annuity
        if col_tipo and col_tipo in chunk.columns:
            chunk[col_tipo] = chunk[col_tipo].str.strip().str.lower().fillna("")
            chunk = chunk[chunk[col_tipo].apply(lambda t: any(f in t for f in FILTRO_LIFE))]
 
        if chunk.empty:
            total_procesadas += CHUNK
            continue
 
        # Buscar columna de número de licencia (identificador único)
        col_licencia = next((c for c in ["license number", "fl license #", "license #", "lic #", "license_number"] if c in chunk.columns), None)
 
        # Separar nombre y enviar cada contacto
        for _, fila in chunk.iterrows():
            # Obtener el número de licencia como ID único
            licencia = str(fila.get(col_licencia, "")).strip() if col_licencia else ""
 
            # Si ya fue enviado antes, saltarlo
            if licencia and ya_fue_enviado(licencia):
                continue
 
            nombre_completo = fila.get(col_nombre, "")
            first_name, last_name = separar_nombre(nombre_completo)
 
            if not first_name and not last_name:
                continue
 
            email = fila.get(col_email, "") if col_email else ""
            phone = fila.get(col_phone, "") if col_phone else ""
 
            exito = enviar_a_ghl(first_name, last_name, email, phone)
            if exito:
                enviados += 1
                # Guardar en base de datos para no repetir
                if licencia:
                    marcar_enviado(licencia)
            else:
                fallidos += 1
 
            time.sleep(0.2)
 
        total_procesadas += CHUNK
        log.info(f"   → Procesadas: {total_procesadas:,} filas | Enviados a GHL: {enviados:,}")
 
    return enviados, fallidos
 
 
# =============================================================================
#  FUNCIÓN 4 — Enviar contacto a GHL
# =============================================================================
def enviar_a_ghl(first_name, last_name, email="", phone=""):
    if not GHL_API_KEY or not GHL_LOCATION_ID:
        log.error("❌ Faltan credenciales GHL.")
        return False
 
    headers = {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Content-Type": "application/json",
        "Version": "2021-07-28",
    }
 
    payload = {
        "locationId": GHL_LOCATION_ID,
        "firstName":  str(first_name).strip(),
        "lastName":   str(last_name).strip(),
        "tags":       TAGS_GHL,
        "source":     SOURCE_GHL,
    }
 
    if email and "@" in str(email):
        payload["email"] = str(email).strip().lower()
    if phone and str(phone).strip():
        payload["phone"] = str(phone).strip()
 
    try:
        resp = requests.post(
            "https://rest.gohighlevel.com/v1/contacts/",
            json=payload,
            headers=headers,
            timeout=30
        )
        return resp.status_code in (200, 201, 422)
    except Exception as e:
        log.error(f"❌ Error enviando a GHL: {e}")
        return False
 
 
# =============================================================================
#  FUNCIÓN PRINCIPAL
# =============================================================================
def ejecutar():
    log.info("=" * 60)
    log.info(f"🚀 Iniciando ciclo — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)
 
    texto = descargar_csv()
    if not texto:
        return
 
    enviados, fallidos = procesar_y_enviar_csv(texto)
    del texto  # liberar memoria inmediatamente
 
    log.info("=" * 60)
    log.info(f"✅ Ciclo terminado — Enviados: {enviados:,} | Fallidos: {fallidos:,}")
    log.info("=" * 60)
 
 
# =============================================================================
#  ARRANQUE
# =============================================================================
if __name__ == "__main__":
    log.info("🤖 Robot de Florida iniciado.")
    iniciar_db()
 
    ejecutar()
 
    schedule.every().hour.do(ejecutar)
    log.info("⏰ Programado para correr cada hora. Robot en espera...")
 
    while True:
        schedule.run_pending()
        time.sleep(30)
