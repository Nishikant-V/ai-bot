import os, hashlib, feedparser, httpx, re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from fastapi import FastAPI, Query, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from contextlib import asynccontextmanager
import json
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL   = os.getenv("DATABASE_URL")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL     = os.getenv("GROQ_MODEL", "llama3-8b-8192")
INGEST_API_KEY = os.getenv("INGEST_API_KEY", "")

RSS_FEEDS = {
    "Reuters":    "https://feeds.reuters.com/reuters/topNews",
    "BBC":        "https://feeds.bbci.co.uk/news/rss.xml",
    "NPR":        "https://feeds.npr.org/1001/rss.xml",
    "TechCrunch": "https://techcrunch.com/feed/",
    "CNBC":       "https://www.cnbc.com/id/100003114/device/rss/rss.html",
}

CATEGORIES = ["World", "Business", "Technology", "AI", "Science", "Health", "Sports"]

CATEGORY_KEYWORDS = {
    "AI":         ["artificial intelligence", "machine learning", "llm", "chatgpt", "openai", "deep learning", "neural", "ai "],
    "Technology": ["tech", "software", "app", "startup", "silicon", "cyber", "hack", "cloud", "robot", "crypto", "blockchain"],
    "Business":   ["market", "stock", "economy", "gdp", "trade", "finance", "bank", "inflation", "invest", "earnings", "billion", "million"],
    "Science":    ["research", "study", "scientist", "nasa", "space", "climate", "physics", "biology", "discovery"],
    "Health":     ["health", "covid", "vaccine", "hospital", "doctor", "cancer", "disease", "fda", "drug", "medical"],
    "Sports":     ["football", "soccer", "nba", "nfl", "tennis", "cricket", "olympic", "championship", "league", "match"],
    "World":      [],  # catch-all
}

# ── Database helpers ──────────────────────────────────────────────────────────

def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    return conn

def db_execute(conn, query: str, params=None):
    """Execute a query and return the cursor (caller must close it)."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(query, params or ())
    return cur

def init_db():
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id          BIGSERIAL PRIMARY KEY,
                url_hash    TEXT UNIQUE NOT NULL,
                title       TEXT NOT NULL,
                source      TEXT NOT NULL,
                date        TEXT,
                url         TEXT NOT NULL,
                content     TEXT,
                summary     TEXT,
                category    TEXT,
                ingested_at TEXT NOT NULL
            )
        """)
        cur.close()
    except Exception as e:
        print(f"DB init error: {e}")
        raise
    finally:
        conn.close()

def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()

def is_similar_title(t1: str, t2: str, threshold: float = 0.85) -> bool:
    return SequenceMatcher(None, t1.lower(), t2.lower()).ratio() >= threshold

def fetch_existing_titles(conn) -> list[str]:
    cur = db_execute(conn, "SELECT title FROM articles ORDER BY id DESC LIMIT 500")
    rows = cur.fetchall()
    cur.close()
    return [r["title"] for r in rows]

# ── Extractive fallbacks (no LLM needed) ─────────────────────────────────────

def extractive_summary(text: str, max_chars: int = 180) -> str:
    """Return the first complete sentence up to max_chars."""
    if not text:
        return "No summary available."
    text = re.sub(r'\s+', ' ', text).strip()
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

# ── Groq helpers ──────────────────────────────────────────────────────────────

def groq_available() -> bool:
    return bool(GROQ_API_KEY)

def groq_generate(prompt: str, timeout: int = 60) -> str:
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    r = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        json=payload,
        headers=headers,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

def extract_json_array(text: str) -> list:
    start = text.find("[")
    end   = text.rfind("]") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    return []

def extract_json_object(text: str) -> dict:
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    return {}

# ── LLM processing ───────────────────────────────────────────────────────────

def llm_process(articles: list[dict]) -> list[dict]:
    """Process articles with Groq; fall back to extractive methods if unavailable."""
    if not articles:
        return articles

    if not groq_available():
        print("Groq unavailable — using extractive fallback")
        for a in articles:
            a["summary"]  = extractive_summary(a.get("content", ""))
            a["category"] = keyword_category(a["title"], a.get("content", ""))
        return articles

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
            raw     = groq_generate(prompt)
            results = extract_json_array(raw)
            result_map = {r["id"]: r for r in results if "id" in r}
            for j, a in enumerate(batch):
                if j in result_map:
                    a["summary"]  = result_map[j].get("summary", "") or extractive_summary(a.get("content", ""))
                    cat           = result_map[j].get("category", "")
                    a["category"] = cat if cat in CATEGORIES else keyword_category(a["title"], a.get("content", ""))
                else:
                    a["summary"]  = extractive_summary(a.get("content", ""))
                    a["category"] = keyword_category(a["title"], a.get("content", ""))
        except Exception as e:
            print(f"Groq batch error: {e} — falling back to extractive")
            for a in batch:
                a["summary"]  = extractive_summary(a.get("content", ""))
                a["category"] = keyword_category(a["title"], a.get("content", ""))

    return articles

# ── App setup ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="News Intelligence Agent", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ingest")
def ingest(x_api_key: Optional[str] = Header(None)):
    # Enforce API key when one is configured (skip check in dev if key is unset)
    if INGEST_API_KEY and x_api_key != INGEST_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing x-api-key header")

    conn = get_db()
    existing_titles = fetch_existing_titles(conn)
    new_articles: list[dict] = []

    for source, feed_url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:20]:
                url   = getattr(entry, "link",  "")
                title = getattr(entry, "title", "").strip()
                if not url or not title:
                    continue
                h = url_hash(url)

                # Skip exact-URL duplicates
                cur = db_execute(conn, "SELECT id FROM articles WHERE url_hash=%s", (h,))
                exists = cur.fetchone()
                cur.close()
                if exists:
                    continue

                # Skip near-duplicate titles
                if any(is_similar_title(title, t) for t in existing_titles):
                    continue

                content  = getattr(entry, "summary", "") or getattr(entry, "description", "")
                date_str = ""
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    date_str = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()

                new_articles.append({
                    "url_hash": h, "title": title, "source": source,
                    "date": date_str, "url": url, "content": content,
                    "summary": "", "category": "",
                })
                existing_titles.append(title)
        except Exception as e:
            print(f"Error fetching {source}: {e}")

    if new_articles:
        processed = llm_process(new_articles)
        now = datetime.now(timezone.utc).isoformat()
        for a in processed:
            try:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO articles "
                    "(url_hash,title,source,date,url,content,summary,category,ingested_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (url_hash) DO NOTHING",
                    (a["url_hash"], a["title"], a["source"], a["date"],
                     a["url"], a["content"], a["summary"], a["category"], now),
                )
                cur.close()
            except Exception as e:
                print(f"Insert error: {e}")

    conn.close()
    return {"ingested": len(new_articles), "sources": list(RSS_FEEDS.keys())}


@app.get("/articles")
def get_articles(page: int = 1, per_page: int = 20, category: Optional[str] = None):
    conn   = get_db()
    offset = (page - 1) * per_page

    if category:
        cur   = db_execute(conn,
            "SELECT * FROM articles WHERE category=%s ORDER BY ingested_at DESC LIMIT %s OFFSET %s",
            (category, per_page, offset))
        rows  = cur.fetchall(); cur.close()
        cur2  = db_execute(conn, "SELECT COUNT(*) AS count FROM articles WHERE category=%s", (category,))
        total = cur2.fetchone()["count"]; cur2.close()
    else:
        cur   = db_execute(conn,
            "SELECT * FROM articles ORDER BY ingested_at DESC LIMIT %s OFFSET %s",
            (per_page, offset))
        rows  = cur.fetchall(); cur.close()
        cur2  = db_execute(conn, "SELECT COUNT(*) AS count FROM articles")
        total = cur2.fetchone()["count"]; cur2.close()

    conn.close()
    return {"articles": [dict(r) for r in rows], "total": total, "page": page, "per_page": per_page}


@app.get("/search")
def search(q: str = Query(""), category: Optional[str] = None):
    conn = get_db()
    like = f"%{q}%"

    if category:
        cur = db_execute(conn,
            "SELECT * FROM articles "
            "WHERE (title ILIKE %s OR content ILIKE %s OR summary ILIKE %s) "
            "AND category=%s ORDER BY ingested_at DESC LIMIT 50",
            (like, like, like, category))
    else:
        cur = db_execute(conn,
            "SELECT * FROM articles "
            "WHERE title ILIKE %s OR content ILIKE %s OR summary ILIKE %s "
            "ORDER BY ingested_at DESC LIMIT 50",
            (like, like, like))

    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"articles": [dict(r) for r in rows], "query": q}


@app.get("/briefing")
def briefing():
    conn  = get_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    cur  = db_execute(conn,
        "SELECT * FROM articles WHERE ingested_at >= %s ORDER BY ingested_at DESC LIMIT 100",
        (today,))
    rows = cur.fetchall(); cur.close()

    if not rows:
        cur  = db_execute(conn, "SELECT * FROM articles ORDER BY ingested_at DESC LIMIT 50")
        rows = cur.fetchall(); cur.close()

    articles = [dict(r) for r in rows]
    conn.close()

    if not articles:
        return {"executive_summary": "No articles available.", "top_stories": [], "trends": []}

    top_stories = articles[:10]

    def extractive_briefing():
        from collections import Counter
        sources = list({a["source"] for a in articles})
        cats    = list({a["category"] for a in articles if a.get("category")})
        summary = (
            f"Today's briefing covers {len(articles)} articles from "
            f"{', '.join(sources[:3])} and other sources. "
            f"Topics span {', '.join(cats[:4]) if cats else 'various categories'}."
        )
        cat_counts = Counter(a["category"] for a in articles if a.get("category"))
        trends = [f"{cat} ({count} stories)" for cat, count in cat_counts.most_common(5)]
        return {"executive_summary": summary, "top_stories": top_stories, "trends": trends}

    if not groq_available():
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
        raw    = groq_generate(prompt, timeout=90)
        result = extract_json_object(raw)
        return {
            "executive_summary": result.get("executive_summary", ""),
            "top_stories":       top_stories,
            "trends":            result.get("trends", []),
        }
    except Exception as e:
        print(f"Briefing Groq error: {e}")
        return extractive_briefing()


@app.get("/stats")
def stats():
    conn = get_db()

    cur   = db_execute(conn, "SELECT COUNT(*) AS count FROM articles")
    total = cur.fetchone()["count"]; cur.close()

    cur         = db_execute(conn,
        "SELECT category, COUNT(*) AS count FROM articles GROUP BY category ORDER BY count DESC")
    by_category = cur.fetchall(); cur.close()

    cur       = db_execute(conn,
        "SELECT source, COUNT(*) AS count FROM articles GROUP BY source ORDER BY count DESC")
    by_source = cur.fetchall(); cur.close()

    conn.close()
    return {
        "total":       total,
        "by_category": [dict(r) for r in by_category],
        "by_source":   [dict(r) for r in by_source],
    }
