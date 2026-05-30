import sys, os
sys.path.insert(0, 'backend')
os.environ['DATABASE_URL'] = 'postgresql://dummy'

import importlib.util
spec = importlib.util.spec_from_file_location('main', 'backend/main.py')
mod  = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

results = []

def check(name, fn):
    try:
        fn()
        print(f"[PASS] {name}")
        results.append(True)
    except Exception as e:
        print(f"[FAIL] {name} -- {e}")
        results.append(False)

# Test 1: Module loads
check("T1: Module imports cleanly", lambda: None)

# Test 2: llm_available() = False with no keys
def t2():
    os.environ.pop('OPENROUTER_API_KEY', None)
    os.environ.pop('GEMINI_API_KEY', None)
    assert mod.llm_available() == False, "Expected False"
check("T2: llm_available()=False when no keys set", t2)

# Test 3: extractive_summary
def t3():
    s = mod.extractive_summary('Breaking news from London. The Prime Minister announced new policies today.')
    assert len(s) > 10
    print(f"       -> {s[:70]}")
check("T3: extractive_summary works", t3)

# Test 4: keyword_category
def t4():
    c = mod.keyword_category('AI startup raises 100M for machine learning platform', '')
    assert c in mod.CATEGORIES, f"Unknown category: {c}"
    print(f"       -> category={c}")
check("T4: keyword_category correct", t4)

# Test 5: url_hash deterministic SHA-256
def t5():
    h1 = mod.url_hash('https://example.com/article/1')
    h2 = mod.url_hash('https://example.com/article/1')
    assert h1 == h2 and len(h1) == 64
check("T5: url_hash deterministic (SHA-256 64-char)", t5)

# Test 6: is_similar_title
def t6():
    assert mod.is_similar_title('Trump signs trade deal with China', 'Trump signs trade deal with china') == True
    assert mod.is_similar_title('Apple releases iPhone 17', 'Google launches Pixel 9') == False
check("T6: is_similar_title catches near-dupes, rejects different titles", t6)

# Test 7: llm_process uses extractive fallback when no key
def t7():
    articles = [{'title': 'Tech startup raises funding', 'content': 'A new AI startup raised 50 million dollars for machine learning research.', 'summary': '', 'category': ''}]
    result = mod.llm_process(articles)
    assert result[0]['summary'] != '', "Empty summary"
    assert result[0]['category'] in mod.CATEGORIES, "Bad category"
    print(f"       -> summary={result[0]['summary'][:60]}")
    print(f"       -> category={result[0]['category']}")
check("T7: llm_process extractive fallback (no API key)", t7)

# Test 8: All RSS feeds use HTTPS
def t8():
    for name, url in mod.RSS_FEEDS.items():
        assert url.startswith('https://'), f"{name} not HTTPS: {url}"
    print(f"       -> {len(mod.RSS_FEEDS)} feeds all use HTTPS")
check("T8: All RSS feeds use HTTPS", t8)

# Test 9: extract_json_array / extract_json_object
def t9():
    arr = mod.extract_json_array('Some text [{"id": 0, "summary": "test", "category": "AI"}] end')
    assert arr == [{"id": 0, "summary": "test", "category": "AI"}]
    obj = mod.extract_json_object('{"executive_summary": "hello", "trends": ["a","b"]}')
    assert obj['executive_summary'] == 'hello'
check("T9: JSON extraction helpers work", t9)

# Test 10: API routes are registered
def t10():
    routes = [r.path for r in mod.app.routes]
    for expected in ['/health', '/ingest', '/articles', '/search', '/briefing', '/stats']:
        assert expected in routes, f"Missing route: {expected}"
    print(f"       -> routes: {[r for r in routes if not r.startswith('/openapi')]}")
check("T10: All 6 API routes registered", t10)

print()
print(f"Result: {sum(results)}/{len(results)} passed")
