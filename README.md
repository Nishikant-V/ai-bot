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
2. Click **"⚡ Fetch News"** in the sidebar (or run `POST /ingest` programmatically)
3. Articles are pulled from standard RSS feeds and Google News feeds, deduplicated, and processed by LLM/heuristics.
4. Use the **Daily Briefing** tab for the intelligence dashboard.

## API

| Endpoint | Method | Description |
|---|---|---|
| `/ingest` | POST | Pull RSS & Google News feeds + extract metadata |
| `/articles` | GET | List (`?page=1&per_page=20&category=AI`) sorted by importance |
| `/search` | GET | Search (`?q=keyword&category=Technology`) sorted by importance |
| `/briefing` | GET | Upgraded executive summary, risks, opportunities, top entities |
| `/stats` | GET | Article counts by source/category |

## Metadata Fields Extracted

For each article, the agent stores and exposes:
- **importance_score**: Prioritization ranking from 1 to 10
- **why_it_matters**: A 2-sentence significance summary
- **entities**: Extracted people, companies, organizations, countries, and locations

## RSS Sources

Reuters · BBC · NPR · TechCrunch · CNBC · Google News (Top, World, Technology, Business, AI searches)

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
