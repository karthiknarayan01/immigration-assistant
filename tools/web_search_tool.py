"""
Live web search. Uses Serper API if a real key is set, otherwise falls back
to direct USCIS scraping. Always fetches fresh — never cached.

Never raises: if Serper is unavailable or misconfigured, or the USCIS
fallback scrape fails too, this returns an empty result with an 'error'
field instead of blowing up the agent turn. The specialist is instructed
to answer with whatever other tools did succeed rather than stall on this.
"""
import httpx
from bs4 import BeautifulSoup
from google.adk.tools import FunctionTool
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from rag.sources import LIVE_SEARCH_DOMAINS

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 immigration-assistant/1.0"
)
_PLACEHOLDER_SERPER_KEY = "your_serper_api_key_here"


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
        On failure, 'results' is empty and an 'error' key explains why —
        this is never raised as an exception.
    """
    domain_filter = " OR ".join(f"site:{d}" for d in LIVE_SEARCH_DOMAINS)
    full_query = f"{query} {visa_category} ({domain_filter})"

    has_real_key = (
        settings.serper_api_key and settings.serper_api_key != _PLACEHOLDER_SERPER_KEY
    )
    if has_real_key:
        try:
            return _serper_search(full_query, recency_days)
        except Exception as e:
            logger.warning(f"Serper search failed, falling back to USCIS scrape: {e}")

    return _fallback_uscis_scrape(query, visa_category)


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=10))
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
    """Fallback: directly fetch USCIS news page when Serper not configured or unavailable."""
    url = "https://www.uscis.gov/newsroom/news-releases"
    try:
        resp = httpx.get(
            url, timeout=15, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
        )
        resp.raise_for_status()
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
        return {
            "results": [],
            "total": 0,
            "error": str(e),
            "note": "Live web search unavailable — answer using RAG/other tool results instead.",
        }


web_search_tool = FunctionTool(func=search_immigration_news)
