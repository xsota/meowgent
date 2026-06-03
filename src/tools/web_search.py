from typing import Dict
from serpapi import GoogleSearch

from config import load_config
from logging import getLogger
logger = getLogger(__name__)


def web_search(query: str) -> Dict[str, str]:
  """web search"""
  search = GoogleSearch({
    "engine": "yahoo",
    "p": query,
    "api_key": load_config().serp_api_key
  })
  result = search.get_dict()

  # "organic_results" key なければエラー
  if "organic_results" not in result or not result["organic_results"]:
    return {"error": "No organic results found for the query."}

  return result["organic_results"][:1] # 1件だけ返す


if __name__ == '__main__':
  web_search_result = web_search("meowgent")
  print(web_search_result)
