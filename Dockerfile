FROM python:3.11-slim
 
# Instalar wget y dependencias mínimas
RUN apt-get update && apt-get install -y \
    wget \
    ca-certificates \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*
 
# Descargar e instalar Chrome (maneja sus propias dependencias)
RUN wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get update \
    && apt-get install -y ./google-chrome-stable_current_amd64.deb \
    && rm google-chrome-stable_current_amd64.deb \
    && rm -rf /var/lib/apt/lists/*
 
WORKDIR /app
 
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
 
COPY . .
 
CMD ["python", "scraper.py"]
 
