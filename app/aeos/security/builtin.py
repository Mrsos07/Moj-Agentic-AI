"""Argument contracts for in-process MCP tools shipped with the application."""
import re
from urllib.parse import urlsplit

from .data import validate

# An asterisk marks a required property. Unknown tools or fields fail closed.
FIELDS = {
    "browser": {"read_page": "url*"},
    "news": {"fetch_feed": "feed_url* limit"},
    "search": {"web_search": "query limit"},
    "memory": {"store": "layer content* key importance", "recall": "layer query limit"},
    "content": {"generate": "system prompt* temperature max_tokens",
                "summarize": "system text*", "rewrite": "system text* tone"},
    "image": {"generate_image": "prompt* size aspect_ratio quality model style"},
    "x": {"post_tweet": "text* media_url", "post_thread": "texts* media_url",
          "quote": "tweet_url* text* media_url", "reply": "tweet_url* reply_text*",
          "schedule_post": "event_id* content* run_at* media_url", "cancel_scheduled_post": "event_id*",
          "update_profile": "name bio location website avatar_url banner_url",
          "delete_tweet": "tweet_id* expected_handle",
          **dict.fromkeys(("like", "retweet", "bookmark", "unbookmark", "unretweet", "unlike"), "tweet_url*"),
          "follow": "profile_url*", "unfollow": "profile_url*"},
    "playwright_x": {**dict.fromkeys(("read_timeline", "read_trends", "read_notifications", "read_top_posts"),
                                   "limit headless list_type"),
                     "read_account_profile": "limit headless handle lock_timeout",
                     "read_my_posts": "limit headless handle",
                     "read_user_profile": "limit headless handle* lock_timeout tab_budget_seconds replies_limit "
                                          "replies_budget_seconds history_windows history_budget_seconds "
                                          "history_per_window include_replies",
                     "search_tweets": "query* limit mode headless"},
    "pixabay": {"get_image_by_id": "id*", "get_video_by_id": "id*",
                **dict.fromkeys(("search_images", "search_videos"), "q query lang video_type image_type "
                                "orientation category min_width min_height colors editors_choice safesearch order page per_page")},
}

def check(server, tool, args):
    fields = FIELDS.get(server, {}).get(tool)
    if fields is None:
        raise ValueError("unregistered builtin tool")
    props, required = {}, []
    for field in fields.split():
        key = field.rstrip("*")
        if field.endswith("*"):
            required.append(key)
        spec = {"type": "string", "maxLength": 48000}
        if key in {"limit", "replies_limit", "history_per_window", "importance", "per_page", "page",
                   "max_tokens", "min_width", "min_height"}:
            spec = {"type": "integer", "minimum": 0, "maximum": 8192 if key in {"max_tokens", "min_width", "min_height"} else 500}
        elif key in {"temperature", "lock_timeout", "tab_budget_seconds", "replies_budget_seconds", "history_budget_seconds"}:
            spec = {"type": "number", "minimum": 0, "maximum": 2 if key == "temperature" else 180}
        elif key in {"headless", "include_replies"}:
            spec = {"type": "boolean"}
        elif key in {"safesearch", "editors_choice", "id", "tweet_id", "media_url", "key"}:
            spec = {"type": {"safesearch": ["boolean", "string"], "editors_choice": ["boolean", "string"],
                             "id": ["integer", "string"], "tweet_id": ["integer", "string"],
                             "media_url": ["string", "null"], "key": ["string", "null"]}[key]}
        elif key == "texts":
            spec = {"type": "array", "minItems": 1, "maxItems": 32, "items": {"type": "string", "maxLength": 8192}}
        elif key == "history_windows":
            spec = {"type": "array", "maxItems": 8, "items": {"type": "array", "minItems": 2, "maxItems": 2,
                    "items": {"type": "string", "maxLength": 10}}}
        props[key] = spec
    validate(args, {"type": "object", "properties": props, "required": required})
    for key in ("handle", "expected_handle"):
        if args.get(key) and not re.fullmatch(r"@?[a-zA-Z0-9_]{1,15}", args[key]):
            raise ValueError("invalid account handle")
    for key in ("tweet_url", "profile_url"):
        if key in args:
            url = urlsplit(args[key])
            route = r"/(?:[A-Za-z0-9_]{1,15}|i/web)/status/[0-9]{1,24}/?" if key == "tweet_url" else r"/[A-Za-z0-9_]{1,15}/?"
            if (url.scheme != "https" or url.netloc.lower() not in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
                    or not re.fullmatch(route, url.path) or url.fragment):
                raise ValueError("invalid X destination")
