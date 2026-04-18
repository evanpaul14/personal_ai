import requests
from config import config

WEB_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for current information using SearXNG.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "num_results": {"type": "integer", "default": 5, "description": "Number of results to return"},
            },
            "required": ["query"],
        },
    },
}

def web_search(query: str, num_results: int = 5) -> dict:
    params = {"q": query, "format": "json"}
    try:
        r = requests.get("http://evans-rasberry-pi.local:8888/search", params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return {"error": f"Web search unavailable: {e}. Let the user know search is offline.", "results": []}

    results = []
    for res in data.get("results", [])[:num_results]:
        results.append({
            "title": res.get("title", ""),
            "url": res.get("url", ""),
            "content": res.get("content", ""),
        })
    return {"results": results, "query": query}
