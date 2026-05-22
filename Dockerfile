FROM python:3.11-slim

# Instala Chrome y dependencias
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Directorio de trabajo
WORKDIR /app

# Instala dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instala ChromeDriver automáticamente
RUN pip install webdriver-manager

# Copia el código
COPY . .

# Crea carpetas necesarias
RUN mkdir -p downloads logs

# Corre el scheduler (espera el lunes 6 AM)
# Para correr inmediatamente: CMD ["python", "scraper.py"]
CMD ["python", "scheduler.py"]
