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
        {article.importance_score && (
          <span className="importance-badge" style={{ marginLeft: '6px' }}>
            ⭐ {article.importance_score}/10
          </span>
        )}
        {article.category && <span className="cat-tag">{article.category}</span>}
      </div>
      <div className="article-title">{article.title}</div>
      {article.summary && <div className="article-summary">{article.summary}</div>}
      
      {article.why_it_matters && (
        <div className="why-it-matters">
          <strong>Why it matters:</strong> {article.why_it_matters}
        </div>
      )}

      {article.entities && Object.values(article.entities).some(list => list?.length > 0) && (
        <div className="entity-tags">
          {Object.entries(article.entities).flatMap(([type, list]) => 
            (list || []).map(ent => (
              <span key={ent} className="entity-tag" title={type}>
                {ent}
              </span>
            ))
          ).slice(0, 5)}
        </div>
      )}

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

      {data.key_risks?.length > 0 && (
        <div className="briefing-section">
          <h2>⚠️ Key Risks</h2>
          <ul>
            {data.key_risks.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </div>
      )}

      {data.key_opportunities?.length > 0 && (
        <div className="briefing-section">
          <h2>🚀 Key Opportunities</h2>
          <ul>
            {data.key_opportunities.map((o, i) => <li key={i}>{o}</li>)}
          </ul>
        </div>
      )}

      {data.important_entities && (
        <div className="briefing-section">
          <h2>🏷️ Important Entities</h2>
          <div className="entity-groups">
            {data.important_entities.companies?.length > 0 && (
              <div className="entity-group">
                <h3>Companies</h3>
                <div className="tags">
                  {data.important_entities.companies.map(c => <span key={c} className="entity-tag">{c}</span>)}
                </div>
              </div>
            )}
            {data.important_entities.people?.length > 0 && (
              <div className="entity-group">
                <h3>People</h3>
                <div className="tags">
                  {data.important_entities.people.map(p => <span key={p} className="entity-tag">{p}</span>)}
                </div>
              </div>
            )}
            {data.important_entities.organizations?.length > 0 && (
              <div className="entity-group">
                <h3>Organizations</h3>
                <div className="tags">
                  {data.important_entities.organizations.map(o => <span key={o} className="entity-tag">{o}</span>)}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      <div>
        <h2 style={{ fontSize: '17px', fontWeight: 700, marginBottom: '16px' }}>📋 Top Stories</h2>
        {(data.top_stories || []).map((s, i) => (
          <div key={s.id} className="top-story-card">
            <div className="story-header">
              <span className="story-num">#{i + 1}</span>
              <a href={s.url} target="_blank" rel="noopener noreferrer" className="story-title-link">
                <h3>{s.title}</h3>
              </a>
              {s.importance_score && (
                <span className="importance-badge">⭐ {s.importance_score}/10</span>
              )}
            </div>
            <div className="story-body">
              <p>{s.summary || s.content?.slice(0, 120)}</p>
              {s.why_it_matters && (
                <div className="why-it-matters">
                  <strong>Why it matters:</strong> {s.why_it_matters}
                </div>
              )}
              {s.entities && Object.values(s.entities).some(list => list?.length > 0) && (
                <div className="entity-tags">
                  {Object.entries(s.entities).flatMap(([type, list]) => 
                    (list || []).map(ent => (
                      <span key={ent} className="entity-tag" title={type}>
                        {ent}
                      </span>
                    ))
                  ).slice(0, 8)}
                </div>
              )}
              <div style={{ display: 'flex', gap: '8px', marginTop: '12px', fontSize: '11px', color: 'var(--muted)' }}>
                <span style={{ color: 'var(--accent3)', fontWeight: 700 }}>{s.source}</span>
                <span>·</span>
                <span>{s.category}</span>
              </div>
            </div>
          </div>
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
        showToast('Admin-only: send POST /ingest with x-api-key header to ingest news.', 'error')
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
          <div style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '8px', textAlign: 'center' }}>
            🔒 Admin operation — requires API key
          </div>
          <div style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '4px', textAlign: 'center' }}>
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
