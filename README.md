# AtmosphereAI

A simple prototype: FastAPI backend + lightweight React UI (single-file `index.html`).

## Run locally

### Backend
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000 --reload
