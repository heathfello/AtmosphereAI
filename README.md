# AtmosphereAI

A simple prototype: FastAPI backend + lightweight React UI (single-file `index.html`).

## Run locally

### Backend
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000 --reload

