import os

from typing import Dict
from serpapi import GoogleSearch

from dotenv import load_dotenv
load_dotenv()

from logging import getLogger
logger = getLogger(__name__)


def web_search(query: str) -> Dict[str, str]:
  """web search"""
  search = GoogleSearch({
    "engine": "yahoo",
    "p": query,
    "api_key": os.environ.get('SERP_API_KEY')
  })
  result = search.get_dict()

  # "organic_results" key なければエラー
  if "organic_results" not in result or not result["organic_results"]:
    return {"error": "No organic results found for the query."}

  return result["organic_results"][:1] # 1件だけ返す


if __name__ == '__main__':
  web_search_result = web_search("meowgent")
  print(web_search_result)
