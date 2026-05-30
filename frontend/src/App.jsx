import { useState, useEffect, useCallback } from 'react'

// In dev: Vite proxies /api → localhost:8000 (vite.config.js)
// In prod: VITE_API_BASE = full Render backend URL (set in Vercel env vars)
const API = import.meta.env.VITE_API_BASE || '/api'
const CATS = ['All', 'World', 'Business', 'Technology', 'AI', 'Science', 'Health', 'Sports']
const CAT_COLORS = {
  World: '#6c63ff', Business: '#f59e0b', Technology: '#38bdf8',
  AI: '#a78bfa', Science: '#34d399', Health: '#f87171', Sports: '#fb923c',
}

function formatDate(d) {
  if (!d) return ''
  try {
    return new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  } catch { return d }
}

function useToast() {
  const [toast, setToast] = useState(null)
  const show = useCallback((msg, type = 'success') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 3500)
  }, [])
  return [toast, show]
}

function ArticleCard({ article }) {
  return (
    <div className="article-card">
      <div className="card-meta">
        <span className="source-tag">{article.source}</span>
        <span className="dot">·</span>
        <span className="date-tag">{formatDate(article.date || article.ingested_at)}</span>
        {article.category && <span className="cat-tag">{article.category}</span>}
      </div>
      <div className="article-title">{article.title}</div>
      {article.summary && <div className="article-summary">{article.summary}</div>}
      <a className="read-link" href={article.url} target="_blank" rel="noopener noreferrer">
        Read full article →
      </a>
    </div>
  )
}

function Dashboard({ stats }) {
  const [articles, setArticles] = useState([])
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [cat, setCat] = useState('All')
  const [q, setQ] = useState('')
  const [searchQ, setSearchQ] = useState('')
  const PER = 12

  const load = useCallback(() => {
    setLoading(true)
    const catParam = cat !== 'All' ? `&category=${cat}` : ''
    const url = searchQ
      ? `${API}/search?q=${encodeURIComponent(searchQ)}${catParam}`
      : `${API}/articles?page=${page}&per_page=${PER}${catParam}`
    fetch(url)
      .then(r => r.json())
      .then(d => { setArticles(d.articles || []); setTotal(d.total || (d.articles || []).length) })
      .catch(() => setArticles([]))
      .finally(() => setLoading(false))
  }, [page, cat, searchQ])

  useEffect(() => { setPage(1) }, [cat, searchQ])
  useEffect(() => { load() }, [load])

  const totalPages = Math.ceil(total / PER)

  const doSearch = () => setSearchQ(q)
  const clearSearch = () => { setQ(''); setSearchQ('') }

  return (
    <div>
      <div className="stats-bar">
        <div className="stat-card">
          <div className="stat-value">{stats.total || 0}</div>
          <div className="stat-label">Total Articles</div>
        </div>
        {(stats.by_source || []).slice(0, 3).map((s, i) => (
          <div className="stat-card" key={i}>
            <div className="stat-value">{s.count}</div>
            <div className="stat-label">{s.source}</div>
          </div>
        ))}
      </div>

      <div className="search-bar">
        <input
          className="search-input"
          placeholder="Search articles…"
          value={q}
          onChange={e => setQ(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && doSearch()}
        />
        <button className="search-btn" onClick={doSearch}>Search</button>
        {searchQ && <button className="clear-btn" onClick={clearSearch}>Clear</button>}
      </div>

      <div className="category-pills">
        {CATS.map(c => (
          <div key={c} className={`pill ${cat === c ? 'active' : ''}`} onClick={() => setCat(c)}>{c}</div>
        ))}
      </div>

      <div className="section-header">
        <div>
          <div className="section-title">
            {searchQ ? `Results for "${searchQ}"` : cat !== 'All' ? `${cat} News` : 'Latest Articles'}
          </div>
          <div className="section-sub">{total} articles found</div>
        </div>
      </div>

      {loading
        ? <div className="load-center"><div className="spinner" /></div>
        : articles.length === 0
          ? (
            <div className="empty">
              <div className="empty-icon">📰</div>
              <h3>No articles yet</h3>
              <p>Click "⚡ Fetch News" in the sidebar to ingest articles.</p>
            </div>
          )
          : (
            <>
              <div className="articles-grid">
                {articles.map(a => <ArticleCard key={a.id} article={a} />)}
              </div>
              {!searchQ && totalPages > 1 && (
                <div className="pagination">
                  <button className="page-btn" disabled={page === 1} onClick={() => setPage(p => p - 1)}>← Prev</button>
                  {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => i + 1).map(p => (
                    <button key={p} className={`page-btn ${page === p ? 'active' : ''}`} onClick={() => setPage(p)}>{p}</button>
                  ))}
                  <button className="page-btn" disabled={page === totalPages} onClick={() => setPage(p => p + 1)}>Next →</button>
                </div>
              )}
            </>
          )
      }
    </div>
  )
}

function Briefing() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`${API}/briefing`)
      .then(r => r.json())
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="load-center"><div className="spinner" /></div>
  if (!data) return <div className="empty"><div className="empty-icon">⚠️</div><h3>Failed to load briefing</h3></div>

  return (
    <div className="briefing-wrap">
      <div className="section-header">
        <div>
          <div className="section-title">Daily Briefing</div>
          <div className="section-sub">
            {new Date().toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}
          </div>
        </div>
      </div>

      {data.executive_summary && (
        <div className="exec-summary">
          <h2>Executive Summary</h2>
          <p>{data.executive_summary}</p>
        </div>
      )}

      {data.trends?.length > 0 && (
        <div className="trends-section">
          <h2>🔥 Top Trends</h2>
          <div>
            {data.trends.map((t, i) => (
              <span key={i} className="trend-pill">
                <span className="trend-num">{i + 1}</span>
                {t}
              </span>
            ))}
          </div>
        </div>
      )}

      <div>
        <h2 style={{ fontSize: '17px', fontWeight: 700, marginBottom: '16px' }}>📋 Top Stories</h2>
        {(data.top_stories || []).map((s, i) => (
          <a key={s.id} href={s.url} target="_blank" rel="noopener noreferrer" className="top-story">
            <div className="story-num">#{i + 1}</div>
            <div className="story-info">
              <h3>{s.title}</h3>
              <p>{s.summary || s.content?.slice(0, 120)}</p>
              <div style={{ display: 'flex', gap: '8px', marginTop: '6px' }}>
                <span style={{ fontSize: '11px', color: 'var(--accent3)', fontWeight: 700 }}>{s.source}</span>
                {s.category && <span style={{ fontSize: '11px', color: 'var(--muted)' }}>{s.category}</span>}
              </div>
            </div>
          </a>
        ))}
      </div>
    </div>
  )
}

function Categories({ stats }) {
  const [articles, setArticles] = useState([])
  const [activeCat, setActiveCat] = useState(null)
  const [loading, setLoading] = useState(false)

  const loadCat = (cat) => {
    setActiveCat(cat)
    setLoading(true)
    fetch(`${API}/articles?category=${cat}&per_page=6`)
      .then(r => r.json())
      .then(d => setArticles(d.articles || []))
      .finally(() => setLoading(false))
  }

  return (
    <div>
      <div className="section-header">
        <div className="section-title">Categories</div>
      </div>
      <div className="cat-grid">
        {(stats.by_category || []).map(c => (
          <div
            key={c.category}
            className="stat-card"
            style={{ borderColor: activeCat === c.category ? (CAT_COLORS[c.category] || 'var(--accent)') : undefined }}
            onClick={() => loadCat(c.category)}
          >
            <div className="stat-value" style={{ fontSize: '24px', color: CAT_COLORS[c.category] || 'var(--accent)' }}>
              {c.count}
            </div>
            <div className="stat-label">{c.category}</div>
          </div>
        ))}
      </div>
      {activeCat && (
        <>
          <div className="section-title" style={{ marginBottom: '16px' }}>{activeCat} Articles</div>
          {loading
            ? <div className="spinner" />
            : <div className="articles-grid">{articles.map(a => <ArticleCard key={a.id} article={a} />)}</div>
          }
        </>
      )}
    </div>
  )
}

export default function App() {
  const [view, setView] = useState('dashboard')
  const [ingesting, setIngesting] = useState(false)
  const [stats, setStats] = useState({ total: 0, by_category: [], by_source: [] })
  const [toast, showToast] = useToast()

  const loadStats = useCallback(() => {
    fetch(`${API}/stats`).then(r => r.json()).then(setStats).catch(() => {})
  }, [])

  useEffect(() => { loadStats() }, [loadStats])

  const ingest = async () => {
    setIngesting(true)
    try {
      const r = await fetch(`${API}/ingest`, { method: 'POST' })
      if (r.status === 401) {
        showToast('Ingestion requires admin access — use the API directly', 'error')
        return
      }
      const d = await r.json()
      showToast(`✓ Ingested ${d.ingested} new articles`, 'success')
      loadStats()
    } catch {
      showToast('Failed to connect to backend', 'error')
    } finally {
      setIngesting(false)
    }
  }

  const navItems = [
    { id: 'dashboard', icon: '📰', label: 'Dashboard' },
    { id: 'briefing',  icon: '📋', label: 'Daily Briefing' },
    { id: 'categories', icon: '🗂️', label: 'Categories' },
  ]

  return (
    <>
      <div className="sidebar">
        <div className="logo">
          <h1>NewsAI</h1>
          <span>Intelligence Agent</span>
        </div>
        {navItems.map(n => (
          <div key={n.id} className={`nav-item ${view === n.id ? 'active' : ''}`} onClick={() => setView(n.id)}>
            <span className="nav-icon">{n.icon}</span>
            <span>{n.label}</span>
          </div>
        ))}
        <div className="sidebar-footer">
          <button className={`ingest-btn${ingesting ? ' loading' : ''}`} onClick={ingest} disabled={ingesting}>
            {ingesting ? '⏳ Fetching…' : '⚡ Fetch News'}
          </button>
          <div style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '10px', textAlign: 'center' }}>
            {stats.total} articles stored
          </div>
        </div>
      </div>

      <div className="main">
        {view === 'dashboard'   && <Dashboard stats={stats} />}
        {view === 'briefing'    && <Briefing />}
        {view === 'categories'  && <Categories stats={stats} />}
      </div>

      {toast && <div className={`toast ${toast.type}`}>{toast.msg}</div>}
    </>
  )
}
