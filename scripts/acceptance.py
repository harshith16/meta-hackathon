"""Acceptance test: real DB, real code paths, no placeholders.

Seed travel limit 8000, supersede to 12000 from a chat message, then
prove: (a) current returns 12000 only; (b) history returns both with
the chat message as evidence; (c) a superseded-only query produces the
conflict warning; (d) employee vs manager differ only where role-scoped.
Plus: keyword path with vectors disabled, vector assertions when Ollama
is up, as-of history, media ingest, explain fields, dedup, the
supersede transaction, and HTTP surfaces.

Run: PYTHONPATH=. python scripts/acceptance.py
Exit code 0 iff every check passes. Cleans its own company rows first
so reruns are deterministic.
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import embed
from app.answer import answer_question
from app.db import get_conn
from app.retrieve import get_timeline, search, search_as_of
from app.store import ingest_chat_message, ingest_media, link_media_to_fact, write_fact

COMPANY = "acceptance-co"
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


def cleanup(conn):
    conn.execute("DELETE FROM edges WHERE company_id = %s", (COMPANY,))
    conn.execute(
        "DELETE FROM memory_sources WHERE memory_id IN "
        "(SELECT id FROM memories WHERE company_id = %s)",
        (COMPANY,),
    )
    conn.execute("DELETE FROM media WHERE company_id = %s", (COMPANY,))
    conn.execute("DELETE FROM chunks WHERE company_id = %s", (COMPANY,))
    conn.execute("DELETE FROM documents WHERE company_id = %s", (COMPANY,))
    conn.execute("DELETE FROM memories WHERE company_id = %s", (COMPANY,))
    conn.commit()


def main():
    vectors_up = embed.vectors_available()
    print(f"vectors_available at start: {vectors_up}")
    with get_conn() as conn:
        cleanup(conn)

        # --- Seed: 8000, then supersede to 12000 from a chat message ---
        old_id = write_fact(
            conn,
            COMPANY,
            "Travel limit is 8000 rupees per trip.",
            ["employee", "manager"],
            effective_date=date(2026, 1, 1),
            source="manual",
            triples=[
                {
                    "source": "travel_policy",
                    "relationship": "limit",
                    "destination": "8000",
                }
            ],
        )
        ingest_chat_message(
            conn,
            COMPANY,
            "msg-travel-12000",
            "Hi team, travel limit is now 12000 rupees per trip, "
            "effective October 1. - Rahul",
        )
        new_id = write_fact(
            conn,
            COMPANY,
            "Travel limit is 12000 rupees per trip.",
            ["employee", "manager"],
            effective_date=date(2026, 10, 1),
            source="chat",
            source_message_id="msg-travel-12000",
            triples=[
                {
                    "source": "travel_policy",
                    "relationship": "limit",
                    "destination": "12000",
                }
            ],
            supersedes_id=old_id,
            relation="updates",
        )
        mgr_id = write_fact(
            conn,
            COMPANY,
            "Manager approval limit is 50000 rupees.",
            ["manager"],
            effective_date=date(2026, 1, 1),
            source="manual",
            triples=[
                {
                    "source": "approval_policy",
                    "relationship": "manager_limit",
                    "destination": "50000",
                }
            ],
        )

        # (a) Current returns 12000 only.
        ans = answer_question(conn, COMPANY, "employee", "What is the travel limit?")
        current_facts = [f["fact"] for f in ans["facts"]]
        check(
            "a-current-12000-only",
            any("12000" in f for f in current_facts)
            and not any("8000" in f for f in current_facts),
            f"facts={current_facts}",
        )

        # (b) History returns both, chat message as evidence.
        chain = get_timeline(conn, COMPANY, new_id)
        chain_facts = [e["fact"] for e in chain]
        v2 = chain[-1] if chain else {}
        src_row = conn.execute(
            "SELECT source_type, source_ref FROM memory_sources "
            "WHERE memory_id = %s",
            (new_id,),
        ).fetchone()
        check(
            "b-history-both-chat-evidence",
            len(chain) == 2
            and any("8000" in f for f in chain_facts)
            and any("12000" in f for f in chain_facts)
            and chain[0]["version"] == 1
            and chain[1]["version"] == 2
            and v2.get("source_message_id") == "msg-travel-12000"
            and src_row is not None
            and src_row[0] == "chat"
            and src_row[1] == "msg-travel-12000",
            f"chain={chain_facts} src={src_row}",
        )

        # (c) Superseded-only query -> conflict warning.
        cur = search(conn, COMPANY, "employee", "travel limit 8000")
        all_rows = search(
            conn, COMPANY, "employee", "travel limit 8000", include_superseded=True
        )
        cur_ids = {r["id"] for r in cur}
        all_ids = {r["id"] for r in all_rows}
        ans_c = answer_question(conn, COMPANY, "employee", "travel limit 8000")
        check(
            "c-conflict-warning",
            old_id in all_ids
            and old_id not in cur_ids
            and ans_c["conflict"] is not None
            and "8000" in ans_c["conflict"],
            f"conflict={ans_c['conflict']}",
        )

        # (d) Role scoping: manager-only fact hidden from employees.
        # Travel answers may differ ONLY by role-scoped facts, so the
        # check is: 12000 present for both roles, manager fact absent
        # for employees and present for managers.
        emp_mgr = search(conn, COMPANY, "employee", "manager approval limit")
        mgr_mgr = search(conn, COMPANY, "manager", "manager approval limit")
        emp_travel = answer_question(
            conn, COMPANY, "employee", "What is the travel limit?"
        )
        mgr_travel = answer_question(
            conn, COMPANY, "manager", "What is the travel limit?"
        )
        emp_travel_ids = [f["id"] for f in emp_travel["facts"]]
        mgr_travel_ids = [f["id"] for f in mgr_travel["facts"]]
        check(
            "d-role-scoping",
            not any(r["id"] == mgr_id for r in emp_mgr)
            and any(r["id"] == mgr_id for r in mgr_mgr)
            and new_id in emp_travel_ids
            and new_id in mgr_travel_ids
            and mgr_id not in emp_travel_ids,
            f"emp_mgr={len(emp_mgr)} mgr_mgr={len(mgr_mgr)} "
            f"emp_travel={len(emp_travel_ids)} "
            f"mgr_travel={len(mgr_travel_ids)}",
        )

        # Keyword path with vectors disabled must give the same current answer.
        real_available = embed.vectors_available
        embed.vectors_available = lambda timeout=2.0: False
        try:
            kw = search(conn, COMPANY, "employee", "travel limit")
        finally:
            embed.vectors_available = real_available
        check(
            "keyword-only-path",
            any("12000" in r["fact"] for r in kw)
            and not any("8000" in r["fact"] for r in kw),
            f"kw_facts={[r['fact'] for r in kw]}",
        )

        # Vector assertions, only when Ollama is actually up.
        if vectors_up:
            row = conn.execute(
                "SELECT embedding FROM memories WHERE id = %s", (new_id,)
            ).fetchone()
            raw_vec = row[0] if row else None
            # psycopg returns pgvector as its text form without the
            # pgvector adapter registered; count dims from either form.
            if isinstance(raw_vec, str):
                dims = len(raw_vec.strip().strip("[]").split(","))
            elif raw_vec is not None:
                dims = len(raw_vec)
            else:
                dims = None
            para = search(
                conn, COMPANY, "employee", "maximum amount allowed for a business trip"
            )
            para_top = para[:3]
            check(
                "vector-assertions",
                raw_vec is not None
                and dims == 768
                and any(r["id"] == new_id for r in para_top)
                and any(
                    r["scores"]["score_details"]["semantic_score"] >= 0.1
                    for r in para_top
                    if r["id"] == new_id
                ),
                f"stored_dims={dims} " f"para_top={[r['fact'] for r in para_top]}",
            )
        else:
            print("SKIP: vector-assertions (Ollama not available)")

        # As-of history.
        asof_old = search_as_of(conn, COMPANY, "employee", "travel limit", "2026-06-01")
        asof_new = search_as_of(conn, COMPANY, "employee", "travel limit", "2026-12-01")
        check(
            "as-of-history",
            asof_old
            and "8000" in asof_old[0]["fact"]
            and asof_new
            and "12000" in asof_new[0]["fact"],
            f"jun={[r['fact'] for r in asof_old[:1]]} "
            f"dec={[r['fact'] for r in asof_new[:1]]}",
        )

        # Media ingest: honest embedded flags + graph link.
        mid_cap = ingest_media(
            conn,
            COMPANY,
            "receipt.jpg",
            "image/jpeg",
            "media/x_receipt.jpg",
            caption="Hotel receipt for Rahul",
        )
        row_cap = conn.execute(
            "SELECT embedded, embedding FROM media WHERE id = %s", (mid_cap,)
        ).fetchone()
        mid_plain = ingest_media(
            conn, COMPANY, "photo.png", "image/png", "media/x_photo.png"
        )
        row_plain = conn.execute(
            "SELECT embedded, embedding FROM media WHERE id = %s", (mid_plain,)
        ).fetchone()
        edge_id = link_media_to_fact(conn, COMPANY, mid_cap, new_id)
        edge_row = conn.execute(
            "SELECT subject, relationship, object FROM edges WHERE id = %s",
            (edge_id,),
        ).fetchone()
        other_fact = write_fact(
            conn,
            "other-co",
            "Other company travel limit is 100 rupees.",
            ["employee", "manager"],
            source="manual",
        )
        try:
            link_media_to_fact(conn, COMPANY, mid_cap, other_fact)
            wrong_company_ok = False
        except ValueError:
            wrong_company_ok = True
        conn.execute("DELETE FROM memories WHERE company_id = %s", ("other-co",))
        conn.commit()
        media_ok = (
            row_cap[0] == vectors_up
            and (row_cap[1] is not None) == vectors_up
            and row_plain[0] is False
            and row_plain[1] is None
            and edge_row is not None
            and edge_row[0] == f"media:{mid_cap}"
            and edge_row[1] == "evidence_for"
            and wrong_company_ok
        )
        check(
            "media-ingest-honest-flags",
            media_ok,
            f"caption_embedded={row_cap[0]} plain_embedded={row_plain[0]} "
            f"edge={edge_row} wrong_company_raises={wrong_company_ok}",
        )

        # Explain fields present.
        res = search(conn, COMPANY, "employee", "travel limit")
        sd = res[0]["scores"]["score_details"] if res else {}
        check(
            "explain-fields",
            set(sd.keys())
            == {
                "semantic_score",
                "bm25_score",
                "entity_boost",
                "raw_score",
                "max_possible_score",
                "final_score",
                "threshold",
            },
            f"keys={sorted(sd.keys())}",
        )

        # Dedup: identical fact returns the same id, no new row.
        before = conn.execute(
            "SELECT count(*) FROM memories WHERE company_id = %s", (COMPANY,)
        ).fetchone()[0]
        dup_id = write_fact(
            conn,
            COMPANY,
            "Travel limit is 12000 rupees per trip.",
            ["employee", "manager"],
            effective_date=date(2026, 10, 1),
            source="chat",
            source_message_id="msg-travel-12000",
        )
        after = conn.execute(
            "SELECT count(*) FROM memories WHERE company_id = %s", (COMPANY,)
        ).fetchone()[0]
        check(
            "dedup-same-id",
            dup_id == new_id and before == after,
            f"dup={dup_id == new_id} count={before}->{after}",
        )

        # Supersede transaction shape: old flipped, new versioned, edge
        # stored on the NEW fact keyed by the OLD fact's id.
        import json as _json

        old_row = conn.execute(
            "SELECT version, is_latest, status FROM memories " "WHERE id = %s",
            (old_id,),
        ).fetchone()
        new_row = conn.execute(
            "SELECT version, parent_memory_id, root_memory_id, "
            "memory_relations FROM memories WHERE id = %s",
            (new_id,),
        ).fetchone()
        rels = new_row[3] if new_row else {}
        if isinstance(rels, str):
            rels = _json.loads(rels)
        check(
            "supersede-transaction",
            old_row[0] == 1
            and old_row[1] is False
            and old_row[2] == "superseded"
            and new_row[0] == 2
            and str(new_row[1]) == old_id
            and str(new_row[2]) == old_id
            and rels.get(old_id) == "updates",
            f"old={old_row} new_version={new_row[0]} rels={rels}",
        )

        # HTTP surfaces: health + timeline page. The timeline handler
        # scopes by COMPANY_ID env, so point it at the test company.
        os.environ["COMPANY_ID"] = COMPANY
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        health = client.get("/health")
        page = client.get(f"/timeline/{new_id}")
        check(
            "http-surfaces",
            health.status_code == 200
            and health.json().get("status") == "ok"
            and page.status_code == 200
            and "Policy timeline" in page.text
            and "12000" in page.text,
            f"health={health.status_code} timeline={page.status_code}",
        )

    passed = sum(1 for _, ok in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} PASS")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
