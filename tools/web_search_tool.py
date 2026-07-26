"""
Live web search. Uses Serper API if key is set, otherwise falls back
to direct USCIS scraping. Always fetches fresh — never cached.
"""
import httpx
from bs4 import BeautifulSoup
from google.adk.tools import FunctionTool
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from rag.sources import LIVE_SEARCH_DOMAINS


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def search_immigration_news(
    query: str,
    visa_category: str,
    recency_days: int = 90,
) -> dict:
    """
    Search immigration news and updates from authoritative sources.
    Use for current policy changes, USCIS memos, processing time updates,
    or anything that may have changed in the last 90 days.

    Args:
        query: What to search for.
        visa_category: One of H1B, F1, B1B2, L1, EB1, EB2, EB3.
        recency_days: How recent results should be (default 90 days).

    Returns:
        dict with 'results' list containing title, snippet, url, date.
    """
    domain_filter = " OR ".join(f"site:{d}" for d in LIVE_SEARCH_DOMAINS)
    full_query = f"{query} {visa_category} ({domain_filter})"

    if settings.serper_api_key:
        return _serper_search(full_query, recency_days)
    else:
        return _fallback_uscis_scrape(query, visa_category)


def _serper_search(query: str, recency_days: int) -> dict:
    headers = {"X-API-KEY": settings.serper_api_key, "Content-Type": "application/json"}
    payload = {"q": query, "num": 8, "tbs": f"qdr:{'m3' if recency_days <= 90 else 'y1'}"}
    resp = httpx.post("https://google.serper.dev/search", json=payload, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    results = []
    for item in data.get("organic", [])[:8]:
        results.append(
            {
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "url": item.get("link", ""),
                "date": item.get("date", "unknown"),
                "source_type": "live_search",
            }
        )
    return {"results": results, "total": len(results)}


def _fallback_uscis_scrape(query: str, visa_category: str) -> dict:
    """Fallback: directly fetch USCIS news page when Serper not configured."""
    url = "https://www.uscis.gov/newsroom/news-releases"
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True)
        soup = BeautifulSoup(resp.text, "lxml")
        items = soup.select("article.news-item")[:5]
        results = []
        for item in items:
            title_el = item.select_one("h3 a")
            if title_el:
                results.append(
                    {
                        "title": title_el.get_text(strip=True),
                        "snippet": "",
                        "url": "https://www.uscis.gov" + title_el.get("href", ""),
                        "date": "recent",
                        "source_type": "official",
                    }
                )
        return {"results": results, "total": len(results)}
    except Exception as e:
        logger.error(f"Fallback scrape failed: {e}")
        return {"results": [], "total": 0, "error": str(e)}


web_search_tool = FunctionTool(func=search_immigration_news)
