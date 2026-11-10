from logging import getLogger
from typing import Any

from serpapi import GoogleSearch

from config import load_config

logger = getLogger(__name__)

MAX_RESULTS = 5
MAX_SNIPPET_LENGTH = 300


def web_search(query: str, max_results: int = MAX_RESULTS) -> dict[str, Any]:
  """Search the web and return compact results (title, url, snippet)."""
  if not isinstance(max_results, int) or isinstance(max_results, bool):
    max_results = MAX_RESULTS
  max_results = min(max(max_results, 1), MAX_RESULTS)

  search = GoogleSearch({
    "engine": "yahoo",
    "p": query,
    "api_key": load_config().serp_api_key
  })
  result = search.get_dict()

  organic_results = result.get("organic_results")
  if not organic_results:
    return {"error": "No organic results found for the query."}

  return {
    "results": [
      {
        "title": item.get("title"),
        "url": item.get("link"),
        "snippet": str(item.get("snippet") or "")[:MAX_SNIPPET_LENGTH],
      }
      for item in organic_results[:max_results]
      if isinstance(item, dict)
    ],
  }


if __name__ == '__main__':
  web_search_result = web_search("meowgent")
  print(web_search_result)
