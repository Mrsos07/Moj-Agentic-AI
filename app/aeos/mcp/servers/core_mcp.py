"""اسم بديل: الخوادم الخمسة نُقلت إلى system/mcps/{content,memory,browser,news,search}/server.py."""
from app.core.system_loader import mcp_module as _m

ContentMCP = _m("content").ContentMCP
MemoryMCP = _m("memory").MemoryMCP
BrowserMCP = _m("browser").BrowserMCP
_news = _m("news")
NewsMCP = _news.NewsMCP
_GENERIC_IMAGE_HOSTS = _news._GENERIC_IMAGE_HOSTS
_is_generic_news_image = _news._is_generic_news_image
_search = _m("search")
SearchMCP = _search.SearchMCP
SEARCH_PROVIDER_KEYS = _search.SEARCH_PROVIDER_KEYS
search_provider = _search.search_provider
tavily_search = _search.tavily_search
