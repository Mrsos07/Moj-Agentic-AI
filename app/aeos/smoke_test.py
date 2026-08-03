"""
Standalone smoke test for the AEOS platform.

Run with:  python -m app.aeos.smoke_test

It initializes the DB, assembles the platform, creates a test employee whose X
capability is stubbed (so no real network/Playwright call happens), then runs a
single synchronous cognitive cycle and prints the report. This exercises the full
pipeline: repository -> runtime -> cognition phases -> planner -> decision ->
capability -> memory -> persist -> destroy.
"""
from __future__ import annotations

import json
import uuid

from app.db.database import init_db

from .bootstrap import AEOSPlatform
from .capabilities.base import CapabilityResult
from .employee.models import (
    EmployeeConfig, EmployeeStatus, Goal, Persona, SchedulerConfig,
)
from .mcp.base import IMCPServer, MCPResponse


class _StubXMCP(IMCPServer):
    """Replaces the real X MCP so the smoke test never touches the network."""

    name = "x"

    @property
    def tools(self):
        return ["post_tweet", "reply", "retweet", "like", "follow", "unfollow",
                "bookmark", "delete_tweet", "update_profile", "list_cookies", "stats"]

    def call(self, request):
        if request.tool == "post_tweet":
            return MCPResponse.ok(tweet_url="https://x.com/stub/status/123",
                                  text=request.params.get("text", ""))
        if request.tool == "stats":
            return MCPResponse.ok(followers=100, following=50)
        return MCPResponse.ok(result="stub")


def main() -> None:
    init_db()
    platform = AEOSPlatform(num_workers=1)

    # swap the real X MCP for the stub
    from .mcp.registry import MCPRegistry
    mcp: MCPRegistry = platform.container.resolve(MCPRegistry)
    mcp.register(_StubXMCP())

    cfg = EmployeeConfig(
        id=uuid.uuid4().hex,
        user_id=None,
        platform="x",
        account_username="smoke_bot",
        cookie_label="smoke_bot",
        status=EmployeeStatus.ACTIVE,
        persona=Persona(display_name="Smoke Bot", role="Tester", tone="casual",
                        language="en", instructions="Post something useful."),
        goals=[Goal(id="g1", description="Grow engagement", metric="engagement",
                    priority=1)],
        installed_skills=["daily_content_creator", "community_engager"],
        mcp_servers=["x", "content", "memory"],
        business_rules={"daily_post_target": 1, "max_actions_per_cycle": 2},
        scheduler=SchedulerConfig(interval_minutes=30),
    )
    saved = platform.repository.save(cfg)
    print(f"created employee {saved.id} (@{saved.account_username})")

    report = platform.run_cycle_now(saved.id, "scheduler")
    print("\n=== CYCLE REPORT ===")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))

    reloaded = platform.repository.get(saved.id)
    print("\ncycles_completed:", reloaded.runtime_state.cycles_completed)
    print("counters:", reloaded.runtime_state.counters)
    assert reloaded.runtime_state.cycles_completed == 1, "cycle did not persist"
    print("\nSMOKE TEST PASSED ✅")


if __name__ == "__main__":
    main()
