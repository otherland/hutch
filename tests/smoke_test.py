"""Smoke test: exercises all 16 tools end-to-end."""
import asyncio, os, sys, pathlib

os.environ.setdefault("HUTCH_DB_PATH", "/tmp/hutch_smoke_test.db")
pathlib.Path(os.environ["HUTCH_DB_PATH"]).unlink(missing_ok=True)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from hutch import db, server  # noqa: E402


async def main():
    await db.init_schema()

    # 1. ensure_project
    proj = await server.ensure_project("/tmp/smoke-test")
    assert proj["slug"], "ensure_project failed"
    print(f" 1/18 ensure_project          slug={proj['slug']}")

    # Idempotent
    proj2 = await server.ensure_project("/tmp/smoke-test")
    assert proj2["id"] == proj["id"], "ensure_project not idempotent"
    print(f" 2/18 ensure_project idempotent ✓")

    # 2. register_agent (auto name)
    a1 = await server.register_agent("/tmp/smoke-test", "claude-code", "opus-4.6")
    assert a1.get("name"), "register_agent failed"
    print(f" 3/18 register_agent (auto)    name={a1['name']}")

    # 2b. register_agent (explicit name)
    a2 = await server.register_agent("/tmp/smoke-test", "codex-cli", "o3", name="SilentOwl")
    assert a2["name"] == "SilentOwl"
    print(f" 4/18 register_agent (explicit) name={a2['name']}")

    # 2c. register_agent (bad name rejected)
    bad = await server.register_agent("/tmp/smoke-test", "x", "y", name="BackendWorker")
    assert "error" in bad
    print(f" 5/18 register_agent rejects   '{bad['error']}'")

    # 3. send_message
    msg = await server.send_message(
        "/tmp/smoke-test", a1["name"], [a2["name"]],
        "[bd-001] Auth refactor", "Starting work on login module.",
        thread_id="bd-001", importance="high", ack_required=True,
    )
    assert msg["message_id"]
    print(f" 6/18 send_message             id={msg['message_id']} thread={msg['thread_id']}")

    # 4. reply_message
    rpl = await server.reply_message("/tmp/smoke-test", msg["message_id"], a2["name"], "Acknowledged, I'll stay clear.")
    assert rpl["thread_id"] == "bd-001"
    print(f" 7/18 reply_message            thread preserved={rpl['thread_id']}")

    # 5. fetch_inbox
    inbox = await server.fetch_inbox("/tmp/smoke-test", a2["name"])
    assert inbox["count"] >= 1
    print(f" 8/18 fetch_inbox              {inbox['count']} message(s)")

    # 5b. fetch_inbox with filters
    urgent = await server.fetch_inbox("/tmp/smoke-test", a2["name"], urgent_only=True)
    assert urgent["count"] >= 1
    print(f" 9/18 fetch_inbox (urgent)     {urgent['count']} message(s)")

    # 6. acknowledge_message
    ack = await server.acknowledge_message("/tmp/smoke-test", a2["name"], msg["message_id"])
    assert ack["acknowledged"]
    print(f"10/18 acknowledge_message      acked_at={ack['acked_at']}")

    # 7. search_messages
    sr = await server.search_messages("/tmp/smoke-test", "login module")
    assert sr["count"] >= 1
    print(f"11/18 search_messages          {sr['count']} result(s)")

    # 8. file_reservation_paths
    res = await server.file_reservation_paths(
        "/tmp/smoke-test", a1["name"], ["src/auth/*.py", "src/auth/login.py"],
        ttl_seconds=3600, reason="bd-001",
    )
    assert len(res["granted"]) == 2
    print(f"12/18 file_reservation_paths   granted={len(res['granted'])}")

    # 8b. Conflict detection
    res2 = await server.file_reservation_paths(
        "/tmp/smoke-test", a2["name"], ["src/auth/login.py"], reason="bd-002",
    )
    assert len(res2["conflicts"]) >= 1
    print(f"13/18 conflict detected        held_by={res2['conflicts'][0]['held_by']}")

    # 11. check_reservations (read-only)
    ck = await server.check_reservations("/tmp/smoke-test", ["src/auth/*.py"])
    assert len(ck["reservations"]) >= 1
    print(f"14/18 check_reservations       {len(ck['reservations'])} active")

    # 9. release_file_reservations
    rel = await server.release_file_reservations("/tmp/smoke-test", a1["name"])
    assert rel["released"] >= 1
    print(f"15/18 release_file_reservations released={rel['released']}")

    # 10. list_agents
    la = await server.list_agents("/tmp/smoke-test")
    assert len(la["agents"]) == 2
    print(f"16/18 list_agents              {len(la['agents'])} agents")

    # 12. store_context
    sc = await server.store_context("/tmp/smoke-test", a1["name"], "api-schema-v1", "CREATE TABLE users (id INT, name TEXT);")
    assert sc["key"] == "api-schema-v1"
    print(f"17/18 store_context            {sc['size_bytes']} bytes")

    # 13. get_context
    gc = await server.get_context("/tmp/smoke-test", "api-schema-v1")
    assert "CREATE TABLE" in gc["value"]
    print(f"18/18 get_context              retrieved '{gc['key']}'")

    # Broadcast bonus
    bc = await server.send_message("/tmp/smoke-test", a1["name"], [], "Update", "All done.", broadcast=True)
    assert a2["name"] in bc["recipients"]
    print(f"  +   broadcast                to {len(bc['recipients'])} agent(s)")

    print(f"\n{'='*50}")
    print(f"ALL TESTS PASSED — 13 tools, 18 assertions")
    print(f"{'='*50}")


if __name__ == "__main__":
    asyncio.run(main())
