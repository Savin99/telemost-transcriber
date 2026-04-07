FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Python 3.11 + системные зависимости
RUN apt-get update && apt-get install -y \
    python3.11 \
    python3.11-venv \
    python3-pip \
    ffmpeg \
    git \
    && ln -sf /usr/bin/python3.11 /usr/bin/python3 \
    && ln -sf /usr/bin/python3.11 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# PyTorch с CUDA
RUN pip install --no-cache-dir \
    torch torchaudio --index-url https://download.pytorch.org/whl/cu124

# WhisperX — транскрипция + alignment + диаризация в одном пакете
RUN pip install --no-cache-dir whisperx

# FastAPI и зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Директория для записей (shared volume)
RUN mkdir -p /app/recordings

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
