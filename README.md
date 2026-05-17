# TeleGallery

**Production-grade Telegram photo gallery uploader**  
Upload large photo collections (50GB–500GB+) to Telegram channels as browsable albums — with full resumability, floodwait handling, and multi-account support.

---

## Features

- 📁 **Recursive folder scanning** — auto-detects all photo subfolders  
- 📸 **Album batching** — auto-chunks 10 photos per Telegram media group  
- 🔄 **Fully resumable** — survives crashes, restarts, and disconnects  
- ⏸ **Pause / Resume / Stop** — full upload lifecycle control  
- 🌊 **FloodWait handling** — auto-pauses with countdown, auto-resumes  
- 🔁 **Retry with backoff** — 5 attempts with exponential delay  
- 👥 **Multi-account** — failover between accounts during floodwait  
- 📊 **Live dashboard** — real-time progress, speed, ETA  
- 📋 **Logs panel** — color-coded structured log stream  
- ❌ **Failed upload management** — retry/export individual failures  
- 🌙 **Dark mode** — sleek dark UI by default  
- 🐳 **Docker support** — headless backend operation  

---

## Folder → Telegram Layout

```
Wedding/
├── Haldi/        →   🎉 HALDI PHOTOS START 🎉
│                     [album 1] [album 2] ...
│                     ✅ HALDI PHOTOS END ✅
├── Mehendi/      →   🎉 MEHENDI PHOTOS START 🎉
│                     [album 1] [album 2] ...
│                     ✅ MEHENDI PHOTOS END ✅
└── Reception/    →   🎉 RECEPTION PHOTOS START 🎉
                      [album 1] [album 2] ...
                      ✅ RECEPTION PHOTOS END ✅
```

---

## Tech Stack

| Layer     | Technology                        |
|-----------|-----------------------------------|
| Frontend  | React 18 + TypeScript + Tailwind  |
| State     | Zustand                           |
| Backend   | Python 3.12+ + FastAPI            |
| Telegram  | Pyrogram (MTProto)                |
| Database  | SQLite (via SQLAlchemy async)     |
| Config    | YAML + .env                       |
| Docker    | Docker + Compose                  |

---

## Quick Start

**Python:** 3.11 or 3.12 recommended. On Windows, use **PowerShell** (older shells do not support `&&` — use `;` or the scripts below).

### 1. Backend

**PowerShell (from repo root):**

```powershell
pip install -r backend\requirements.txt
py start.py
# → http://127.0.0.1:8000
# → API docs: http://127.0.0.1:8000/docs
```

Or: `.\scripts\start-backend.ps1`

**bash / macOS / Linux:**

```bash
pip install -r backend/requirements.txt
python start.py
```

### 2. Frontend (requires Node.js)

**PowerShell:**

```powershell
cd frontend
npm install
npm run dev
# → http://localhost:5173
```

Or: `.\scripts\start-frontend.ps1`

### 2b. Desktop shell (Tauri + Rust)

Requires [Rust](https://rustup.rs/) and a C++ toolchain on Windows (MSVC). From `frontend/`:

```bash
npm run tauri dev
```

This opens a native window pointing at the Vite dev server. For release builds, add app icons first (see [Tauri icons](https://v1.tauri.app/v1/guides/features/icons/)), then run `npm run tauri build`.

### 3. Docker (backend only)

```bash
docker-compose up --build
```

---

## Getting Telegram API Credentials

1. Go to [https://my.telegram.org](https://my.telegram.org)
2. Log in with your phone number
3. Click **API development tools**
4. Create an app → copy `api_id` and `api_hash`
5. Add the account in TeleGallery's **Accounts** page

---

## Configuration

Edit `config.yaml` to customize:

```yaml
templates:
  folder_start: "🎉 {folder_name} PHOTOS START 🎉"
  folder_end:   "✅ {folder_name} PHOTOS END ✅"

upload:
  album_size: 10        # max 10 (Telegram limit)
  max_retries: 5
  retry_base_delay: 5   # seconds
```

---

## Project Structure

```
TeleGallery/
├── backend/
│   ├── main.py             ← FastAPI app
│   ├── api/                ← REST endpoints
│   ├── engine/             ← Upload orchestrator
│   ├── telegram/           ← Pyrogram client & uploader
│   ├── db/                 ← SQLAlchemy models & repos
│   ├── services/           ← Business logic & SSE bus
│   ├── models/             ← Pydantic schemas
│   ├── config/             ← Settings
│   └── utils/              ← File scanner, logger
├── src-tauri/              ← Tauri desktop shell (optional)
├── frontend/
│   ├── src/
│   │   ├── pages/          ← Dashboard, Accounts, Logs, Failed, History, Settings
│   │   ├── components/     ← FloodwaitBanner, etc.
│   │   ├── store/          ← Zustand stores
│   │   ├── api/            ← Typed Axios client
│   │   ├── hooks/          ← useSSE
│   │   └── types/          ← Shared TypeScript types
│   └── package.json
├── config.yaml
├── docker-compose.yml
├── Dockerfile
├── start.py
└── README.md
```

---

## Resume Strategy

On every restart TeleGallery:
1. Finds the last `running` or `paused` session in SQLite
2. Resets any files stuck in `uploading` state → `pending`
3. Skips folders where `start_msg_id` is already saved
4. Skips files where `status = uploaded`
5. Resumes from the first non-completed album

No duplicate uploads. No duplicate START/END messages. Ever.

---

## Notes

- `tgcrypto` (C extension for faster crypto) is optional — Pyrogram falls back to pure-Python `pyaes`
- Install Visual C++ Build Tools if you want `tgcrypto` for better performance on Windows
- **Session strings** live in SQLite. Set `TELEGALLERY_SECRET_KEY` in `.env` to store them **encrypted** (see `.env.example`). Otherwise they are saved as plaintext (fine for local dev).
- On backend start, saved accounts are **warmed** (auto-connect) so uploads and **FloodWait failover** work across multiple accounts without clicking Connect each time.
- **FloodWait failover**: with `failover_on_floodwait: true` in `config.yaml` (default), if one account hits FloodWait and another account is connected, uploads **switch** to the other account immediately instead of waiting.
- Run tests (PowerShell): `.\scripts\run-tests.ps1` — or `cd backend; pip install -r requirements.txt; py -m pytest tests -q`

---

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for system design, resume/floodwait/retry strategies, and diagrams.

---

## License

MIT — open source, free to use and modify.
