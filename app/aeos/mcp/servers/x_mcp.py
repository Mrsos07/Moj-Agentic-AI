"""
X Platform MCP server.

Wraps the EXISTING X automation subsystem (``app/services/x_bridge.py`` -> the
Flask ``app/x`` server driving Playwright) behind the MCP contract. This is the
key migration step: the legacy X code becomes just another pluggable MCP server,
with no business logic of its own.

Every call is scoped to an employee via its ``cookie_label`` (the account
identity used by the automation layer), enforcing per-employee isolation.
"""
from __future__ import annotations

import logging
from typing import List

from app.services import x_bridge

from ..base import IMCPServer, MCPRequest, MCPResponse

logger = logging.getLogger("aeos.mcp.x")


class XPlatformMCP(IMCPServer):
    name = "x"

    _TOOLS = [
        "post_tweet", "post_thread", "quote", "reply", "retweet", "like",
        "follow", "unfollow", "bookmark", "delete_tweet", "update_profile",
        "schedule_post", "cancel_scheduled_post", "list_scheduled_posts",
        "list_cookies", "stats",
    ]

    @property
    def tools(self) -> List[str]:
        return list(self._TOOLS)

    def health(self) -> bool:
        try:
            res = x_bridge.get_stats()
            return bool(res)
        except Exception:  # noqa: BLE001
            return False

    def call(self, request: MCPRequest) -> MCPResponse:
        tool = request.tool
        p = request.params
        # cookie_label identifies the employee's account within the automation layer
        label = request.context.get("cookie_label") or p.get("cookie_label", "")

        try:
            if tool == "post_tweet":
                res = x_bridge.post_tweet(
                    cookie_label=label,
                    text=p.get("text", ""),
                    media_url=p.get("media_url", ""),
                )
            elif tool == "post_thread":
                res = x_bridge.post_thread(
                    cookie_label=label,
                    texts=p.get("texts") or [],
                    media_url=p.get("media_url", ""),
                )
            elif tool == "quote":
                res = x_bridge.quote(
                    cookie_label=label,
                    tweet_url=p["tweet_url"],
                    text=p.get("text", ""),
                    media_url=p.get("media_url", ""),
                )
            elif tool == "schedule_post":
                res = x_bridge.schedule_post(
                    event_id=p["event_id"],
                    cookie_label=label,
                    content=p.get("content", ""),
                    run_at=p["run_at"],
                    media_url=p.get("media_url") or None,
                )
            elif tool == "cancel_scheduled_post":
                res = x_bridge.cancel_scheduled_post(p["event_id"])
            elif tool == "list_scheduled_posts":
                res = x_bridge.list_scheduled_posts(
                    status=p.get("status"), limit=int(p.get("limit", 100)),
                )
            elif tool == "reply":
                res = x_bridge.reply(
                    cookie_label=label,
                    tweet_url=p["tweet_url"],
                    reply_text=p.get("reply_text", ""),
                )
            elif tool == "retweet":
                res = x_bridge.repost(cookie_label=label, tweet_url=p["tweet_url"])
            elif tool == "like":
                res = x_bridge.like(cookie_label=label, tweet_url=p["tweet_url"])
            elif tool == "follow":
                res = x_bridge.follow(cookie_label=label, profile_url=p["profile_url"])
            elif tool == "unfollow":
                res = x_bridge.unfollow(cookie_label=label, profile_url=p["profile_url"])
            elif tool == "bookmark":
                res = x_bridge.bookmark(cookie_label=label, tweet_url=p["tweet_url"])
            elif tool == "delete_tweet":
                res = x_bridge.delete_tweet(cookie_label=label, tweet_id=p["tweet_id"])
            elif tool == "update_profile":
                res = x_bridge.update_profile(cookie_label=label, **{
                    k: p.get(k, "") for k in
                    ("name", "bio", "location", "website", "avatar_url", "banner_url")
                })
            elif tool == "list_cookies":
                res = x_bridge.list_cookies()
            elif tool == "stats":
                res = x_bridge.get_stats()
            else:
                return MCPResponse.fail(f"unknown x tool '{tool}'")

            if isinstance(res, dict) and res.get("success") is False:
                return MCPResponse.fail(res.get("error") or res.get("message") or "x call failed")
            return MCPResponse.ok(**(res if isinstance(res, dict) else {"result": res}))

        except KeyError as exc:
            return MCPResponse.fail(f"missing required param: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("X MCP tool failed: %s", tool)
            return MCPResponse.fail(str(exc))
