"""
=============================================================
  Florida Life & Annuity License Scraper → GoHighLevel
=============================================================
¿Qué hace este script?
  1. Cada hora descarga el CSV de licencias de Florida (DFS)
  2. Separa los nombres: "APELLIDO, NOMBRE" → first_name / last_name
  3. Filtra solo licencias Life & Annuity activas/válidas
  4. Manda cada contacto a GHL con el tag 'licencia-florida-renovada'
 
Variables de entorno requeridas (agrégalas en Railway):
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
 
# Selenium — para abrir el browser invisible y aceptar términos
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
 
# ── Configuración de logs (para ver qué pasa en Railway) ──────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)
 
# ── Credenciales desde variables de entorno ───────────────────────────────────
GHL_API_KEY     = os.environ.get("GHL_API_KEY", "")
GHL_LOCATION_ID = os.environ.get("GHL_LOCATION_ID", "")
 
# ── URL del sitio de Florida ──────────────────────────────────────────────────
FLORIDA_URL = "https://licenseesearch.fldfs.com"
 
# Tipos de licencia que nos interesan (Life & Annuity)
LICENCIAS_VALIDAS = {
    "2-14", "2-15", "2-16",          # Life, Health, Variable Annuity
    "life", "annuity", "life & annuity",
    "life and health", "2-14 (life)",
}
 
# Tags que se agregan en GHL
TAGS_GHL  = ["RECRUIT AUTOMATICO"]
 
# Source que se agrega en GHL
SOURCE_GHL = "LICENSE SEARCH"
 
 
# =============================================================================
#  FUNCIÓN 1 — Separar nombre
#  El CSV trae: "GARCIA LOPEZ, ANA MARIA"
#  →  first_name = "Ana Maria"   |   last_name = "Garcia Lopez"
# =============================================================================
def separar_nombre(nombre_completo: str):
    nombre_completo = str(nombre_completo).strip()
 
    if "," in nombre_completo:
        partes      = nombre_completo.split(",", 1)   # parte solo en la 1ª coma
        last_name   = partes[0].strip().title()       # antes de la coma = apellido
        first_name  = partes[1].strip().title()       # después de la coma = nombre
    else:
        # Si no hay coma, todo va al apellido
        last_name  = nombre_completo.title()
        first_name = ""
 
    return first_name, last_name
 
 
# =============================================================================
#  FUNCIÓN 2 — Abrir browser invisible, aceptar términos y descargar CSV
# =============================================================================
def descargar_csv():
    log.info("🌐 Abriendo browser invisible hacia licenseesearch.fldfs.com ...")
 
    # ── Configurar Chrome en modo invisible (headless) ────────────────────────
    opciones = Options()
    opciones.add_argument("--headless=new")
    opciones.add_argument("--no-sandbox")
    opciones.add_argument("--disable-dev-shm-usage")
    opciones.add_argument("--disable-gpu")
    opciones.add_argument("--window-size=1280,720")
    opciones.add_argument("--disable-extensions")
    opciones.add_argument("--disable-infobars")
    opciones.add_argument("--disable-browser-side-navigation")
    opciones.add_argument("--disable-features=VizDisplayCompositor")
    opciones.add_argument("--single-process")
    opciones.add_argument("--memory-pressure-off")
    opciones.add_argument("--max_old_space_size=512")
    opciones.add_argument("--js-flags=--max-old-space-size=512")
 
    # Carpeta temporal para las descargas
    carpeta_descarga = "/tmp/florida_csv"
    os.makedirs(carpeta_descarga, exist_ok=True)
 
    prefs = {
        "download.default_directory":         carpeta_descarga,
        "download.prompt_for_download":       False,
        "download.directory_upgrade":         True,
        "safebrowsing.enabled":               True,
    }
    opciones.add_experimental_option("prefs", prefs)
 
    driver = None
    try:
        driver = webdriver.Chrome(options=opciones)
        wait   = WebDriverWait(driver, 20)
 
        # 1. Ir al sitio de Florida
        driver.get(FLORIDA_URL)
        log.info("   ✅ Sitio cargado")
 
        # 2. Aceptar los términos automáticamente
        #    (busca cualquier botón/checkbox de aceptar términos)
        try:
            # Intento 1 — checkbox de términos
            checkbox = wait.until(
                EC.presence_of_element_located((By.XPATH,
                    "//input[@type='checkbox' and (contains(@id,'agree') or contains(@name,'agree') or contains(@id,'term') or contains(@name,'term'))]"
                ))
            )
            if not checkbox.is_selected():
                checkbox.click()
            log.info("   ✅ Checkbox de términos aceptado")
        except Exception:
            pass
 
        try:
            # Intento 2 — botón de "I Agree" / "Accept" / "Acepto"
            boton = wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//input[@type='submit' or @type='button'] | //button"
                    "[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'agree') or "
                    "contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'accept') or "
                    "contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'acepto')]"
                ))
            )
            boton.click()
            log.info("   ✅ Botón de términos clickeado")
            time.sleep(2)
        except Exception:
            pass
 
        # 3. Buscar botón/link de exportar a CSV
        try:
            boton_csv = wait.until(
                EC.element_to_be_clickable((By.XPATH,
                    "//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'csv') or "
                    "contains(translate(@value,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'csv') or "
                    "contains(translate(@href,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'csv') or "
                    "contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'export')]"
                ))
            )
            boton_csv.click()
            log.info("   ✅ Clic en exportar CSV")
        except Exception as e:
            log.warning(f"   ⚠️ No encontré botón CSV directamente: {e}")
 
        # 4. Esperar que el archivo aparezca en la carpeta de descarga
        log.info("   ⏳ Esperando descarga del CSV...")
        archivo_csv = None
        for _ in range(30):   # espera hasta 30 segundos
            archivos = [
                f for f in os.listdir(carpeta_descarga)
                if f.endswith(".csv") and not f.endswith(".crdownload")
            ]
            if archivos:
                archivo_csv = os.path.join(carpeta_descarga, archivos[0])
                break
            time.sleep(1)
 
        if not archivo_csv:
            log.error("❌ El CSV no se descargó en 30 segundos.")
            return None
 
        with open(archivo_csv, "r", encoding="utf-8", errors="ignore") as f:
            contenido = f.read()
 
        # Borrar el archivo temporal
        os.remove(archivo_csv)
 
        log.info(f"✅ CSV descargado — {len(contenido):,} caracteres")
        return contenido
 
    except Exception as e:
        log.error(f"❌ Error en el browser: {e}")
        return None
 
    finally:
        if driver:
            driver.quit()
 
 
# =============================================================================
#  FUNCIÓN 3 — Limpiar y filtrar el CSV
# =============================================================================
def procesar_csv(texto_csv: str):
    log.info("🧹 Procesando CSV...")
 
    try:
        df = pd.read_csv(StringIO(texto_csv), dtype=str)
    except Exception as e:
        log.error(f"❌ No se pudo leer el CSV: {e}")
        return pd.DataFrame()
 
    log.info(f"   Total de filas descargadas: {len(df):,}")
    log.info(f"   Columnas: {list(df.columns)}")
 
    # ── Normalizar nombres de columnas (quitar espacios, pasar a minúsculas) ──
    df.columns = df.columns.str.strip().str.lower()
 
    # ── Buscar la columna de nombre ───────────────────────────────────────────
    col_nombre = None
    for posible in ["licensee name", "name", "agent name", "full name"]:
        if posible in df.columns:
            col_nombre = posible
            break
 
    if col_nombre is None:
        log.error("❌ No encontré la columna de nombre en el CSV.")
        log.error(f"   Columnas disponibles: {list(df.columns)}")
        return pd.DataFrame()
 
    # ── Buscar columna de tipo de licencia ────────────────────────────────────
    col_tipo = None
    for posible in ["license type", "type", "license_type", "lic type"]:
        if posible in df.columns:
            col_tipo = posible
            break
 
    # ── Buscar columna de status ──────────────────────────────────────────────
    col_status = None
    for posible in ["license status", "status", "lic status"]:
        if posible in df.columns:
            col_status = posible
            break
 
    # ── Filtrar por tipo de licencia Life & Annuity ───────────────────────────
    if col_tipo:
        df[col_tipo] = df[col_tipo].str.strip().str.lower()
        mask_tipo = df[col_tipo].apply(
            lambda t: any(v in str(t) for v in LICENCIAS_VALIDAS)
        )
        df = df[mask_tipo]
        log.info(f"   Después de filtrar por tipo: {len(df):,} filas")
    else:
        log.warning("⚠️  No encontré columna de tipo de licencia. Tomando todos.")
 
    # ── Filtrar solo licencias activas ────────────────────────────────────────
    if col_status:
        df[col_status] = df[col_status].str.strip().str.lower()
        df = df[df[col_status].isin(["active", "a", "activo", "current"])]
        log.info(f"   Después de filtrar activas: {len(df):,} filas")
    else:
        log.warning("⚠️  No encontré columna de status. Tomando todas.")
 
    # ── Separar nombre en first_name y last_name ──────────────────────────────
    df[["first_name", "last_name"]] = df[col_nombre].apply(
        lambda n: pd.Series(separar_nombre(n))
    )
 
    # ── Buscar columna de email (opcional) ────────────────────────────────────
    col_email = None
    for posible in ["email", "email address", "e-mail"]:
        if posible in df.columns:
            col_email = posible
            break
 
    # ── Buscar columna de teléfono (opcional) ─────────────────────────────────
    col_phone = None
    for posible in ["phone", "phone number", "telephone", "tel"]:
        if posible in df.columns:
            col_phone = posible
            break
 
    log.info(f"✅ CSV procesado — {len(df):,} contactos listos para GHL")
    return df, col_email, col_phone
 
 
# =============================================================================
#  FUNCIÓN 4 — Enviar un contacto a GHL
# =============================================================================
def enviar_a_ghl(first_name: str, last_name: str, email: str = "", phone: str = ""):
    if not GHL_API_KEY or not GHL_LOCATION_ID:
        log.error("❌ Faltan GHL_API_KEY o GHL_LOCATION_ID en las variables de entorno.")
        return False
 
    url = "https://rest.gohighlevel.com/v1/contacts/"
 
    headers = {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Content-Type":  "application/json",
        "Version":       "2021-07-28",
    }
 
    payload = {
        "locationId": GHL_LOCATION_ID,
        "firstName":  first_name,
        "lastName":   last_name,
        "tags":       TAGS_GHL,
        "source":     SOURCE_GHL,
    }
 
    if email and str(email).strip() and "@" in str(email):
        payload["email"] = str(email).strip().lower()
 
    if phone and str(phone).strip():
        payload["phone"] = str(phone).strip()
 
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
 
        if resp.status_code in (200, 201):
            return True
        elif resp.status_code == 422:
            # Contacto ya existe — GHL lo actualiza automáticamente
            return True
        else:
            log.warning(f"   ⚠️ GHL respondió {resp.status_code}: {resp.text[:200]}")
            return False
 
    except requests.RequestException as e:
        log.error(f"   ❌ Error de red al enviar a GHL: {e}")
        return False
 
 
# =============================================================================
#  FUNCIÓN PRINCIPAL — Se ejecuta cada hora
# =============================================================================
def ejecutar():
    log.info("=" * 60)
    log.info(f"🚀 Iniciando ciclo — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)
 
    # 1. Descargar
    texto_csv = descargar_csv()
    if texto_csv is None:
        log.error("❌ No se pudo descargar el CSV. Reintentará en la próxima hora.")
        return
 
    # 2. Procesar
    resultado = procesar_csv(texto_csv)
    if isinstance(resultado, pd.DataFrame) and resultado.empty:
        log.warning("⚠️ No hay contactos para enviar.")
        return
 
    df, col_email, col_phone = resultado
 
    if df.empty:
        log.warning("⚠️ No hay contactos para enviar después del filtro.")
        return
 
    # 3. Enviar a GHL
    enviados  = 0
    fallidos  = 0
    total     = len(df)
 
    log.info(f"📤 Enviando {total:,} contactos a GHL...")
 
    for _, fila in df.iterrows():
        first_name = str(fila.get("first_name", "")).strip()
        last_name  = str(fila.get("last_name",  "")).strip()
        email      = str(fila.get(col_email, "")).strip() if col_email else ""
        phone      = str(fila.get(col_phone, "")).strip() if col_phone else ""
 
        if not first_name and not last_name:
            continue
 
        exito = enviar_a_ghl(first_name, last_name, email, phone)
 
        if exito:
            enviados += 1
        else:
            fallidos += 1
 
        # Pequeña pausa para no saturar la API de GHL
        time.sleep(0.3)
 
        # Log de progreso cada 100 contactos
        if (enviados + fallidos) % 100 == 0:
            log.info(f"   → Progreso: {enviados + fallidos:,}/{total:,}")
 
    log.info("=" * 60)
    log.info(f"✅ Ciclo terminado — Enviados: {enviados:,} | Fallidos: {fallidos:,}")
    log.info("=" * 60)
 
 
# =============================================================================
#  ARRANQUE — Corre una vez al inicio y luego cada hora
# =============================================================================
if __name__ == "__main__":
    log.info("🤖 Robot de Florida iniciado.")
 
    if not GHL_API_KEY:
        log.warning("⚠️  GHL_API_KEY no configurada. Configúrala en Railway.")
    if not GHL_LOCATION_ID:
        log.warning("⚠️  GHL_LOCATION_ID no configurada. Configúrala en Railway.")
 
    # Corre una vez al arrancar
    ejecutar()
 
    # Luego corre cada hora
    schedule.every().hour.do(ejecutar)
    log.info("⏰ Programado para correr cada hora. Robot en espera...")
 
    while True:
        schedule.run_pending()
        time.sleep(30)
