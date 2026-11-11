"""`fp update all`: the periodic pipeline. Each step is isolated — one failing source never blocks the rest."""

from __future__ import annotations

import time
import traceback
from datetime import date
from typing import Callable

STEPS = ["csrankings", "dblp", "dblp_extra", "korean", "korean_verify", "orcid", "homepage", "degree_llm", "departments", "positions", "hangul", "openalex", "inst_types", "citations", "title_citations", "snapshot", "report"]


def run_all(log: Callable[[str], None] = print, skip: set[str] | None = None, use_llm: bool = True) -> dict:
    from fp import institutions, pii, pool, report
    from fp.ingest import llm_degree, llm_dept, llm_hangul, llm_position, citations, csrankings, dblp, dblp_extra, homepage, openalex, orcid

    skip = skip or set()
    out: dict[str, object] = {}

    def step(name, fn):
        if name in skip:
            out[name] = "skipped"
            return
        t = time.time()
        log(f"[{name}] start")
        try:
            out[name] = fn()
            log(f"[{name}] done in {time.time() - t:.0f}s: {out[name]}")
        except Exception as e:  # keep going; the report shows what failed
            out[name] = f"FAILED: {e!r}"
            log(f"[{name}] FAILED\n{traceback.format_exc()}")

    sha_box: dict[str, str] = {}

    def do_csrankings():
        sha, counts = csrankings.update()
        sha_box["sha"] = sha
        return {"sha": sha[:12], "loaded": counts is not None}

    step("csrankings", do_csrankings)

    con = pii.connect()
    try:
        def do_dblp():
            sha = sha_box.get("sha") or con.execute(
                "SELECT version FROM snapshot WHERE source='csrankings' ORDER BY fetched_at DESC LIMIT 1").fetchone()[0]
            if con.execute("SELECT 1 FROM snapshot WHERE source='dblp' AND version=?", [sha]).fetchone():
                return {"sha": sha[:12], "loaded": False}
            src = dblp.download(sha)
            dblp.parse(src, dblp.snapshot_dir(sha))
            return {"sha": sha[:12], **dblp.load(con, sha)}

        step("dblp", do_dblp)
        step("dblp_extra", lambda: dblp_extra.reapply(con))  # catalog venues outside CSrankings (reload wipes them)
        step("korean", lambda: pool.classify(con, since=date.today().year - 10, use_llm=use_llm))
        step("korean_verify", lambda: pool.verify(con) if use_llm else "skipped (no llm)")
        step("orcid", lambda: {s: orcid.update(con, scope=s) for s in ("kr", "korean")})
        step("homepage", lambda: homepage.update(con, scope="kr", use_llm=use_llm))
        step("degree_llm", lambda: llm_degree.update(con) if use_llm else "skipped (no llm)")
        step("departments", lambda: llm_dept.update(con) if use_llm else "skipped (no llm)")  # new faculty only
        # new people only (cached answers are reused); after openalex would see newer affiliations, but the pool
        # (targets) only needs DBLP + korean
        step("positions", lambda: llm_position.update(con) if use_llm else "skipped (no llm)")
        step("hangul", lambda: llm_hangul.update(con) if use_llm else "skipped (no llm)")  # new people only
        step("openalex", lambda: openalex.update(con))
        step("inst_types", lambda: institutions.fetch_types(con))
        step("citations", lambda: citations.update(con))
        step("title_citations", lambda: citations.update_titles(con))
        step("snapshot", lambda: {"run_id": report.snapshot(con, out)})
        step("report", lambda: {"path": report.write(con)})
    finally:
        con.close()
    return out
