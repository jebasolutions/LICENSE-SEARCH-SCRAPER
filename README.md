# 🦅 Jeba Solutions — Florida License Scraper

Descarga automáticamente el CSV de licencias de Florida,
filtra agentes Life & Annuity, y los envía a GoHighLevel CRM.

## ⚡ Cómo funciona

```
Cada lunes 6 AM →
Abre browser invisible →
Va a licenseesearch.fldfs.com →
Acepta términos automáticamente →
Descarga CSV →
Filtra Life & Annuity válidos →
Separa nombre/apellido →
Agrega tag "licencia-florida-renovada" →
Envía a GHL CRM →
GHL dispara workflow de reclutamiento
```

## 🔧 Setup local (para probar)

### 1. Instala dependencias
```bash
pip install -r requirements.txt
```

### 2. Configura tus credenciales
```bash
cp .env.example .env
# Edita .env con tu GHL_API_KEY y GHL_LOCATION_ID
```

### 3. Corre una vez para probar
```bash
python scraper.py
```

### 4. Activa el scheduler automático
```bash
python scheduler.py
```

## 🚀 Deploy en Railway (100% automático)

### 1. Sube a GitHub
```bash
git init
git add .
git commit -m "Jeba Solutions Florida Scraper"
git remote add origin https://github.com/TU_USUARIO/jeba-florida-scraper.git
git push -u origin main
```

### 2. Deploy en Railway
1. Ve a railway.app → New Project → Deploy from GitHub
2. Selecciona el repo jeba-florida-scraper
3. En Variables agrega:
   - GHL_API_KEY = tu API key de GHL
   - GHL_LOCATION_ID = tu location ID de GHL
4. Deploy → listo

### 3. Railway corre el scheduler 24/7
El servidor en Railway mantiene el scheduler corriendo.
Cada lunes a las 6 AM el script corre automáticamente.
Costo: $5/mes en Railway.

## 🔑 Dónde encontrar tus credenciales de GHL

**GHL_API_KEY:**
GHL → Settings → Company → API Keys → Create Key

**GHL_LOCATION_ID:**
GHL → Settings → Company → mira la URL del browser
Ejemplo: app.gohighlevel.com/location/ABC123xyz/settings
El ID es: ABC123xyz

## 📊 Filtros configurados

- License Type: Life Including Variable Annuity, Life Agent, Life & Health
- Status: Current, Active
- Tag automático: licencia-florida-renovada
- Source: Florida DFS License Search

## 📁 Estructura del proyecto

```
jeba-florida-scraper/
├── scraper.py          # Script principal
├── scheduler.py        # Scheduler automático (lunes 6 AM)
├── requirements.txt    # Dependencias Python
├── Dockerfile          # Para Railway/Docker
├── .env.example        # Template de variables
├── downloads/          # CSV descargados (temporal)
└── logs/               # Logs de ejecución
```
