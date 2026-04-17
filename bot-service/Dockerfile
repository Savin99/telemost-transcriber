FROM python:3.12-slim

WORKDIR /app

# Системные зависимости для Chrome, Xvfb, PulseAudio, FFmpeg
RUN apt-get update && apt-get install -y \
    wget gnupg2 \
    xvfb \
    pulseaudio \
    ffmpeg \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxkbcommon-x11-0 \
    libxrandr2 \
    libxss1 \
    libgl1-mesa-dri \
    libgl1-mesa-glx \
    dos2unix \
    && rm -rf /var/lib/apt/lists/*

# Chrome Stable (не Chromium — у него проблемы с WebRTC)
RUN wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | \
    gpg --dearmor -o /usr/share/keyrings/google-linux-signing-key.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-linux-signing-key.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
    > /etc/apt/sources.list.d/google-chrome.list && \
    apt-get update && apt-get install -y google-chrome-stable && \
    rm -rf /var/lib/apt/lists/*

# Python-зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright — устанавливаем Chromium-зависимости (сам Chrome уже есть)
RUN playwright install --with-deps chromium

# Копируем приложение
COPY . .

# Entrypoint
RUN dos2unix /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# Создаём директорию для записей
RUN mkdir -p /app/recordings

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
