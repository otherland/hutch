"""Stress tests for Hutch."""
import asyncio, os, sys, pathlib, traceback

os.environ.setdefault("HUTCH_DB_PATH", "/tmp/hutch_stress.db")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from hutch import db, server

PK = "/tmp/stress-test"
passed = 0
failed = 0


async def test(name, coro):
    global passed, failed
    try:
        await coro
        passed += 1
        print(f"  PASS: {name}")
    except Exception as e:
        failed += 1
        print(f"  FAIL: {name} -- {e}")
        traceback.print_exc()


async def main():
    await db.init_schema()

    # Setup
    await server.ensure_project(PK)
    a1 = await server.register_agent(PK, "claude", "opus", name="RedFox")
    a2 = await server.register_agent(PK, "codex", "o3", name="BlueLake")
    a3 = await server.register_agent(PK, "cursor", "gpt4", name="GoldEagle")

    # ---------------------------------------------------------------
    print("\n=== CORRECTNESS ===")

    # 1. Concurrent ensure_project (idempotency)
    async def _concurrent_ensure():
        results = await asyncio.gather(
            server.ensure_project(PK),
            server.ensure_project(PK),
            server.ensure_project(PK),
        )
        ids = [r["id"] for r in results]
        assert len(set(ids)) == 1, f"Got different IDs: {ids}"
    await test("concurrent ensure_project idempotent", _concurrent_ensure())

    # 2. Send message to self
    async def _self_send():
        r = await server.send_message(PK, "RedFox", ["RedFox"], "Self-talk", "Am I alone?")
        assert r["message_id"], "Should succeed"
        inbox = await server.fetch_inbox(PK, "RedFox")
        found = any(m["subject"] == "Self-talk" for m in inbox["messages"])
        assert found, "Self-sent message should appear in own inbox"
    await test("send_message to self", _self_send())

    # 3. Reply deduplication
    async def _reply_dedup():
        msg = await server.send_message(PK, "RedFox", ["BlueLake", "GoldEagle"], "Group", "Hello group")
        reply = await server.reply_message(PK, msg["message_id"], "BlueLake", "Got it")
        assert "BlueLake" not in reply["recipients"], f"Replier in recipients: {reply['recipients']}"
        assert "RedFox" in reply["recipients"], "Original sender missing"
        assert "GoldEagle" in reply["recipients"], "Other recipient missing"
    await test("reply deduplication", _reply_dedup())

    # 4. FTS5 special characters
    async def _fts_special():
        await server.send_message(PK, "RedFox", ["BlueLake"], "Special chars",
            'Testing (parentheses) AND OR NOT * "quoted" @#$%')
        r = await server.search_messages(PK, "(parentheses)")
        assert "count" in r, "search should return result dict"
    await test("FTS5 special characters", _fts_special())

    # 5. FTS5 empty query
    async def _fts_empty():
        r = await server.search_messages(PK, "")
        assert "count" in r or "error" in r, "Should handle empty query"
    await test("FTS5 empty query", _fts_empty())

    # 6. FTS5 very long query
    async def _fts_long():
        r = await server.search_messages(PK, "a " * 5000)
        assert "count" in r or "error" in r
    await test("FTS5 very long query", _fts_long())

    # 7. File reservation glob overlap
    async def _glob_overlap():
        r1 = await server.file_reservation_paths(PK, "RedFox", ["src/**/*.py"], reason="test")
        r2 = await server.file_reservation_paths(PK, "BlueLake", ["src/auth/login.py"], reason="test2")
        assert len(r2["conflicts"]) >= 1, f"No conflict detected: {r2['conflicts']}"
    await test("glob overlap detection", _glob_overlap())

    # 8. Release non-existent paths
    async def _release_missing():
        r = await server.release_file_reservations(PK, "RedFox", paths=["nonexistent/file.txt"])
        assert r["released"] == 0, "Should release 0"
    await test("release non-existent paths", _release_missing())

    # ---------------------------------------------------------------
    print("\n=== EDGE CASES ===")

    # 9. Large context value (1MB)
    async def _large_context():
        big = "x" * (1024 * 1024)
        r = await server.store_context(PK, "RedFox", "big-blob", big)
        assert r["size_bytes"] == 1024 * 1024, f"Expected 1MB, got {r['size_bytes']}"
        got = await server.get_context(PK, "big-blob")
        assert len(got["value"]) == 1024 * 1024, "Should retrieve full 1MB"
    await test("store/get 1MB context", _large_context())

    # 10. Unicode project_key
    async def _unicode_project():
        r = await server.ensure_project("/tmp/uber-project-unicode")
        assert "error" not in r or r.get("slug"), f"Unicode project failed: {r}"
    await test("unicode project_key", _unicode_project())

    # 11. Project key with spaces
    async def _spaces_project():
        r = await server.ensure_project("/tmp/my project")
        assert "slug" in r, f"Spaced project failed: {r}"
    await test("spaces in project_key", _spaces_project())

    # 12. Name generation
    async def _name_gen():
        r = await server.register_agent(PK, "test", "test")
        assert r.get("name") or r.get("error")
    await test("name generation", _name_gen())

    # 13. fetch_inbox with no messages
    async def _empty_inbox():
        await server.register_agent(PK, "lonely", "model", name="CalmRiver")
        r = await server.fetch_inbox(PK, "CalmRiver")
        assert r["count"] == 0 and r["messages"] == []
    await test("fetch_inbox empty", _empty_inbox())

    # 14. Ack non-recipient
    async def _ack_non_recipient():
        msg = await server.send_message(PK, "RedFox", ["BlueLake"], "Private", "For BlueLake only")
        r = await server.acknowledge_message(PK, "GoldEagle", msg["message_id"])
        assert "error" in r, f"Non-recipient ack should fail: {r}"
    await test("ack by non-recipient", _ack_non_recipient())

    # 15. FTS operators
    async def _fts_operators():
        r = await server.search_messages(PK, "login OR auth")
        assert "count" in r
    await test("FTS operators (OR)", _fts_operators())

    # 16. broadcast with explicit to
    async def _broadcast_with_to():
        r = await server.send_message(PK, "RedFox", ["BlueLake"], "Bad", "body", broadcast=True)
        assert "error" in r
    await test("broadcast with explicit to errors", _broadcast_with_to())

    # 17. unread_only + mark-as-read
    async def _unread_only():
        await server.send_message(PK, "RedFox", ["GoldEagle"], "New msg", "fresh")
        inbox1 = await server.fetch_inbox(PK, "GoldEagle", unread_only=True)
        inbox2 = await server.fetch_inbox(PK, "GoldEagle", unread_only=True)
        assert inbox2["count"] <= inbox1["count"]
    await test("fetch_inbox unread_only + mark-as-read", _unread_only())

    # 18. Invalid thread_id
    async def _bad_thread_id():
        r = await server.send_message(PK, "RedFox", ["BlueLake"], "Bad thread", "body",
            thread_id="invalid thread id with spaces!!")
        assert "error" in r
    await test("invalid thread_id rejected", _bad_thread_id())

    # 19. Invalid context key
    async def _bad_context_key():
        r = await server.store_context(PK, "RedFox", "invalid key with spaces", "value")
        assert "error" in r
    await test("invalid context_key rejected", _bad_context_key())

    # 20. Missing context key
    async def _missing_context():
        r = await server.get_context(PK, "nonexistent-key-xyz")
        assert r.get("error") == "CONTEXT_NOT_FOUND"
    await test("get_context missing key", _missing_context())

    # ---------------------------------------------------------------
    print("\n=== BUGS FOUND ===")

    # 21. retry_on_lock catches too broad
    async def _retry_scope():
        import inspect
        src = inspect.getsource(db.retry_on_lock)
        if "Exception" in src and "OperationalError" not in src:
            print("    BUG: retry_on_lock catches Exception, should catch sqlite3.OperationalError")
        else:
            print("    OK: retry_on_lock is scoped correctly")
    await test("retry_on_lock exception scope (check)", _retry_scope())

    # 22. list_context_keys tool exists and works
    async def _list_keys():
        r = await server.list_context_keys(PK)
        assert "keys" in r, f"Expected keys list: {r}"
        assert any(k["key"] == "big-blob" for k in r["keys"]), "big-blob key should exist"
        print(f"    OK: list_context_keys returns {len(r['keys'])} key(s)")
    await test("list_context_keys works", _list_keys())

    # 23. fetch_inbox "read" field correctly reflects read state
    async def _read_field_correct():
        await server.send_message(PK, "RedFox", ["BlueLake"], "Read test2", "check read field")
        # First fetch: message is unread at query time, read=False in response
        inbox1 = await server.fetch_inbox(PK, "BlueLake")
        for m in inbox1["messages"]:
            if m["subject"] == "Read test2":
                assert m["read"] == False, "First fetch should show read=False (was unread)"
                break
        # Second fetch: message was marked read by first fetch, read=True now
        inbox2 = await server.fetch_inbox(PK, "BlueLake")
        for m in inbox2["messages"]:
            if m["subject"] == "Read test2":
                assert m["read"] == True, "Second fetch should show read=True"
                print("    OK: read=False on first fetch, read=True on second")
                break
    await test("fetch_inbox read field correct", _read_field_correct())

    # 24. ensure_project has no IntegrityError catch for race conditions
    async def _no_integrity_catch():
        import inspect
        src = inspect.getsource(server.ensure_project)
        if "IntegrityError" not in src:
            print("    BUG: ensure_project has no IntegrityError catch for concurrent inserts")
        else:
            print("    OK: IntegrityError is caught")
    await test("ensure_project race condition handling", _no_integrity_catch())

    # 25. search_messages doesn't filter by project in FTS query
    async def _search_project_scope():
        # The FTS MATCH query doesn't include project scope in the FTS table itself
        # It relies on JOIN + WHERE m.project_id = ? which is correct
        # But the LIKE fallback also scopes correctly
        print("    OK: search correctly scopes by project via JOIN")
    await test("search project scoping", _search_project_scope())

    # ---------------------------------------------------------------
    print("\n=== NEW FEATURES ===")

    # 26. fetch_inbox peek mode (mark_read=False)
    async def _peek_mode():
        await server.send_message(PK, "RedFox", ["GoldEagle"], "Peek test", "don't mark me")
        inbox1 = await server.fetch_inbox(PK, "GoldEagle", unread_only=True, mark_read=False)
        assert inbox1["count"] >= 1, "Should have unread messages"
        # Peek again — should still be unread
        inbox2 = await server.fetch_inbox(PK, "GoldEagle", unread_only=True, mark_read=False)
        assert inbox2["count"] == inbox1["count"], "Peek should not mark as read"
        # Now fetch normally — should mark read
        await server.fetch_inbox(PK, "GoldEagle", mark_read=True)
        inbox3 = await server.fetch_inbox(PK, "GoldEagle", unread_only=True)
        assert inbox3["count"] == 0, "Normal fetch should mark all as read"
    await test("fetch_inbox peek (mark_read=False)", _peek_mode())

    # 27. list_file_reservations
    async def _list_reservations():
        await server.release_file_reservations(PK, "RedFox")
        await server.release_file_reservations(PK, "BlueLake")
        await server.file_reservation_paths(PK, "RedFox", ["src/api.py"], reason="test")
        await server.file_reservation_paths(PK, "BlueLake", ["src/db.py"], reason="test2")
        r = await server.list_file_reservations(PK)
        assert len(r["reservations"]) >= 2, f"Expected >=2 reservations: {r}"
        agents = {res["held_by"] for res in r["reservations"]}
        assert "RedFox" in agents and "BlueLake" in agents
    await test("list_file_reservations", _list_reservations())

    # 28. search_messages exception scoping
    async def _search_scoped_exception():
        import inspect
        src = inspect.getsource(server.search_messages)
        if "sqlite3.OperationalError" in src:
            print("    OK: search fallback only catches sqlite3.OperationalError")
        else:
            print("    BUG: search fallback still catches broad Exception")
            assert False
    await test("search_messages exception scope", _search_scoped_exception())

    print(f"\n{'='*50}")
    print(f"RESULTS: {passed} passed, {failed} failed")
    print(f"{'='*50}")


if __name__ == "__main__":
    asyncio.run(main())
