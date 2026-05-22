"""
=============================================================
  GoLeadAI — Florida License Scraper → GoHighLevel v2
=============================================================

CÓMO FUNCIONA:

  PRIMERA VEZ (arranque):
    1. Descarga el CSV completo de Florida (~319 MB)
    2. Filtra solo licencias Life, Annuity y Health
    3. Envía cada agente a GHL con:
         - Tags:   IUL, RECRUIT AUTOMATICO
         - Source: LICENSE SEARCH
         - Campos: nombre, email, teléfono, dirección, ciudad,
                   license number, county, NPN, fecha vencimiento,
                   fecha de captura, postal code
    4. Guarda cada número de licencia en SQLite

  CADA HORA (corridas siguientes):
    1. Hace un HEAD request al servidor de Florida
       → Si el CSV NO cambió: no descarga nada, espera la próxima hora
       → Si SÍ cambió: descarga el CSV completo actualizado
    2. Lee el CSV en pedazos de 5,000 filas (no usa mucha memoria)
    3. Por cada agente Life/Annuity/Health:
       → Si su licencia YA está en SQLite: lo salta (ya fue enviado)
       → Si es NUEVO: lo envía a GHL y lo guarda en SQLite
    4. Resultado: solo los contactos nuevos llegan a GHL

  COLUMNAS DEL CSV DE FLORIDA (confirmadas):
    first name            → firstName en GHL
    last name             → lastName en GHL
    license tycl desc     → filtro Life / Annuity / Health
    license number        → campo personalizado + clave de dedup
    npn number            → campo personalizado NPN
    email address         → email en GHL
    business phone        → teléfono en GHL
    business address1     → dirección en GHL
    business city         → ciudad en GHL
    business county       → se separa automáticamente:
                             · nombre del condado → campo personalizado county
                             · zip de 5 dígitos  → postalCode en GHL
    license exp date      → campo personalizado license_exp_date
    (automático)          → campo personalizado date_captured

  VARIABLES DE ENTORNO (configurar en Railway):
    GHL_API_KEY       → Private Integration Token (pit-...)
    GHL_LOCATION_ID   → ID de la sub-cuenta en GHL

  CAMPOS PERSONALIZADOS A CREAR EN GHL:
    license, county, npn, license_exp_date, date_captured
=============================================================
"""

import os
import re
import time
import logging
import sqlite3
import requests
import pandas as pd
import schedule
from datetime import datetime

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Credenciales GHL ──────────────────────────────────────────────────────────
GHL_API_KEY     = os.environ.get("GHL_API_KEY", "")
GHL_LOCATION_ID = os.environ.get("GHL_LOCATION_ID", "")

# ── URL del CSV de Florida ────────────────────────────────────────────────────
CSV_URL   = "https://www.myfloridacfo.com/downloads/AAS/LicenseeSearch/AllValidLicensesIndividual.csv"
CSV_LOCAL = "/tmp/florida.csv"

# ── Configuración ─────────────────────────────────────────────────────────────
TAGS_GHL    = ["IUL", "RECRUIT AUTOMATICO"]
SOURCE_GHL  = "LICENSE SEARCH"
DB_PATH     = "/app/procesados.db"
PAUSA_GHL   = 0.2
CHUNK_SIZE  = 5000

# Filtro: Life, Annuity y Health
FILTRO_LIFE = ["life", "annuity", "health", "2-14", "2-15", "2-16", "2-40", "2-57"]
# 2-14 = Life (including annuities and variable contracts)
# 2-15 = Life, Health & Annuity
# 2-16 = Life Agent
# 2-40 = Health
# 2-57 = Health (Limited Benefit)

# Columnas del CSV de Florida
MAP_FIRST    = ["first name", "firstname", "first_name"]
MAP_LAST     = ["last name",  "lastname",  "last_name"]
MAP_TIPO     = ["license tycl desc", "license type", "license_type", "lic type"]
MAP_LIC      = ["license number", "license_number", "lic number"]
MAP_NPN      = ["npn number", "npn"]
MAP_EMAIL    = ["email address", "email", "e-mail"]
MAP_PHONE    = ["business phone", "phone", "phone number"]
MAP_ADDRESS  = ["business address1", "business address", "address1", "address"]
MAP_CITY     = ["business city", "city"]
MAP_COUNTY   = ["business county", "county"]
MAP_LIC_EXP  = ["license exp date", "license expiration date", "expiration date",
                "exp date", "lic exp date", "license expiry date", "expiry date"]


# =============================================================================
#  BASE DE DATOS
# =============================================================================
def iniciar_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS enviados (
            licencia TEXT PRIMARY KEY,
            fecha    TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS config (
            clave TEXT PRIMARY KEY,
            valor TEXT
        )
    """)
    conn.commit()
    conn.close()
    total = contar_enviados()
    log.info(f"📂 Base de datos lista — {total:,} agentes ya procesados anteriormente")

def ya_fue_enviado(licencia: str) -> bool:
    conn   = sqlite3.connect(DB_PATH)
    existe = conn.execute("SELECT 1 FROM enviados WHERE licencia = ?", (licencia,)).fetchone() is not None
    conn.close()
    return existe

def marcar_enviado(licencia: str):
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

def get_config(clave: str) -> str:
    conn = sqlite3.connect(DB_PATH)
    row  = conn.execute("SELECT valor FROM config WHERE clave = ?", (clave,)).fetchone()
    conn.close()
    return row[0] if row else ""

def set_config(clave: str, valor: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO config (clave, valor) VALUES (?, ?)",
        (clave, valor)
    )
    conn.commit()
    conn.close()


# =============================================================================
#  VERIFICAR SI EL CSV CAMBIÓ
# =============================================================================
def csv_fue_actualizado() -> bool:
    try:
        resp = requests.head(CSV_URL, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0"
        })
        nuevo_lm  = resp.headers.get("Last-Modified", "").strip()
        ultimo_lm = get_config("csv_last_modified")

        if not nuevo_lm:
            log.info("   ℹ️  Servidor no retornó Last-Modified — descargando para asegurar")
            return True

        if nuevo_lm != ultimo_lm:
            log.info(f"   🆕 CSV actualizado detectado")
            log.info(f"      Anterior: {ultimo_lm or '(primera corrida)'}")
            log.info(f"      Nuevo:    {nuevo_lm}")
            return True
        else:
            log.info(f"   ✅ CSV sin cambios desde: {nuevo_lm}")
            log.info(f"      No hay agentes nuevos — esperando próxima hora")
            return False

    except Exception as e:
        log.warning(f"   ⚠️ No se pudo verificar HEAD ({e}) — descargando de todas formas")
        return True


# =============================================================================
#  DESCARGAR CSV AL DISCO
# =============================================================================
def descargar_csv() -> str | None:
    log.info("📥 Descargando CSV de Florida al disco...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0"
    }

    for intento in range(1, 6):
        try:
            bytes_ya = os.path.getsize(CSV_LOCAL) if os.path.exists(CSV_LOCAL) else 0

            if bytes_ya > 0:
                log.info(f"   Intento {intento}/5 — continuando desde {bytes_ya/1024/1024:.1f} MB...")
                headers["Range"] = f"bytes={bytes_ya}-"
                modo = "ab"
            else:
                log.info(f"   Intento {intento}/5 — descargando desde cero...")
                modo = "wb"

            with requests.get(CSV_URL, headers=headers, timeout=600, stream=True) as resp:
                if resp.status_code == 200 and bytes_ya > 0:
                    log.info("   El servidor no soporta Range → descargando desde cero...")
                    modo     = "wb"
                    bytes_ya = 0
                elif resp.status_code not in (200, 206):
                    resp.raise_for_status()

                lm = resp.headers.get("Last-Modified", "").strip()
                if lm:
                    set_config("csv_last_modified", lm)

                bytes_nuevos = 0
                with open(CSV_LOCAL, modo) as f:
                    for pedazo in resp.iter_content(chunk_size=512 * 1024):
                        if pedazo:
                            f.write(pedazo)
                            bytes_nuevos += len(pedazo)

            total_mb = (bytes_ya + bytes_nuevos) / 1024 / 1024
            log.info(f"✅ CSV guardado en disco — {total_mb:.1f} MB")
            return CSV_LOCAL

        except Exception as e:
            log.warning(f"   ⚠️ Intento {intento} fallido: {e}")
            if intento < 5:
                espera = intento * 30
                log.info(f"   ⏳ Esperando {espera}s antes de reintentar...")
                time.sleep(espera)
            else:
                log.error("❌ No se pudo descargar el CSV tras 5 intentos.")
                if os.path.exists(CSV_LOCAL):
                    os.remove(CSV_LOCAL)
                return None


# =============================================================================
#  SEPARAR COUNTY Y ZIP CODE
#  El zip de 5 dígitos viene dentro del campo "business county"
#  Ejemplo: "DADE 33101" → county="Dade", zip="33101"
# =============================================================================
def extraer_zip_del_county(valor: str) -> tuple:
    valor = str(valor).strip()
    if not valor or valor.lower() in ("nan", "none", ""):
        return "", ""

    match = re.search(r'\b(\d{5})(?:-\d{4})?\b', valor)
    if match:
        zip_code      = match.group(1)
        county_limpio = re.sub(r'\b\d{5}(?:-\d{4})?\b', '', valor)
        county_limpio = re.sub(r'\s+', ' ', county_limpio).strip().strip(',').strip()
        return county_limpio.title(), zip_code
    else:
        return valor.title(), ""


# =============================================================================
#  MAPEAR COLUMNAS
# =============================================================================
def mapear_columnas(ruta_csv: str) -> dict:
    with open(ruta_csv, "r", encoding="utf-8", errors="ignore") as f:
        primera_linea = f.readline()

    columnas = [c.strip().strip('"').lower() for c in primera_linea.strip().split(",")]
    log.info(f"   Columnas en el CSV: {columnas}")

    def buscar(opciones):
        return next((c for c in opciones if c in columnas), None)

    mapa = {
        "first":   buscar(MAP_FIRST),
        "last":    buscar(MAP_LAST),
        "tipo":    buscar(MAP_TIPO),
        "lic":     buscar(MAP_LIC),
        "npn":     buscar(MAP_NPN),
        "email":   buscar(MAP_EMAIL),
        "phone":   buscar(MAP_PHONE),
        "address": buscar(MAP_ADDRESS),
        "city":    buscar(MAP_CITY),
        "county":  buscar(MAP_COUNTY),
        "lic_exp": buscar(MAP_LIC_EXP),
    }

    log.info(f"   ✅ Columnas mapeadas:")
    log.info(f"      Nombre:           '{mapa['first']}' + '{mapa['last']}'")
    log.info(f"      Tipo lic:         '{mapa['tipo']}'  (filtro Life/Annuity/Health)")
    log.info(f"      Licencia:         '{mapa['lic']}'   (clave única)")
    log.info(f"      NPN:              '{mapa['npn']}'")
    log.info(f"      Email:            '{mapa['email']}'")
    log.info(f"      Teléfono:         '{mapa['phone']}'")
    log.info(f"      Ciudad:           '{mapa['city']}'")
    log.info(f"      County+Zip:       '{mapa['county']}'  ← se separan automáticamente")
    log.info(f"      Vencimiento lic:  '{mapa['lic_exp']}'")
    log.info(f"      Fecha captura:    automática (fecha de hoy)")
    log.info(f"      Dirección:        '{mapa['address']}'")

    if not mapa["first"] and not mapa["last"]:
        log.error("❌ No se encontraron columnas de nombre en el CSV.")
        return {}

    return mapa


# =============================================================================
#  PROCESAR CSV Y ENVIAR SOLO LOS NUEVOS A GHL
# =============================================================================
def procesar_y_enviar_csv(ruta_csv: str) -> tuple:
    log.info("🧹 Procesando CSV en pedazos de 5,000 filas...")

    mapa = mapear_columnas(ruta_csv)
    if not mapa:
        return 0, 0, 0

    enviados    = 0
    fallidos    = 0
    omitidos    = 0
    filas_total = 0
    life_total  = 0

    for chunk in pd.read_csv(
        ruta_csv,
        dtype=str,
        low_memory=False,
        chunksize=CHUNK_SIZE,
        encoding="utf-8",
        on_bad_lines="skip"
    ):
        chunk.columns = chunk.columns.str.strip().str.lower()
        filas_total  += len(chunk)

        # Filtrar Life, Annuity y Health
        col_tipo = mapa.get("tipo")
        if col_tipo and col_tipo in chunk.columns:
            chunk[col_tipo] = chunk[col_tipo].str.strip().str.lower().fillna("")
            chunk = chunk[chunk[col_tipo].apply(
                lambda t: any(p in t for p in FILTRO_LIFE)
            )]

        if chunk.empty:
            continue

        life_total += len(chunk)

        for _, fila in chunk.iterrows():
            col_lic  = mapa.get("lic")
            licencia = str(fila.get(col_lic, "")).strip() if col_lic else ""

            # Saltar si ya fue enviado
            if licencia and ya_fue_enviado(licencia):
                omitidos += 1
                continue

            first_name = str(fila.get(mapa["first"], "")).strip().title() if mapa.get("first") else ""
            last_name  = str(fila.get(mapa["last"],  "")).strip().title() if mapa.get("last")  else ""

            if not first_name and not last_name:
                continue

            # Separar county y zip del mismo campo
            county_raw           = fila.get(mapa["county"], "") if mapa.get("county") else ""
            county_limpio, zip_c = extraer_zip_del_county(county_raw)

            exito = enviar_a_ghl(
                first_name    = first_name,
                last_name     = last_name,
                email         = fila.get(mapa["email"],   "") if mapa.get("email")   else "",
                phone         = fila.get(mapa["phone"],   "") if mapa.get("phone")   else "",
                address       = fila.get(mapa["address"], "") if mapa.get("address") else "",
                city          = fila.get(mapa["city"],    "") if mapa.get("city")    else "",
                county        = county_limpio,
                zip_code      = zip_c,
                license_num   = licencia,
                npn           = fila.get(mapa["npn"],     "") if mapa.get("npn")     else "",
                lic_exp       = fila.get(mapa["lic_exp"], "") if mapa.get("lic_exp") else "",
                date_captured = datetime.now().strftime("%Y-%m-%d"),
            )

            if exito:
                enviados += 1
                if licencia:
                    marcar_enviado(licencia)
            else:
                fallidos += 1

            time.sleep(PAUSA_GHL)

        log.info(
            f"   → Filas: {filas_total:,} | Life/Ann/Health: {life_total:,} | "
            f"Nuevos→GHL: {enviados:,} | Ya enviados: {omitidos:,}"
        )

    return enviados, fallidos, omitidos


# =============================================================================
#  ENVIAR CONTACTO A GHL (API v2)
# =============================================================================
def enviar_a_ghl(first_name, last_name, email="", phone="",
                 address="", city="", zip_code="", county="",
                 license_num="", npn="", lic_exp="", date_captured="") -> bool:

    if not GHL_API_KEY or not GHL_LOCATION_ID:
        log.error("❌ Faltan variables de entorno: GHL_API_KEY o GHL_LOCATION_ID")
        return False

    headers = {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Content-Type":  "application/json",
        "Version":       "2021-07-28",
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

    if phone and str(phone).strip() not in ("", "nan"):
        payload["phone"] = str(phone).strip()

    if address and str(address).strip() not in ("", "nan"):
        payload["address1"] = str(address).strip().title()

    if city and str(city).strip() not in ("", "nan"):
        payload["city"] = str(city).strip().title()

    if zip_code and str(zip_code).strip() not in ("", "nan"):
        payload["postalCode"] = str(zip_code).strip()

    # Campos personalizados
    custom_fields = []

    if license_num and str(license_num).strip() not in ("", "nan"):
        custom_fields.append({"key": "license",          "field_value": str(license_num).strip()})

    if county and str(county).strip() not in ("", "nan"):
        custom_fields.append({"key": "county",           "field_value": str(county).strip().title()})

    if npn and str(npn).strip() not in ("", "nan"):
        custom_fields.append({"key": "npn",              "field_value": str(npn).strip()})

    if lic_exp and str(lic_exp).strip() not in ("", "nan"):
        custom_fields.append({"key": "license_exp_date", "field_value": str(lic_exp).strip()})

    if date_captured and str(date_captured).strip() not in ("", "nan"):
        custom_fields.append({"key": "date_captured",    "field_value": str(date_captured).strip()})

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
            log.warning(f"   ⚠️ GHL respondió {resp.status_code}: {resp.text[:200]}")
        return resp.status_code in (200, 201, 422)

    except requests.exceptions.Timeout:
        log.error(f"❌ Timeout enviando a GHL ({first_name} {last_name})")
        return False
    except Exception as e:
        log.error(f"❌ Error enviando a GHL: {e}")
        return False


# =============================================================================
#  CICLO PRINCIPAL
# =============================================================================
def ejecutar():
    log.info("=" * 60)
    log.info(f"🚀 Ciclo iniciado — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"   Agentes ya en base de datos: {contar_enviados():,}")
    log.info("=" * 60)

    log.info("🔍 Verificando si hay nuevos datos en Florida...")
    if not csv_fue_actualizado():
        log.info("⏭️  Sin cambios — no hay nada nuevo que enviar.")
        log.info("=" * 60)
        return

    ruta = descargar_csv()
    if not ruta:
        log.error("❌ No se pudo obtener el CSV. Abortando ciclo.")
        return

    enviados, fallidos, omitidos = procesar_y_enviar_csv(ruta)

    try:
        os.remove(ruta)
        log.info("🗑️  Archivo temporal eliminado del disco")
    except Exception:
        pass

    log.info("=" * 60)
    log.info(f"✅ Ciclo completado")
    log.info(f"   🆕 Nuevos enviados a GHL:        {enviados:,}")
    log.info(f"   ⏭️  Ya existían (omitidos):       {omitidos:,}")
    log.info(f"   ❌ Errores:                       {fallidos}")
    log.info(f"   📊 Total acumulado en base datos: {contar_enviados():,}")
    log.info("=" * 60)


# =============================================================================
#  ARRANQUE
# =============================================================================
if __name__ == "__main__":
    log.info("🤖 GoLeadAI — Florida License Scraper")
    log.info(f"   GHL Location: {GHL_LOCATION_ID[:10]}..." if GHL_LOCATION_ID else "   ⚠️  GHL_LOCATION_ID no configurado")
    log.info(f"   GHL API Key:  {GHL_API_KEY[:14]}..." if GHL_API_KEY else "   ⚠️  GHL_API_KEY no configurado")

    iniciar_db()
    ejecutar()

    schedule.every().hour.do(ejecutar)
    log.info("⏰ Programado: corre cada hora. Esperando...")

    while True:
        schedule.run_pending()
        time.sleep(30)
