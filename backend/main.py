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
from psycopg2.pool import ThreadedConnectionPool

DATABASE_URL       = os.getenv("DATABASE_URL")
INGEST_API_KEY     = os.getenv("INGEST_API_KEY", "")
FRONTEND_URL       = os.getenv("FRONTEND_URL", "")  # e.g. https://your-app.vercel.app

# ── LLM provider config (all optional — app works without any) ───────────────
# Priority: OpenRouter → Gemini → extractive fallback
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL   = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL       = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")


RSS_FEEDS = {
    "Reuters":    "https://feeds.reuters.com/reuters/topNews",
    "BBC":        "https://feeds.bbci.co.uk/news/rss.xml",
    "NPR":        "https://feeds.npr.org/1001/rss.xml",
    "TechCrunch": "https://techcrunch.com/feed/",
    "CNBC":       "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    # Priority 1: Google News Feeds
    "Google News - Top": "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
    "Google News - World": "https://news.google.com/rss/sections/CAAqJggKIiBDQkFTRWdvSUwyMHZNR3gxTXplc0VnSndlR291YW5Sb2tYUXNJRzlyeWxoekx6RTZDbUpIU2pnQVAB?hl=en-US&gl=US&ceid=US:en",
    "Google News - Tech": "https://news.google.com/rss/sections/CAAqKggKIiRDQkFTRlFvSUwyMHZNR1F3TkdZd0VnSlFiM0psYzNOMFgyOXVTZ2dBUAE?hl=en-US&gl=US&ceid=US:en",
    "Google News - Business": "https://news.google.com/rss/sections/CAAqKggKIiRDQkFTRlFvSUwyMHZNR3B6YlddeEVnSlFiM0psYzNOMFgyOXVTZ2dBUAE?hl=en-US&gl=US&ceid=US:en",
    "Google News - AI": "https://news.google.com/rss/search?q=Artificial+Intelligence+OR+Machine+Learning+OR+LLM&hl=en-US&gl=US&ceid=US:en",
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

# ── Connection Pool ───────────────────────────────────────────────────────────
# Neon free tier allows ~100 connections; keeping a small pool (1–5) is safe.

_pool: Optional[ThreadedConnectionPool] = None

def get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = ThreadedConnectionPool(1, 5, DATABASE_URL)
    return _pool

def get_db():
    """Borrow a connection from the pool (autocommit enabled)."""
    conn = get_pool().getconn()
    conn.autocommit = True
    return conn

def return_db(conn) -> None:
    """Return a borrowed connection back to the pool."""
    get_pool().putconn(conn)

# ── Database helpers ──────────────────────────────────────────────────────────

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
        # Schema migration
        cur.execute("ALTER TABLE articles ADD COLUMN IF NOT EXISTS entities TEXT")
        cur.execute("ALTER TABLE articles ADD COLUMN IF NOT EXISTS importance_score INTEGER DEFAULT 5")
        cur.execute("ALTER TABLE articles ADD COLUMN IF NOT EXISTS why_it_matters TEXT")
        cur.close()
    except Exception as e:
        print(f"DB init error: {e}")
        raise
    finally:
        return_db(conn)

def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()

def is_similar_title(t1: str, t2: str, threshold: float = 0.85) -> bool:
    return SequenceMatcher(None, t1.lower(), t2.lower()).ratio() >= threshold

def fetch_existing_titles(conn) -> list[str]:
    cur = db_execute(conn, "SELECT title FROM articles ORDER BY id DESC LIMIT 500")
    rows = cur.fetchall()
    cur.close()
    return [r["title"] for r in rows]

# ── Extractive fallbacks & Heuristics (Priority 2, 3, 4) ─────────────────────

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

def extractive_entities(title: str, content: str) -> dict:
    """Extract entities using lightweight keyword matching as a fallback."""
    combined = (title + " " + (content or "")).lower()
    
    companies_list = ["google", "microsoft", "openai", "meta", "apple", "amazon", "nvidia", "tesla", "anthropic", "netflix", "reuters", "cnbc", "bbc"]
    orgs_list = ["un", "who", "nato", "eu", "fbi", "cia", "nasa", "sec", "fda", "imf", "wto"]
    countries_list = ["us", "usa", "china", "uk", "ukraine", "russia", "india", "japan", "germany", "france", "taiwan", "canada"]
    
    companies = [c.capitalize() for c in companies_list if f" {c} " in f" {combined} " or combined.startswith(c)]
    organizations = [o.upper() for o in orgs_list if f" {o} " in f" {combined} " or combined.startswith(o)]
    countries = [cn.upper() if len(cn) <= 3 else cn.capitalize() for cn in countries_list if f" {cn} " in f" {combined} " or combined.startswith(cn)]
    
    # Extract capitalized words from title as potential people/locations
    words = re.findall(r'\b[A-Z][a-z]+\b', title)
    people = []
    locations = []
    
    common_stops = ["The", "A", "An", "In", "On", "At", "By", "For", "To", "With", "And", "Or", "But", "Reuters", "Bbc", "Npr", "Cnbc", "TechCrunch"]
    filtered_words = [w for w in words if w not in common_stops]
    
    if filtered_words:
        for w in filtered_words[:3]:
            if any(c in w.lower() for c in countries_list) or w.lower() in ["london", "tokyo", "beijing", "washington", "paris", "berlin", "moscow", "kyiv"]:
                locations.append(w)
            else:
                people.append(w)

    return {
        "people": list(set(people))[:3],
        "organizations": list(set(organizations))[:3],
        "companies": list(set(companies))[:3],
        "countries": list(set(countries))[:3],
        "locations": list(set(locations))[:3]
    }

def extractive_importance_score(title: str, content: str) -> int:
    """Determine article importance score (1-10) using keyword weight heuristics."""
    combined = (title + " " + (content or "")).lower()
    score = 5  # baseline
    
    high_impact = ["crisis", "war", "tariff", "election", "breakthrough", "acquisition", "billion", "merger", "unprecedented", "regulate", "antitrust"]
    ai_impact = ["llm", "gpt-4", "claude", "gemini", "openai", "nvidia", "artificial intelligence", "supercomputer"]
    
    if any(w in combined for w in high_impact):
        score += 2
    if any(w in combined for w in ai_impact):
        score += 2
        
    return min(max(score, 1), 10)

def extractive_why_it_matters(title: str, content: str, category: str) -> str:
    """Generate a lightweight explanation of why this news is significant."""
    return f"This updates critical events in {category}. It highlights ongoing shifts in industry standards, strategic developments, and regulatory frameworks."

# ── LLM helpers ──────────────────────────────────────────────────────────────

def llm_available() -> bool:
    return bool(OPENROUTER_API_KEY or GEMINI_API_KEY)

def _openrouter_generate(prompt: str, timeout: int) -> str:
    """Call OpenRouter — free models, OpenAI-compatible API."""
    r = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        json={
            "model": OPENROUTER_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        },
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/Nishikant-V/ai-bot",
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

def _gemini_generate(prompt: str, timeout: int) -> str:
    """Call Gemini 1.5 Flash — free tier via Google AI Studio key."""
    r = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        params={"key": GEMINI_API_KEY},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2},
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

def llm_generate(prompt: str, timeout: int = 60) -> str:
    """Try OpenRouter first, then Gemini. Raises if neither is configured."""
    if OPENROUTER_API_KEY:
        return _openrouter_generate(prompt, timeout)
    if GEMINI_API_KEY:
        return _gemini_generate(prompt, timeout)
    raise RuntimeError("No LLM provider configured — using extractive fallback")


def extract_json_array(text: str) -> list:
    start = text.find("[")
    end   = text.rfind("]") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except Exception:
            return []
    return []

def extract_json_object(text: str) -> dict:
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except Exception:
            return {}
    return {}

# ── LLM processing ───────────────────────────────────────────────────────────

def llm_process(articles: list[dict]) -> list[dict]:
    """Process articles with LLM; fall back to extractive methods if unavailable."""
    if not articles:
        return articles

    if not llm_available():
        print("No LLM provider configured — using extractive fallback")
        for a in articles:
            a["summary"]  = extractive_summary(a.get("content", ""))
            a["category"] = keyword_category(a["title"], a.get("content", ""))
            a["entities"] = extractive_entities(a["title"], a.get("content", ""))
            a["importance_score"] = extractive_importance_score(a["title"], a.get("content", ""))
            a["why_it_matters"] = extractive_why_it_matters(a["title"], a.get("content", ""), a["category"])
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
            f'- "category": exactly one of {CATEGORIES}\n'
            f'- "importance_score": integer between 1 and 10 based on global/economic/tech/political impact\n'
            f'- "why_it_matters": maximum 2 sentences explaining why this news is significant\n'
            f'- "entities": object with keys: "people" (list), "organizations" (list), "companies" (list), "countries" (list), "locations" (list)\n\n'
            f"Articles:\n{batch_text}\n\n"
            f"Return ONLY a valid JSON array. No explanation, no markdown."
        )
        try:
            raw        = llm_generate(prompt)
            results    = extract_json_array(raw)
            result_map = {r["id"]: r for r in results if "id" in r}
            for j, a in enumerate(batch):
                if j in result_map:
                    item = result_map[j]
                    a["summary"]  = item.get("summary", "") or extractive_summary(a.get("content", ""))
                    cat           = item.get("category", "")
                    a["category"] = cat if cat in CATEGORIES else keyword_category(a["title"], a.get("content", ""))
                    
                    # Store new intelligence parameters
                    try:
                        a["importance_score"] = int(item.get("importance_score", extractive_importance_score(a["title"], a.get("content", ""))))
                    except Exception:
                        a["importance_score"] = extractive_importance_score(a["title"], a.get("content", ""))
                    
                    a["why_it_matters"] = item.get("why_it_matters", "") or extractive_why_it_matters(a["title"], a.get("content", ""), a["category"])
                    
                    ent = item.get("entities", {})
                    if not isinstance(ent, dict):
                        ent = {}
                    a["entities"] = {
                        "people": ent.get("people", []) if isinstance(ent.get("people"), list) else [],
                        "organizations": ent.get("organizations", []) if isinstance(ent.get("organizations"), list) else [],
                        "companies": ent.get("companies", []) if isinstance(ent.get("companies"), list) else [],
                        "countries": ent.get("countries", []) if isinstance(ent.get("countries"), list) else [],
                        "locations": ent.get("locations", []) if isinstance(ent.get("locations"), list) else []
                    }
                else:
                    a["summary"]  = extractive_summary(a.get("content", ""))
                    a["category"] = keyword_category(a["title"], a.get("content", ""))
                    a["entities"] = extractive_entities(a["title"], a.get("content", ""))
                    a["importance_score"] = extractive_importance_score(a["title"], a.get("content", ""))
                    a["why_it_matters"] = extractive_why_it_matters(a["title"], a.get("content", ""), a["category"])
        except Exception as e:
            print(f"LLM batch error: {e} — falling back to extractive")
            for a in batch:
                a["summary"]  = extractive_summary(a.get("content", ""))
                a["category"] = keyword_category(a["title"], a.get("content", ""))
                a["entities"] = extractive_entities(a["title"], a.get("content", ""))
                a["importance_score"] = extractive_importance_score(a["title"], a.get("content", ""))
                a["why_it_matters"] = extractive_why_it_matters(a["title"], a.get("content", ""), a["category"])

    return articles

def format_article(r) -> dict:
    d = dict(r)
    # Parse entities string
    entities_raw = d.get("entities")
    if isinstance(entities_raw, str):
        try:
            d["entities"] = json.loads(entities_raw)
        except Exception:
            d["entities"] = {"people": [], "organizations": [], "companies": [], "countries": [], "locations": []}
    elif not isinstance(entities_raw, dict):
        d["entities"] = {"people": [], "organizations": [], "companies": [], "countries": [], "locations": []}
    
    # Defaults
    if d.get("importance_score") is None:
        d["importance_score"] = 5
    if d.get("why_it_matters") is None:
        d["why_it_matters"] = ""
        
    return d

# ── App setup ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    get_pool()   # initialise connection pool on startup
    init_db()
    yield
    if _pool:
        _pool.closeall()  # drain pool cleanly on shutdown

app = FastAPI(title="News Intelligence Agent", lifespan=lifespan)

# CORS: restrict to configured frontend origin in production; allow all in dev.
_origins = [FRONTEND_URL] if FRONTEND_URL else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """Liveness + DB connectivity check used by Render's healthCheckPath."""
    try:
        conn = get_db()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
        finally:
            return_db(conn)
        return {"status": "ok", "db": "ok"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB unavailable: {e}")


@app.post("/ingest")
def ingest(x_api_key: Optional[str] = Header(None)):
    """
    Pull RSS feeds, deduplicate, and LLM-process new articles.
    Protected by x-api-key header when INGEST_API_KEY env var is set.
    """
    if INGEST_API_KEY and x_api_key != INGEST_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing x-api-key header")

    conn = get_db()
    try:
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
                        "(url_hash,title,source,date,url,content,summary,category,ingested_at,entities,importance_score,why_it_matters) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (url_hash) DO NOTHING",
                        (a["url_hash"], a["title"], a["source"], a["date"],
                         a["url"], a["content"], a["summary"], a["category"], now,
                         json.dumps(a["entities"]), a["importance_score"], a["why_it_matters"]),
                    )
                    cur.close()
                except Exception as e:
                    print(f"Insert error: {e}")

        return {"ingested": len(new_articles), "sources": list(RSS_FEEDS.keys())}
    finally:
        return_db(conn)


@app.get("/articles")
def get_articles(page: int = 1, per_page: int = 20, category: Optional[str] = None):
    conn   = get_db()
    offset = (page - 1) * per_page
    try:
        if category:
            cur   = db_execute(conn,
                "SELECT * FROM articles WHERE category=%s ORDER BY importance_score DESC, ingested_at DESC LIMIT %s OFFSET %s",
                (category, per_page, offset))
            rows  = cur.fetchall(); cur.close()
            cur2  = db_execute(conn, "SELECT COUNT(*) AS count FROM articles WHERE category=%s", (category,))
            total = cur2.fetchone()["count"]; cur2.close()
        else:
            cur   = db_execute(conn,
                "SELECT * FROM articles ORDER BY importance_score DESC, ingested_at DESC LIMIT %s OFFSET %s",
                (per_page, offset))
            rows  = cur.fetchall(); cur.close()
            cur2  = db_execute(conn, "SELECT COUNT(*) AS count FROM articles")
            total = cur2.fetchone()["count"]; cur2.close()

        return {"articles": [format_article(r) for r in rows], "total": total, "page": page, "per_page": per_page}
    finally:
        return_db(conn)


@app.get("/search")
def search(q: str = Query(""), category: Optional[str] = None):
    conn = get_db()
    like = f"%{q}%"
    try:
        if category:
            cur = db_execute(conn,
                "SELECT * FROM articles "
                "WHERE (title ILIKE %s OR content ILIKE %s OR summary ILIKE %s) "
                "AND category=%s ORDER BY importance_score DESC, ingested_at DESC LIMIT 50",
                (like, like, like, category))
        else:
            cur = db_execute(conn,
                "SELECT * FROM articles "
                "WHERE title ILIKE %s OR content ILIKE %s OR summary ILIKE %s "
                "ORDER BY importance_score DESC, ingested_at DESC LIMIT 50",
                (like, like, like))

        rows = cur.fetchall()
        cur.close()
        return {"articles": [format_article(r) for r in rows], "query": q}
    finally:
        return_db(conn)


@app.get("/briefing")
def briefing():
    conn  = get_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        cur  = db_execute(conn,
            "SELECT * FROM articles WHERE ingested_at >= %s ORDER BY importance_score DESC, ingested_at DESC LIMIT 100",
            (today,))
        rows = cur.fetchall(); cur.close()

        if not rows:
            cur  = db_execute(conn, "SELECT * FROM articles ORDER BY importance_score DESC, ingested_at DESC LIMIT 50")
            rows = cur.fetchall(); cur.close()

        articles = [format_article(r) for r in rows]
    finally:
        return_db(conn)

    if not articles:
        return {
            "executive_summary": "No articles available.",
            "top_stories": [],
            "key_risks": [],
            "key_opportunities": [],
            "important_entities": {"companies": [], "people": [], "organizations": []}
        }

    top_stories = articles[:10]

    # Pre-calculate consolidated entities from top 10 articles
    companies = set()
    people = set()
    organizations = set()
    for a in top_stories:
        ent = a.get("entities") or {}
        if isinstance(ent, dict):
            for c in ent.get("companies", []): companies.add(c)
            for p in ent.get("people", []): people.add(p)
            for o in ent.get("organizations", []): organizations.add(o)

    consolidated_entities = {
        "companies": list(companies)[:10],
        "people": list(people)[:10],
        "organizations": list(organizations)[:10]
    }

    def extractive_briefing():
        cats = [a["category"] for a in articles if a.get("category")]
        from collections import Counter
        cat_counts = Counter(cats)
        dominant_cat = cat_counts.most_common(1)[0][0] if cat_counts else "General"
        
        summary = (
            f"Daily briefing compiling {len(articles)} analyzed news events, prioritized by global impact. "
            f"Significant updates in {dominant_cat} and related fields detail ongoing changes to business models and technical progress."
        )
        
        risks = [
            f"Increased regulatory and compliance checks in {dominant_cat} technologies.",
            "Potential economic turbulence affecting supply chains and investment funding.",
            "Competitive and security challenges from emerging technical deployments."
        ]
        
        opportunities = [
            "Increased efficiencies from adopting artificial intelligence and automation.",
            "Strategic partnerships and market growth in high-importance areas.",
            "New research discoveries enabling novel product developments."
        ]
        
        return {
            "executive_summary": summary,
            "top_stories": top_stories,
            "key_risks": risks,
            "key_opportunities": opportunities,
            "important_entities": consolidated_entities
        }

    if not llm_available():
        return extractive_briefing()

    stories_text = "\n".join(
        f"- {a['title']} ({a['source']}) [Importance: {a['importance_score']}]: {a['summary']}. Why it matters: {a['why_it_matters']}"
        for a in top_stories
    )
    prompt = (
        "You are a professional news editor. Based on these top stories, compile a daily briefing in JSON format.\n"
        "Provide:\n"
        "1. A 3-sentence executive_summary summarizing today's main news.\n"
        "2. A list of 3 key_risks identified from these stories.\n"
        "3. A list of 3 key_opportunities identified from these stories.\n"
        "4. A dictionary/object of consolidated important_entities found across these stories with keys: 'companies', 'people', 'organizations'. Avoid duplicate entries.\n\n"
        f"Stories:\n{stories_text}\n\n"
        'Return ONLY valid JSON with these keys: {"executive_summary": "...", "key_risks": ["..."], "key_opportunities": ["..."], "important_entities": {"companies": [...], "people": [...], "organizations": [...]}}'
    )
    try:
        raw    = llm_generate(prompt, timeout=90)
        result = extract_json_object(raw)
        
        final_entities = result.get("important_entities", {})
        if not final_entities or not isinstance(final_entities, dict):
            final_entities = consolidated_entities
        else:
            final_entities = {
                "companies": final_entities.get("companies", consolidated_entities["companies"]),
                "people": final_entities.get("people", consolidated_entities["people"]),
                "organizations": final_entities.get("organizations", consolidated_entities["organizations"])
            }

        return {
            "executive_summary": result.get("executive_summary", "") or extractive_briefing()["executive_summary"],
            "top_stories":       top_stories,
            "key_risks":         result.get("key_risks", []) or extractive_briefing()["key_risks"],
            "key_opportunities": result.get("key_opportunities", []) or extractive_briefing()["key_opportunities"],
            "important_entities": final_entities,
        }
    except Exception as e:
        print(f"Briefing LLM error: {e}")
        return extractive_briefing()


@app.get("/stats")
def stats():
    conn = get_db()
    try:
        cur   = db_execute(conn, "SELECT COUNT(*) AS count FROM articles")
        total = cur.fetchone()["count"]; cur.close()

        cur         = db_execute(conn,
            "SELECT category, COUNT(*) AS count FROM articles GROUP BY category ORDER BY count DESC")
        by_category = cur.fetchall(); cur.close()

        cur       = db_execute(conn,
            "SELECT source, COUNT(*) AS count FROM articles GROUP BY source ORDER BY count DESC")
        by_source = cur.fetchall(); cur.close()

        return {
            "total":       total,
            "by_category": [dict(r) for r in by_category],
            "by_source":   [dict(r) for r in by_source],
        }
    finally:
        return_db(conn)
