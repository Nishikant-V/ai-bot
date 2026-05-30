import os, sqlite3, hashlib, feedparser, httpx, re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from contextlib import asynccontextmanager
import json

DB_PATH = os.getenv("DB_PATH", "./news.db")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b")

RSS_FEEDS = {
    "Reuters": "https://feeds.reuters.com/reuters/topNews",
    "BBC": "http://feeds.bbci.co.uk/news/rss.xml",
    "AP News": "https://rsshub.app/apnews/topics/apf-topnews",
    "TechCrunch": "https://techcrunch.com/feed/",
    "CNBC": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
}

CATEGORIES = ["World", "Business", "Technology", "AI", "Science", "Health", "Sports"]

# Keyword map for extractive category fallback
CATEGORY_KEYWORDS = {
    "AI": ["artificial intelligence", "machine learning", "llm", "chatgpt", "openai", "deep learning", "neural", "ai "],
    "Technology": ["tech", "software", "app", "startup", "silicon", "cyber", "hack", "cloud", "robot", "crypto", "blockchain"],
    "Business": ["market", "stock", "economy", "gdp", "trade", "finance", "bank", "inflation", "invest", "earnings", "billion", "million"],
    "Science": ["research", "study", "scientist", "nasa", "space", "climate", "physics", "biology", "discovery"],
    "Health": ["health", "covid", "vaccine", "hospital", "doctor", "cancer", "disease", "fda", "drug", "medical"],
    "Sports": ["football", "soccer", "nba", "nfl", "tennis", "cricket", "olympic", "championship", "league", "match"],
    "World": [],  # catch-all
}

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url_hash TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            source TEXT NOT NULL,
            date TEXT,
            url TEXT NOT NULL,
            content TEXT,
            summary TEXT,
            category TEXT,
            ingested_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()

def is_similar_title(t1: str, t2: str, threshold=0.85) -> bool:
    return SequenceMatcher(None, t1.lower(), t2.lower()).ratio() >= threshold

def fetch_existing_titles(conn) -> list[str]:
    rows = conn.execute("SELECT title FROM articles ORDER BY id DESC LIMIT 500").fetchall()
    return [r["title"] for r in rows]

# ── Extractive fallbacks (no LLM needed) ────────────────────────────────────

def extractive_summary(text: str, max_chars: int = 180) -> str:
    """Return the first complete sentence up to max_chars."""
    if not text:
        return "No summary available."
    text = re.sub(r'\s+', ' ', text).strip()
    # Try to cut at sentence boundary
    match = re.search(r'[.!?]', text[:max_chars + 50])
    if match and match.end() <= max_chars + 50:
        return text[:match.end()].strip()
    return text[:max_chars].rsplit(' ', 1)[0].strip() + "…"

def keyword_category(title: str, content: str) -> str:
    combined = (title + " " + (content or "")).lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            return cat
    return "World"

# ── Ollama helpers ───────────────────────────────────────────────────────────

def ollama_available() -> bool:
    try:
        r = httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False

def ollama_generate(prompt: str, timeout: int = 60) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2},
    }
    r = httpx.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json().get("response", "").strip()

def extract_json_array(text: str) -> list:
    # Strip <think>...</think> blocks (qwen3 thinking mode)
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    start = text.find("[")
    end = text.rfind("]") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    return []

def extract_json_object(text: str) -> dict:
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    return {}

# ── LLM processing ───────────────────────────────────────────────────────────

def llm_process(articles: list[dict]) -> list[dict]:
    """Process articles with Ollama; fall back to extractive methods if unavailable."""
    if not articles:
        return articles

    if not ollama_available():
        print("Ollama unavailable — using extractive fallback")
        for a in articles:
            a["summary"] = extractive_summary(a.get("content", ""))
            a["category"] = keyword_category(a["title"], a.get("content", ""))
        return articles

    # Process in small batches to stay within context window
    BATCH = 5
    for i in range(0, len(articles), BATCH):
        batch = articles[i:i + BATCH]
        batch_text = "\n\n".join(
            f"ID:{j}\nTITLE:{a['title']}\nCONTENT:{(a.get('content') or '')[:300]}"
            for j, a in enumerate(batch)
        )
        prompt = (
            f"For each news article below, return a JSON array. "
            f"Each object must have:\n"
            f'- "id": the numeric ID shown\n'
            f'- "summary": one sentence summary (max 25 words)\n'
            f'- "category": exactly one of {CATEGORIES}\n\n'
            f"Articles:\n{batch_text}\n\n"
            f"Return ONLY a valid JSON array. No explanation, no markdown."
        )
        try:
            raw = ollama_generate(prompt)
            results = extract_json_array(raw)
            result_map = {r["id"]: r for r in results if "id" in r}
            for j, a in enumerate(batch):
                if j in result_map:
                    a["summary"] = result_map[j].get("summary", "") or extractive_summary(a.get("content", ""))
                    cat = result_map[j].get("category", "")
                    a["category"] = cat if cat in CATEGORIES else keyword_category(a["title"], a.get("content", ""))
                else:
                    a["summary"] = extractive_summary(a.get("content", ""))
                    a["category"] = keyword_category(a["title"], a.get("content", ""))
        except Exception as e:
            print(f"Ollama batch error: {e} — falling back to extractive")
            for a in batch:
                a["summary"] = extractive_summary(a.get("content", ""))
                a["category"] = keyword_category(a["title"], a.get("content", ""))

    return articles

# ── App setup ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="News Intelligence Agent", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Routes ───────────────────────────────────────────────────────────────────

@app.post("/ingest")
def ingest():
    conn = get_db()
    existing_titles = fetch_existing_titles(conn)
    new_articles = []

    for source, feed_url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:20]:
                url = getattr(entry, "link", "")
                title = getattr(entry, "title", "").strip()
                if not url or not title:
                    continue
                h = url_hash(url)
                if conn.execute("SELECT id FROM articles WHERE url_hash=?", (h,)).fetchone():
                    continue
                if any(is_similar_title(title, t) for t in existing_titles):
                    continue
                content = getattr(entry, "summary", "") or getattr(entry, "description", "")
                date_str = ""
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    date_str = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
                new_articles.append({
                    "url_hash": h, "title": title, "source": source,
                    "date": date_str, "url": url, "content": content,
                    "summary": "", "category": ""
                })
                existing_titles.append(title)
        except Exception as e:
            print(f"Error fetching {source}: {e}")

    if new_articles:
        processed = llm_process(new_articles)
        now = datetime.now(timezone.utc).isoformat()
        for a in processed:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO articles "
                    "(url_hash,title,source,date,url,content,summary,category,ingested_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (a["url_hash"], a["title"], a["source"], a["date"],
                     a["url"], a["content"], a["summary"], a["category"], now)
                )
            except Exception as e:
                print(f"Insert error: {e}")
        conn.commit()

    conn.close()
    return {"ingested": len(new_articles), "sources": list(RSS_FEEDS.keys())}

@app.get("/articles")
def get_articles(page: int = 1, per_page: int = 20, category: Optional[str] = None):
    conn = get_db()
    offset = (page - 1) * per_page
    if category:
        rows = conn.execute(
            "SELECT * FROM articles WHERE category=? ORDER BY ingested_at DESC LIMIT ? OFFSET ?",
            (category, per_page, offset)
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM articles WHERE category=?", (category,)).fetchone()[0]
    else:
        rows = conn.execute(
            "SELECT * FROM articles ORDER BY ingested_at DESC LIMIT ? OFFSET ?",
            (per_page, offset)
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    conn.close()
    return {"articles": [dict(r) for r in rows], "total": total, "page": page, "per_page": per_page}

@app.get("/search")
def search(q: str = Query(""), category: Optional[str] = None):
    conn = get_db()
    like = f"%{q}%"
    if category:
        rows = conn.execute(
            "SELECT * FROM articles WHERE (title LIKE ? OR content LIKE ? OR summary LIKE ?) "
            "AND category=? ORDER BY ingested_at DESC LIMIT 50",
            (like, like, like, category)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM articles WHERE title LIKE ? OR content LIKE ? OR summary LIKE ? "
            "ORDER BY ingested_at DESC LIMIT 50",
            (like, like, like)
        ).fetchall()
    conn.close()
    return {"articles": [dict(r) for r in rows], "query": q}

@app.get("/briefing")
def briefing():
    conn = get_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT * FROM articles WHERE ingested_at >= ? ORDER BY ingested_at DESC LIMIT 100",
        (today,)
    ).fetchall()
    if not rows:
        rows = conn.execute("SELECT * FROM articles ORDER BY ingested_at DESC LIMIT 50").fetchall()
    articles = [dict(r) for r in rows]
    conn.close()

    if not articles:
        return {"executive_summary": "No articles available.", "top_stories": [], "trends": []}

    top_stories = articles[:10]

    # Extractive fallback briefing
    def extractive_briefing():
        sources = list({a["source"] for a in articles})
        cats = list({a["category"] for a in articles if a.get("category")})
        summary = (
            f"Today's briefing covers {len(articles)} articles from "
            f"{', '.join(sources[:3])} and other sources. "
            f"Topics span {', '.join(cats[:4]) if cats else 'various categories'}."
        )
        # Derive trends from category counts
        from collections import Counter
        cat_counts = Counter(a["category"] for a in articles if a.get("category"))
        trends = [f"{cat} ({count} stories)" for cat, count in cat_counts.most_common(5)]
        return {"executive_summary": summary, "top_stories": top_stories, "trends": trends}

    if not ollama_available():
        return extractive_briefing()

    stories_text = "\n".join(
        f"- {a['title']} ({a['source']}): {a.get('summary', '')}" for a in top_stories
    )
    prompt = (
        "You are a news editor. Based on these top stories, provide:\n"
        "1. A 3-sentence executive summary of today's news\n"
        "2. Top 5 trending themes/topics as short phrases\n\n"
        f"Stories:\n{stories_text}\n\n"
        'Return ONLY valid JSON: {"executive_summary": "...", "trends": ["t1","t2","t3","t4","t5"]}'
    )
    try:
        raw = ollama_generate(prompt, timeout=90)
        result = extract_json_object(raw)
        return {
            "executive_summary": result.get("executive_summary", ""),
            "top_stories": top_stories,
            "trends": result.get("trends", []),
        }
    except Exception as e:
        print(f"Briefing LLM error: {e}")
        return extractive_briefing()

@app.get("/stats")
def stats():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    by_category = conn.execute(
        "SELECT category, COUNT(*) as count FROM articles GROUP BY category ORDER BY count DESC"
    ).fetchall()
    by_source = conn.execute(
        "SELECT source, COUNT(*) as count FROM articles GROUP BY source ORDER BY count DESC"
    ).fetchall()
    conn.close()
    return {
        "total": total,
        "by_category": [dict(r) for r in by_category],
        "by_source": [dict(r) for r in by_source],
    }
