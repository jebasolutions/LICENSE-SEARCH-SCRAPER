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
TAGS_GHL    = ["IUL", "RECRUIT AUTOMATICO"]
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
#  FUNCIÓN 2 — Descargar CSV directo al disco (nunca en memoria completa)
# =============================================================================
CSV_LOCAL = "/tmp/florida.csv"

def descargar_csv():
    log.info("📥 Descargando CSV de Florida al disco...")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }

    MAX_INTENTOS = 5
    for intento in range(1, MAX_INTENTOS + 1):
        try:
            # Ver cuánto ya descargamos (para continuar desde donde se quedó)
            bytes_ya_descargados = os.path.getsize(CSV_LOCAL) if os.path.exists(CSV_LOCAL) else 0

            if bytes_ya_descargados > 0:
                mb_ya = bytes_ya_descargados / 1024 / 1024
                log.info(f"   Intento {intento}/{MAX_INTENTOS} — continuando desde {mb_ya:.1f} MB...")
                headers["Range"] = f"bytes={bytes_ya_descargados}-"
                modo_archivo = "ab"  # append — agrega al final
            else:
                log.info(f"   Intento {intento}/{MAX_INTENTOS} — descargando desde cero...")
                modo_archivo = "wb"  # write — empieza de cero

            with requests.get(CSV_URL, headers=headers, timeout=600, stream=True) as resp:
                # 206 = servidor acepta continuar desde donde nos quedamos
                # 200 = servidor manda todo desde cero
                if resp.status_code == 200 and bytes_ya_descargados > 0:
                    log.info("   El servidor no soporta continuar — descargando desde cero...")
                    modo_archivo = "wb"
                    bytes_ya_descargados = 0
                elif resp.status_code not in (200, 206):
                    resp.raise_for_status()

                bytes_nuevos = 0
                with open(CSV_LOCAL, modo_archivo) as f:
                    for pedazo in resp.iter_content(chunk_size=512 * 1024):
                        if pedazo:
                            f.write(pedazo)
                            bytes_nuevos += len(pedazo)

            total_mb = (bytes_ya_descargados + bytes_nuevos) / 1024 / 1024
            log.info(f"✅ CSV guardado en disco — {total_mb:.1f} MB totales")
            return CSV_LOCAL

        except Exception as e:
            log.warning(f"   ⚠️ Intento {intento} fallido: {e}")
            if intento < MAX_INTENTOS:
                espera = intento * 30
                log.info(f"   ⏳ Esperando {espera}s y continuando desde donde se quedó...")
                time.sleep(espera)
            else:
                log.error("❌ No se pudo descargar el CSV después de 5 intentos.")
                # Borrar archivo incompleto
                if os.path.exists(CSV_LOCAL):
                    os.remove(CSV_LOCAL)
                return None


# =============================================================================
#  FUNCIÓN 3 — Procesar y enviar el CSV en pedazos (para no agotar memoria)
# =============================================================================
def procesar_y_enviar_csv(ruta_csv: str):
    log.info("🧹 Procesando CSV desde disco en pedazos pequeños...")

    # Leer primera línea para detectar columnas (sin cargar todo el archivo)
    with open(ruta_csv, "r", encoding="utf-8", errors="ignore") as f:
        primera_linea = f.readline()

    columnas_raw = primera_linea.strip().split(",")
    columnas     = [c.strip().strip('"').lower() for c in columnas_raw]
    log.info(f"   Columnas detectadas: {columnas}")

    # Columnas exactas del CSV de Florida
    col_first   = next((c for c in ["first name", "firstname", "first_name"] if c in columnas), None)
    col_last    = next((c for c in ["last name", "lastname", "last_name"] if c in columnas), None)
    col_tipo    = next((c for c in ["license tycl desc", "license type", "license_type", "lic type"] if c in columnas), None)
    col_email   = next((c for c in ["email address", "email", "e-mail"] if c in columnas), None)
    col_phone   = next((c for c in ["business phone", "phone", "phone number"] if c in columnas), None)
    col_address = next((c for c in ["business address1", "business address", "address"] if c in columnas), None)
    col_city    = next((c for c in ["business city", "city"] if c in columnas), None)
    col_county  = next((c for c in ["business county", "county"] if c in columnas), None)
    col_npn     = next((c for c in ["npn number", "npn"] if c in columnas), None)

    if not col_first and not col_last:
        log.error(f"❌ No encontré columnas de nombre. Columnas: {columnas}")
        return 0, 0

    log.info(f"   ✅ Columnas mapeadas — Nombre: '{col_first}' '{col_last}' | Tipo: '{col_tipo}' | Email: '{col_email}'")

    enviados = 0
    fallidos = 0
    total_procesadas = 0

    # Leer directamente del archivo en disco — 5,000 filas a la vez
    CHUNK = 5000
    for chunk in pd.read_csv(ruta_csv, dtype=str, low_memory=False,
                              chunksize=CHUNK, encoding="utf-8", on_bad_lines="skip"):

        # Normalizar columnas del chunk
        chunk.columns = chunk.columns.str.strip().str.lower()

        # Filtrar por Life & Annuity
        if col_tipo and col_tipo in chunk.columns:
            chunk[col_tipo] = chunk[col_tipo].str.strip().str.lower().fillna("")
            chunk = chunk[chunk[col_tipo].apply(lambda t: any(f in t for f in FILTRO_LIFE))]

        if chunk.empty:
            total_procesadas += CHUNK
            continue

        col_licencia = "license number" if "license number" in chunk.columns else None

        # Filtrar por Life & Annuity en este chunk
        if col_tipo and col_tipo in chunk.columns:
            chunk[col_tipo] = chunk[col_tipo].str.strip().str.lower().fillna("")
            chunk = chunk[chunk[col_tipo].apply(lambda t: any(f in t for f in FILTRO_LIFE))]

        if chunk.empty:
            total_procesadas += CHUNK
            continue

        # Enviar cada contacto
        for _, fila in chunk.iterrows():
            licencia = str(fila.get(col_licencia, "")).strip() if col_licencia else ""

            if licencia and ya_fue_enviado(licencia):
                continue

            first_name = str(fila.get(col_first, "")).strip().title() if col_first else ""
            last_name  = str(fila.get(col_last,  "")).strip().title() if col_last  else ""

            if not first_name and not last_name:
                continue

            exito = enviar_a_ghl(
                first_name  = first_name,
                last_name   = last_name,
                email       = fila.get(col_email,   "") if col_email   else "",
                phone       = fila.get(col_phone,   "") if col_phone   else "",
                address     = fila.get(col_address, "") if col_address else "",
                city        = fila.get(col_city,    "") if col_city    else "",
                county      = fila.get(col_county,  "") if col_county  else "",
                license_num = licencia,
                npn         = fila.get(col_npn,     "") if col_npn     else "",
            )

            if exito:
                enviados += 1
                if licencia:
                    marcar_enviado(licencia)
            else:
                fallidos += 1

            time.sleep(0.2)

        total_procesadas += CHUNK
        log.info(f"   → Procesadas: {total_procesadas:,} filas | Enviados a GHL: {enviados:,}")

    return enviados, fallidos


# =============================================================================
#  FUNCIÓN 4 — Enviar contacto a GHL con todos los campos
# =============================================================================
def enviar_a_ghl(first_name, last_name, email="", phone="",
                 address="", city="", county="", license_num="", npn=""):
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
    if address and str(address).strip():
        payload["address1"] = str(address).strip().title()
    if city and str(city).strip():
        payload["city"] = str(city).strip().title()

    # Campos personalizados: LICENSE, COUNTY, NPN
    custom_fields = []
    if license_num and str(license_num).strip():
        custom_fields.append({"key": "license", "field_value": str(license_num).strip()})
    if county and str(county).strip():
        custom_fields.append({"key": "county", "field_value": str(county).strip().title()})
    if npn and str(npn).strip():
        custom_fields.append({"key": "npn", "field_value": str(npn).strip()})

    if custom_fields:
        payload["customFields"] = custom_fields

    try:
        resp = requests.post(
            "https://services.leadconnectorhq.com/contacts/",
            json=payload,
            headers=headers,
            timeout=30
        )
        if resp.status_code not in (200, 201, 422):
            log.warning(f"   GHL respondió {resp.status_code}: {resp.text[:200]}")
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

    ruta = descargar_csv()
    if not ruta:
        return

    enviados, fallidos = procesar_y_enviar_csv(ruta)

    # Borrar el archivo del disco al terminar
    try:
        os.remove(ruta)
        log.info("🗑️  Archivo temporal eliminado del disco")
    except Exception:
        pass

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
