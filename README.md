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

## Деплой на Vast.ai

### Первичная настройка сервера

```bash
# На сервере:
bash /workspace/telemost-transcriber/bootstrap_vast_host.sh

# Заполни /workspace/.bashrc секретами:
export TG_BOT_TOKEN=...
export TELEMOST_SERVICE_API_KEY=...
export HF_TOKEN=...
# и другие переменные (см. .env.example)
```

### Деплой обновлений

```bash
# Локально: создай .env.deploy
echo 'VAST_SSH="ssh -p PORT root@IP"' > .env.deploy

# Деплой всех сервисов:
./deploy.sh all

# Деплой конкретного сервиса:
./deploy.sh tg|watcher|bot|transcriber

# Быстрый деплой через rsync (без git):
./hotfix.sh bot
```

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
| `deploy.sh` | git push + pull + рестарт сервисов на Vast.ai |
| `hotfix.sh` | rsync + рестарт (без git, для быстрых итераций) |
| `bootstrap_vast_host.sh` | Первичная настройка Vast.ai сервера |
| `remote/restart_service.sh` | Скрипт рестарта, исполняемый на сервере |
