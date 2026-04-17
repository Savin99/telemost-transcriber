"""Быстрая проверка файлов на Google Drive."""

import os
import sys

os.environ.setdefault("GDRIVE_CLIENT_SECRET", "../credentials/client_secret.json")
os.environ.setdefault("GDRIVE_TOKEN_PATH", "../credentials/gdrive_token.json")
os.environ.setdefault("GDRIVE_FOLDER_ID", "1jwDy7XAtvX327nf0MJWZHzFERBwkbjvR")

from gdrive import _get_drive_service, GDRIVE_FOLDER_ID

svc = _get_drive_service()
if not svc:
    print("ERROR: Drive not authorized")
    sys.exit(1)

files = (
    svc.files()
    .list(
        q=f"'{GDRIVE_FOLDER_ID}' in parents and trashed=false",
        fields="files(id,name,mimeType,description,size)",
    )
    .execute()
    .get("files", [])
)

for f in files:
    desc = f.get("description", "") or ""
    size_mb = int(f.get("size", 0)) / 1024 / 1024
    marker = " [DONE]" if "transcribed" in desc else ""
    print(f"  {f['name']:60} {size_mb:7.1f}MB  mime={f.get('mimeType', '?')}{marker}")
