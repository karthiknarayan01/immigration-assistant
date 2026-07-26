"""
Fetches current USCIS processing times live — always fresh, never from RAG.
"""
import httpx
from google.adk.tools import FunctionTool
from loguru import logger

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 immigration-assistant/1.0"
)

FORM_MAP = {
    "H1B": ["I-129"],
    "F1": ["I-539"],
    "B1B2": ["I-539"],
    "L1": ["I-129"],
    "EB1": ["I-140", "I-485"],
    "EB2": ["I-140", "I-485"],
    "EB3": ["I-140", "I-485"],
}


def get_processing_times(visa_category: str) -> dict:
    """
    Fetch current USCIS processing times for a visa category.
    Always live data from uscis.gov — never cached.

    Args:
        visa_category: One of H1B, F1, B1B2, L1, EB1, EB2, EB3.

    Returns:
        dict with processing time ranges by form and service center.
    """
    forms = FORM_MAP.get(visa_category.upper(), [])
    results = {}
    for form in forms:
        try:
            url = f"https://egov.uscis.gov/processing-times/api/processingtime/IV/{form}"
            resp = httpx.get(
                url, timeout=15, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
            )
            if resp.status_code == 200:
                results[form] = {
                    "form": form,
                    "data": resp.json(),
                    "source": "https://egov.uscis.gov/processing-times/",
                }
            else:
                results[form] = {
                    "form": form,
                    "error": f"USCIS returned {resp.status_code}",
                    "fallback_url": "https://egov.uscis.gov/processing-times/",
                }
        except Exception as e:
            logger.error(f"Processing times failed for {form}: {e}")
            results[form] = {
                "form": form,
                "error": str(e),
                "fallback_url": "https://egov.uscis.gov/processing-times/",
            }
    return {
        "visa_category": visa_category,
        "forms": results,
        "disclaimer": "Processing times change frequently. Always verify at egov.uscis.gov/processing-times/",
    }


processing_times_tool = FunctionTool(func=get_processing_times)
