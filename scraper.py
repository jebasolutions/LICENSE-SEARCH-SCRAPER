"""
Jeba Solutions — Florida License Scraper
Descarga automáticamente el CSV de licencias de Florida,
filtra agentes Life & Annuity, y los manda a GoHighLevel CRM.
"""

import os
import time
import logging
import requests
import pandas as pd
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ── Logging ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/scraper.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ── Configuración — pon tus valores en .env ───────────────
GHL_API_KEY        = os.getenv("GHL_API_KEY", "TU_API_KEY_AQUI")
GHL_LOCATION_ID    = os.getenv("GHL_LOCATION_ID", "TU_LOCATION_ID_AQUI")
DOWNLOAD_DIR       = os.path.abspath("downloads")
CSV_FILENAME       = "AllLicensesRequiringCE.csv"
FLORIDA_URL        = "https://licenseesearch.fldfs.com/BulkDownload"

# Filtros
LICENSE_TYPES = [
    "Life Including Variable Annuity",
    "Life Agent",
    "Life & Health",
]
VALID_STATUSES = ["Current", "Active"]

# Tag y source que llegarán a GHL
CONTACT_TAG    = "licencia-florida-renovada"
CONTACT_SOURCE = "Florida DFS License Search"


# ─────────────────────────────────────────────────────────
# PASO 1 — Descargar el CSV con Selenium
# ─────────────────────────────────────────────────────────
def download_csv():
    log.info("🌐 Abriendo browser para descargar CSV de Florida...")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    chrome_options = Options()
    chrome_options.add_argument("--headless")          # Sin ventana visible
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_experimental_option("prefs", {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    })

    driver = webdriver.Chrome(options=chrome_options)
    wait   = WebDriverWait(driver, 30)

    try:
        driver.get(FLORIDA_URL)
        log.info("✅ Página cargada")

        # Acepta términos si aparece un checkbox o botón
        try:
            checkbox = wait.until(EC.presence_of_element_located(
                (By.XPATH, "//input[@type='checkbox']")
            ))
            if not checkbox.is_selected():
                checkbox.click()
                log.info("✅ Términos aceptados (checkbox)")
            time.sleep(1)
        except Exception:
            log.info("ℹ️  No hay checkbox de términos — continuando")

        # Busca el link de descarga "AllLicensesRequiringCE"
        try:
            download_link = wait.until(EC.element_to_be_clickable(
                (By.PARTIAL_LINK_TEXT, "All Licenses Requiring CE")
            ))
            download_link.click()
            log.info("✅ Clic en descarga iniciado")
        except Exception:
            # Plan B — buscar por href que contenga el tipo
            links = driver.find_elements(By.TAG_NAME, "a")
            for link in links:
                href = link.get_attribute("href") or ""
                if "AllLicensesRequiringCE" in href or "RequiringCE" in href:
                    link.click()
                    log.info(f"✅ Descarga iniciada via href: {href}")
                    break

        # Espera que el archivo termine de descargarse (max 3 min)
        log.info("⏳ Esperando descarga...")
        csv_path = os.path.join(DOWNLOAD_DIR, CSV_FILENAME)
        for i in range(180):
            if os.path.exists(csv_path) and not os.path.exists(csv_path + ".crdownload"):
                log.info(f"✅ CSV descargado: {csv_path}")
                return csv_path
            time.sleep(1)

        raise TimeoutError("❌ El archivo no terminó de descargar en 3 minutos")

    finally:
        driver.quit()


# ─────────────────────────────────────────────────────────
# PASO 2 — Limpiar y filtrar el CSV
# ─────────────────────────────────────────────────────────
def clean_csv(csv_path):
    log.info("🧹 Procesando CSV...")

    # Lee el CSV manejando comillas y encoding especiales
    df = pd.read_csv(
        csv_path,
        encoding="latin-1",       # Florida usa latin-1, no UTF-8
        on_bad_lines="skip",       # Salta líneas con error de formato
        quotechar='"',
        dtype=str,
        low_memory=False
    )

    log.info(f"📊 Total filas descargadas: {len(df)}")
    log.info(f"📋 Columnas: {list(df.columns)}")

    # Normaliza nombres de columnas (quita espacios, uppercase)
    df.columns = [c.strip().upper().replace(" ", "_") for c in df.columns]

    # Detecta las columnas relevantes automáticamente
    name_col    = next((c for c in df.columns if "NAME" in c), None)
    type_col    = next((c for c in df.columns if "TYPE" in c or "CLASS" in c), None)
    status_col  = next((c for c in df.columns if "STATUS" in c), None)
    email_col   = next((c for c in df.columns if "EMAIL" in c), None)
    phone_col   = next((c for c in df.columns if "PHONE" in c or "TEL" in c), None)
    city_col    = next((c for c in df.columns if "CITY" in c), None)
    license_col = next((c for c in df.columns if "LICENSE" in c and "NUM" in c), None) or \
                  next((c for c in df.columns if "LICENSE" in c), None)

    log.info(f"🔍 Columna nombre: {name_col}")
    log.info(f"🔍 Columna tipo: {type_col}")
    log.info(f"🔍 Columna status: {status_col}")

    # Filtra por tipo de licencia Life & Annuity
    if type_col:
        mask_type = df[type_col].str.strip().str.upper().isin(
            [t.upper() for t in LICENSE_TYPES]
        )
        df = df[mask_type]
        log.info(f"✅ Después de filtrar por tipo: {len(df)} agentes")

    # Filtra por status válido
    if status_col:
        mask_status = df[status_col].str.strip().str.upper().isin(
            [s.upper() for s in VALID_STATUSES]
        )
        df = df[mask_status]
        log.info(f"✅ Después de filtrar por status: {len(df)} agentes")

    # Separa nombre y apellido
    # Formato Florida: "APELLIDO, NOMBRE" o "APELLIDO NOMBRE"
    def split_name(full_name):
        if pd.isna(full_name):
            return "", ""
        full_name = str(full_name).strip()
        if "," in full_name:
            # Formato: "RODRIGUEZ, MARIA A"
            parts = full_name.split(",", 1)
            last  = parts[0].strip().title()
            first = parts[1].strip().split()[0].title() if parts[1].strip() else ""
        else:
            # Formato: "RODRIGUEZ MARIA A"
            parts = full_name.split()
            last  = parts[0].title() if parts else ""
            first = parts[1].title() if len(parts) > 1 else ""
        return first, last

    if name_col:
        df[["first_name", "last_name"]] = df[name_col].apply(
            lambda x: pd.Series(split_name(x))
        )
    else:
        df["first_name"] = ""
        df["last_name"]  = ""

    # Capitaliza ciudad
    if city_col:
        df["city_clean"] = df[city_col].str.strip().str.title()
    else:
        df["city_clean"] = ""

    # Agrega tag y source
    df["tag"]    = CONTACT_TAG
    df["source"] = CONTACT_SOURCE

    # Renombra columnas para GHL
    rename_map = {}
    if email_col:   rename_map[email_col]   = "email"
    if phone_col:   rename_map[phone_col]   = "phone"
    if license_col: rename_map[license_col] = "license_number"
    if city_col:    rename_map[city_col]    = "city_raw"

    df = df.rename(columns=rename_map)

    # Selecciona solo columnas necesarias
    keep = ["first_name", "last_name", "email", "phone",
            "city_clean", "license_number", "tag", "source"]
    keep = [c for c in keep if c in df.columns]
    df   = df[keep].drop_duplicates(subset=["email"]).dropna(subset=["email"])

    log.info(f"🎯 Agentes listos para enviar a GHL: {len(df)}")
    return df


# ─────────────────────────────────────────────────────────
# PASO 3 — Enviar contactos a GoHighLevel
# ─────────────────────────────────────────────────────────
def send_to_ghl(df):
    log.info("📤 Enviando contactos a GoHighLevel...")

    headers = {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Content-Type": "application/json",
        "Version": "2021-07-28"
    }

    success = 0
    errors  = 0
    total   = len(df)

    for i, row in df.iterrows():
        payload = {
            "firstName": row.get("first_name", ""),
            "lastName":  row.get("last_name", ""),
            "email":     row.get("email", ""),
            "phone":     row.get("phone", ""),
            "city":      row.get("city_clean", ""),
            "source":    row.get("source", CONTACT_SOURCE),
            "tags":      [CONTACT_TAG],
            "customFields": [
                {
                    "key":   "license_number",
                    "value": row.get("license_number", "")
                }
            ]
        }

        # Agrega location ID si está configurado
        if GHL_LOCATION_ID and GHL_LOCATION_ID != "TU_LOCATION_ID_AQUI":
            payload["locationId"] = GHL_LOCATION_ID

        try:
            response = requests.post(
                "https://services.leadconnectorhq.com/contacts/",
                json=payload,
                headers=headers,
                timeout=10
            )

            if response.status_code in [200, 201]:
                success += 1
            elif response.status_code == 422:
                # Contacto ya existe — intenta actualizar
                log.debug(f"Contacto ya existe: {row.get('email')} — saltando")
                success += 1
            else:
                log.warning(f"Error {response.status_code} para {row.get('email')}: {response.text[:100]}")
                errors += 1

        except Exception as e:
            log.error(f"Excepción enviando {row.get('email')}: {e}")
            errors += 1

        # Rate limit — máx 10 requests por segundo en GHL
        if (i + 1) % 10 == 0:
            time.sleep(1)
            log.info(f"  Progreso: {i+1}/{total} ({success} exitosos, {errors} errores)")

    log.info(f"✅ COMPLETADO: {success} contactos enviados a GHL, {errors} errores")
    return success, errors


# ─────────────────────────────────────────────────────────
# MAIN — Orquesta todo el proceso
# ─────────────────────────────────────────────────────────
def main():
    start = datetime.now()
    log.info("=" * 60)
    log.info(f"🦅 JEBA SOLUTIONS — Florida License Scraper")
    log.info(f"⏰ Iniciando: {start.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)

    try:
        # Paso 1: Descargar
        csv_path = download_csv()

        # Paso 2: Limpiar y filtrar
        df = clean_csv(csv_path)

        if df.empty:
            log.warning("⚠️  No se encontraron agentes Life & Annuity. Revisa los filtros.")
            return

        # Paso 3: Enviar a GHL
        success, errors = send_to_ghl(df)

        # Resumen final
        elapsed = (datetime.now() - start).seconds
        log.info("=" * 60)
        log.info(f"🎯 RESUMEN FINAL")
        log.info(f"   Agentes enviados a GHL: {success}")
        log.info(f"   Errores: {errors}")
        log.info(f"   Tiempo total: {elapsed} segundos")
        log.info(f"   Próxima ejecución: Lunes 6:00 AM")
        log.info("=" * 60)

    except Exception as e:
        log.error(f"❌ Error crítico: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
