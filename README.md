# News Intelligence Agent

AI-powered news aggregation — runs fully locally with Ollama. No paid services, no API keys.

## Quick Start

### 1. Ollama (LLM — optional but recommended)

```bash
# Install from https://ollama.com, then:
ollama pull qwen3:4b
```

> **Without Ollama**: the app still works — summaries are extracted from article text and categories are assigned via keyword matching.

### 2. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Backend runs at **http://localhost:8000** · API docs at **http://localhost:8000/docs**

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend runs at **http://localhost:3000**

---

## Usage

1. Open **http://localhost:3000**
2. Click **"⚡ Fetch News"** in the sidebar
3. Articles are pulled from RSS feeds, deduplicated, and processed by Ollama
4. Use the **Daily Briefing** tab for an executive summary + trends

## API

| Endpoint | Method | Description |
|---|---|---|
| `/ingest` | POST | Pull RSS feeds + LLM process |
| `/articles` | GET | List (`?page=1&per_page=20&category=AI`) |
| `/search` | GET | Search (`?q=keyword&category=Technology`) |
| `/briefing` | GET | Executive summary + top 10 + trends |
| `/stats` | GET | Article counts by source/category |

## RSS Sources

Reuters · BBC · AP News · TechCrunch · CNBC

## Folder Structure

```
.
├── backend/
│   ├── main.py          # FastAPI — all routes, Ollama, SQLite
│   └── requirements.txt
├── frontend/
│   ├── package.json
│   ├── vite.config.js
│   ├── index.html
│   └── src/
│       ├── main.jsx
│       ├── App.jsx      # All React components
│       └── App.css
├── .env.example
└── README.md
```
