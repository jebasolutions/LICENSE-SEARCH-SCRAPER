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
TAGS_GHL   = ["RECRUIT AUTOMATICO"]
SOURCE_GHL = "LICENSE SEARCH"
 
# Palabras clave para filtrar Life & Annuity
FILTRO_LIFE = ["life", "annuity", "2-14", "2-15", "2-16"]
 
 
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
#  FUNCIÓN 3 — Procesar y filtrar el CSV
# =============================================================================
def procesar_csv(texto_csv: str):
    log.info("🧹 Procesando CSV...")
    try:
        df = pd.read_csv(StringIO(texto_csv), dtype=str, low_memory=False)
    except Exception as e:
        log.error(f"❌ No se pudo leer el CSV: {e}")
        return None
 
    log.info(f"   Total filas: {len(df):,}")
    log.info(f"   Columnas: {list(df.columns)}")
 
    # Normalizar columnas
    df.columns = df.columns.str.strip().str.lower()
 
    # Buscar columna de nombre
    col_nombre = None
    for c in ["licensee name", "name", "agent name", "full name", "individual name"]:
        if c in df.columns:
            col_nombre = c
            break
 
    if col_nombre is None:
        log.error(f"❌ No encontré columna de nombre. Columnas: {list(df.columns)}")
        return None
 
    # Buscar columna de tipo de licencia
    col_tipo = None
    for c in ["license type", "type", "license_type", "lic type", "category"]:
        if c in df.columns:
            col_tipo = c
            break
 
    # Filtrar por Life & Annuity
    if col_tipo:
        df[col_tipo] = df[col_tipo].str.strip().str.lower().fillna("")
        mask = df[col_tipo].apply(lambda t: any(f in t for f in FILTRO_LIFE))
        df = df[mask]
        log.info(f"   Después de filtrar Life & Annuity: {len(df):,} filas")
    else:
        log.warning("⚠️  No encontré columna de tipo. Usando todos los registros.")
 
    # Separar nombre
    df[["first_name", "last_name"]] = df[col_nombre].apply(
        lambda n: pd.Series(separar_nombre(n))
    )
 
    # Columnas opcionales
    col_email = next((c for c in ["email", "email address", "e-mail"] if c in df.columns), None)
    col_phone = next((c for c in ["phone", "phone number", "telephone"] if c in df.columns), None)
 
    log.info(f"✅ Listos para enviar: {len(df):,} contactos")
    return df, col_email, col_phone
 
 
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
 
    resultado = procesar_csv(texto)
    if not resultado:
        return
 
    df, col_email, col_phone = resultado
    if df.empty:
        log.warning("⚠️  Sin contactos para enviar.")
        return
 
    enviados = fallidos = 0
    total = len(df)
    log.info(f"📤 Enviando {total:,} contactos a GHL...")
 
    for _, fila in df.iterrows():
        exito = enviar_a_ghl(
            first_name = fila.get("first_name", ""),
            last_name  = fila.get("last_name", ""),
            email      = fila.get(col_email, "") if col_email else "",
            phone      = fila.get(col_phone, "") if col_phone else "",
        )
        if exito:
            enviados += 1
        else:
            fallidos += 1
 
        time.sleep(0.3)
 
        if (enviados + fallidos) % 100 == 0:
            log.info(f"   → Progreso: {enviados + fallidos:,}/{total:,}")
 
    log.info("=" * 60)
    log.info(f"✅ Ciclo terminado — Enviados: {enviados:,} | Fallidos: {fallidos:,}")
    log.info("=" * 60)
 
 
# =============================================================================
#  ARRANQUE
# =============================================================================
if __name__ == "__main__":
    log.info("🤖 Robot de Florida iniciado.")
 
    ejecutar()
 
    schedule.every().hour.do(ejecutar)
    log.info("⏰ Programado para correr cada hora. Robot en espera...")
 
    while True:
        schedule.run_pending()
        time.sleep(30)
