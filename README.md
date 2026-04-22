# telemost-transcriber

Автоматическая транскрибация встреч Telemost. Бот подключается к встрече, записывает аудио, транскрибирует с разделением по спикерам и отправляет результат в Telegram / Google Drive.

## Архитектура

```
Telegram          tg-bot               bot-service            transcriber-service
 (user)      (aiogram, порт -)      (FastAPI, порт 8000)     (FastAPI, порт 8001)
   |               |                       |                         |
   |-- /rec URL -->|-- POST /join -------->|                         |
   |               |                       |-- Playwright+Xvfb ----->| (Telemost)
   |               |                       |   (запись аудио)        |
   |-- /stop ----->|-- POST /leave ------->|                         |
   |               |                       |-- POST /transcribe ---->|
   |               |                       |   (WhisperX + pyannote) |
   |<-- transcript-|<-- результат ---------|<-- JSON segments -------|
   |               |                       |
   |               |                       |-- upload --> Google Drive
   |               |                       |
                   |
          drive_watcher.py
          (авто-транскрибация файлов из Google Drive)
```

**bot-service** — управляет записью встреч через headless-браузер (Playwright + Xvfb + PulseAudio), хранит статусы в SQLite/PostgreSQL, загружает транскрипты в Google Drive.

**transcriber-service** — транскрибация через WhisperX, диаризация через pyannote, опциональное улучшение через Claude (имена спикеров, текст транскрипта).

**tg-bot** — Telegram-интерфейс (`/rec`, `/stop`, `/status`). Включает `drive_watcher.py` для авто-транскрибации файлов из Google Drive.

## Prerequisites

- Python 3.12+
- NVIDIA GPU + CUDA 12.4 (для transcriber-service)
- FFmpeg
- Google Chrome (для Playwright)

## Quick Start (Docker)

```bash
cp .env.example .env
# Заполни обязательные переменные: TG_BOT_TOKEN, TELEMOST_SERVICE_API_KEY, HF_TOKEN
docker compose up
```

## Запуск локально (без Docker)

```bash
# transcriber-service
cd transcriber-service
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
pip install "whisperx @ git+https://github.com/m-bain/whisperX.git"
pip install -r requirements.txt
uvicorn app.main:app --port 8001

# bot-service
cd bot-service
pip install -r requirements.txt
playwright install chromium
uvicorn app.main:app --port 8000

# tg-bot
cd tg-bot
pip install -r requirements.txt
python bot.py
```

## Деплой на VDS

Продакшн-хост: **VDS 193.233.87.211**. GPU-пайплайн вынесен на Modal (`modal_app/whisperx_service.py`). На VDS: supervisord (bot-service + tg-bot + drive-watcher) + Docker (transcriber через Dockerfile.cpu).

### Настройка (один раз)

```bash
# Локально:
cp .env.deploy.example .env.deploy
# Отредактируй VDS_SSH_HOST/VDS_SSH_PORT/VDS_SSH_USER.
# SSH-ключ:
ssh-copy-id root@193.233.87.211
# Modal (для `./deploy_vds.sh modal`):
pip3 install --user modal
modal token new
```

Секреты на VDS лежат в `/root/telemost/env.sh` (mode 600) — формат `export VAR=...`. Шаблон переменных — в [.env.example](.env.example).

### Деплой обновлений

```bash
./deploy_vds.sh all                  # modal → transcriber → bot → tg → watcher
./deploy_vds.sh bot|tg|watcher       # rsync + supervisorctl restart + healthcheck
./deploy_vds.sh transcriber          # rsync + docker compose up -d --build + healthcheck
./deploy_vds.sh transcriber --no-build  # без пересборки образа (быстро)
./deploy_vds.sh modal                # локальный modal deploy modal_app/whisperx_service.py
./deploy_vds.sh status               # supervisorctl status + docker ps + df -h + free -m
./deploy_vds.sh logs transcriber     # tail -f logs/transcriber.log
./deploy_vds.sh restart bot          # только рестарт, без rsync
./deploy_vds.sh all --dry-run        # план без побочных эффектов
./deploy_vds.sh --help               # все флаги
```

Архив скриптов для старого Vast.ai GPU-хоста — в [`_deprecated/`](_deprecated/).

## Переменные окружения

Полный список с дефолтами и описаниями: [.env.example](.env.example)

Основные группы:
- **Обязательные**: `TG_BOT_TOKEN`, `TELEMOST_SERVICE_API_KEY`, `HF_TOKEN`
- **LLM (Claude)**: `ANTHROPIC_API_KEY`, `SPEAKER_LLM_*`, `TRANSCRIPT_LLM_*`, `MEETING_METADATA_*`
- **Google Drive**: `GDRIVE_FOLDER_ID`, `GDRIVE_CLIENT_SECRET`, `GDRIVE_TOKEN_PATH`

## Тесты

```bash
# Все сервисы
pytest bot-service/tests/ -v
pytest transcriber-service/tests/ -v
pytest tg-bot/tests/ -v

# Линтинг
ruff check .
```

CI запускается автоматически на push/PR в main (GitHub Actions).

## Скрипты

| Скрипт | Назначение |
|--------|-----------|
| `deploy_vds.sh` | Единый deploy на VDS: rsync + supervisorctl/docker + healthcheck + Modal deploy |
| `remote/rotate_logs.sh` | Хостовой logrotate, cron на VDS каждый час |
| `_deprecated/` | Архив Vast.ai-эпохи. Не запускать — выдают `[DEPRECATED]` + exit 1. |
