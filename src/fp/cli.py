"""`fp` command-line interface."""

from __future__ import annotations

import csv
from datetime import date
from typing import Optional

from datetime import datetime

import typer
from rich.console import Console
from rich.table import Table

from fp import db, metrics, paths

app = typer.Typer(no_args_is_help=True, add_completion=False)
update_app = typer.Typer(no_args_is_help=True, help="Fetch sources and refresh the local DB.")
app.add_typer(update_app, name="update")
pii_app = typer.Typer(no_args_is_help=True, help="Local encrypted personal-data store (age, degrees).")
app.add_typer(pii_app, name="pii")


@pii_app.command("export-key")
def pii_export_key(to: str = typer.Argument(..., help="file to write (created with mode 600)")):
    """Copy the pii.duckdb key from this Mac's Keychain to a private file, to move it to a server.
    Transfer it securely (scp/USB/password manager), never by e-mail or chat; delete the copy afterwards."""
    from fp import pii

    key = pii.keychain_key()
    if not key:
        typer.echo("no key in the Keychain", err=True)
        raise typer.Exit(1)
    typer.echo(f"wrote {pii.write_key_file(key, to)} (mode 600)")


@pii_app.command("import-key")
def pii_import_key(src: str = typer.Argument(..., help="key file from `fp pii export-key`")):
    """Install a key on this machine (default key file; on macOS also usable via Keychain)."""
    from pathlib import Path

    from fp import pii

    key = Path(src).expanduser().read_text().strip()
    if len(key) != 64:
        typer.echo("not a pii key (expected 64 hex chars)", err=True)
        raise typer.Exit(1)
    kf = pii.write_key_file(key)
    con = pii.connect(read_only=True)  # verifies the key opens the DB
    n = con.execute("SELECT count(*) FROM pii.affiliation").fetchone()[0]
    typer.echo(f"installed {kf} (mode 600); pii.duckdb opens ({n:,} affiliation rows)")
console = Console()

THIS_YEAR = date.today().year

from contextlib import contextmanager


@contextmanager
def _progress(label: str, every: int = 25):
    """Rich bar on a terminal; plain periodic lines when output is redirected to a log."""
    if console.is_terminal:
        from rich.progress import Progress

        with Progress(console=console) as prog:
            task = prog.add_task(label, total=None)
            yield lambda n, tot: prog.update(task, completed=n, total=tot)
    else:
        def cb(n, tot):
            if n % every == 0 or n == tot:
                print(f"{label}: {n}/{tot}", flush=True)
        yield cb



def _split(s: Optional[str]) -> list[str] | None:
    return [x.strip() for x in s.split(",") if x.strip()] if s else None


def _filter(start, end, area, next_tier, country, school) -> metrics.Filter:
    return metrics.Filter(
        start=start,
        end=end,
        areas=_split(area),
        next_tier=next_tier,
        country=None if country in (None, "", "world") else country,
        schools=_split(school),
    )


def _fmt(v):
    if isinstance(v, float):
        return f"{v:.2f}"
    if isinstance(v, dict):
        return ", ".join(f"{k}:{x}" for k, x in list(v.items())[:6])
    return str(v)


def _show(rows, cols, title: str, csv_out: Optional[str]):
    if csv_out:
        out = paths.EXPORTS / csv_out
        paths.ensure_dirs()
        with open(out, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            w.writerows([[_fmt(v) for v in r] for r in rows])
        console.print(f"[green]wrote {out}[/green]")
        return
    t = Table(title=title)
    t.add_column("#", justify="right")
    for c in cols:
        t.add_column(c)
    for i, r in enumerate(rows, 1):
        t.add_row(str(i), *[_fmt(v) for v in r])
    console.print(t)


Start = typer.Option(THIS_YEAR - 10, "--from", help="start year")
End = typer.Option(THIS_YEAR, "--to", help="end year")
Area = typer.Option(None, help="comma-separated parent areas, e.g. mod,mlmining (see `fp areas`)")
NextTier = typer.Option(False, "--next-tier", help="include next-tier venues")
Country = typer.Option("kr", help="institution country code, or 'world'")
School = typer.Option(None, help="comma-separated institution names")
CsvOut = typer.Option(None, "--csv", help="write to exports/<file> instead of printing")


@update_app.command("csrankings")
def update_csrankings(force: bool = typer.Option(False, help="reload even if sha unchanged")):
    from fp.ingest import csrankings

    sha, counts = csrankings.update(force=force)
    if counts is None:
        console.print(f"csrankings already at {sha[:12]} — nothing to do")
    else:
        console.print(f"loaded csrankings {sha[:12]}: {counts}")


@app.command()
def areas():
    """List parent areas."""
    con = db.connect(read_only=True)
    rows = metrics.all_areas(con)
    _show(rows, ["area", "title", "category"], "Areas", None)


@app.command()
def schools(
    start: int = Start, end: int = End, area: Optional[str] = Area, next_tier: bool = NextTier,
    country: str = Country, csv_out: Optional[str] = CsvOut,
):
    """Rank institutions: geometric mean over areas of (paper count + 1)."""
    con = db.connect(read_only=True)
    rows, cols = metrics.school_ranking(con, _filter(start, end, area, next_tier, country, None))
    _show(rows, cols, f"Schools {country} {start}-{end} {area or 'all areas'}", csv_out)


@app.command()
def faculty(
    start: int = Start, end: int = End, area: Optional[str] = Area, next_tier: bool = NextTier,
    country: str = Country, school: Optional[str] = School, limit: int = typer.Option(50),
    csv_out: Optional[str] = CsvOut,
):
    """Rank faculty by paper count."""
    con = db.connect(read_only=True)
    rows, cols = metrics.faculty_table(con, _filter(start, end, area, next_tier, country, school), limit)
    _show(rows, cols, f"Faculty {school or country} {start}-{end} {area or 'all areas'}", csv_out)


@app.command()
def person(name: str, start: int = typer.Option(1970, "--from"), end: int = typer.Option(THIS_YEAR, "--to")):
    """Publication profile of one person (exact csrankings name, or a substring)."""
    con = db.connect(read_only=True)
    rows, cols = metrics.person_profile(con, name, start, end)
    if not rows:
        hits = metrics.find_people(con, name)
        if len(hits) == 1:
            name = hits[0][0]
            rows, cols = metrics.person_profile(con, name, start, end)
        else:
            _show(hits, ["name", "affiliation"], f"Matches for '{name}'", None)
            return
    _show(rows, cols, name, None)


@app.command()
def compare(
    names: list[str], start: int = Start, end: int = End, next_tier: bool = NextTier,
):
    """Compare people side by side: pubs per area within the year range."""
    con = db.connect(read_only=True)
    resolved = []
    for n in names:
        hits = [h[0] for h in metrics.find_people(con, n)]
        exact = [h for h in hits if h == n]
        if exact or len(hits) == 1:
            resolved.append((exact or hits)[0])
        else:
            console.print(f"[red]'{n}' is ambiguous or unknown: {hits[:8]}[/red]")
            raise typer.Exit(1)
    f = metrics.Filter(start=start, end=end, next_tier=next_tier, country=None)
    where, params = f.where()
    rows = con.execute(
        f"""PIVOT (SELECT v.area_title AS area, p.name, sum(p.count) AS pubs {metrics.BASE}
                  WHERE {where} AND p.name IN (SELECT unnest(?)) GROUP BY ALL)
            ON name USING sum(pubs) GROUP BY area ORDER BY area""",
        params + [resolved],
    ).fetchall()
    cols = ["area"] + sorted(resolved)
    _show([[r[0]] + [int(x or 0) for x in r[1:]] for r in rows], cols, f"{start}-{end}", None)


@app.command("app")
def run_app(
    port: int = typer.Option(8501),
    host: str = typer.Option("127.0.0.1", help="0.0.0.0 to serve other computers (requires FP_APP_PASSWORD)"),
):
    """Open the dashboard. Local only by default; serving on a network requires a login password."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    if host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("FP_APP_PASSWORD"):
        typer.echo("Refusing to serve on the network without a password (personal data). "
                   "Set FP_APP_PASSWORD, or use 127.0.0.1 behind an SSH tunnel / authenticating proxy.", err=True)
        raise typer.Exit(2)
    script = Path(__file__).with_name("main.py")
    subprocess.run([
        sys.executable, "-m", "streamlit", "run", str(script),
        "--server.address", host, "--server.port", str(port),
        "--server.headless", "true", "--browser.gatherUsageStats", "false",
    ])


@update_app.command("all")
def update_all(
    skip: Optional[str] = typer.Option(None, help="comma-separated steps to skip"),
    no_llm: bool = typer.Option(False, "--no-llm"),
):
    """Run the whole periodic pipeline, snapshot tracked people, write the change report to exports/."""
    from fp import pipeline

    stamp = lambda m: print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {m}", flush=True)
    out = pipeline.run_all(log=stamp, skip=set((skip or "").split(",")) - {""}, use_llm=not no_llm)
    failed = [k for k, v in out.items() if isinstance(v, str) and v.startswith("FAILED")]
    stamp(f"finished; failed steps: {failed or 'none'}")
    if failed:
        raise typer.Exit(1)


schedule_app = typer.Typer(help="Periodic `fp update all` via a user launchd agent (macOS)")
app.add_typer(schedule_app, name="schedule")


@schedule_app.command("install")
def schedule_install(
    weekday: int = typer.Option(0, help="0 = Sunday … 6 = Saturday"),
    hour: int = typer.Option(3), minute: int = typer.Option(0),
):
    from fp import schedule

    console.print(f"installed {schedule.install(weekday, hour, minute)}")


@schedule_app.command("uninstall")
def schedule_uninstall():
    from fp import schedule

    console.print("removed" if schedule.uninstall() else "not installed")


@schedule_app.command("status")
def schedule_status():
    from fp import schedule

    console.print(schedule.status())


@app.command("report")
def report_cmd(snapshot: bool = typer.Option(False, help="take a snapshot first (normally done by update all)")):
    """Write the change report (last two pipeline runs) to exports/ and print it."""
    from fp import pii, report

    con = pii.connect(read_only=not snapshot)
    if snapshot:
        report.snapshot(con, {"manual": True})
    path = report.write(con)
    console.print(open(path, encoding="utf-8").read())
    console.print(f"[dim]{path}[/dim]")


@update_app.command("dblp")
def update_dblp(force: bool = typer.Option(False)):
    """Parse CSrankings' filtered DBLP (all authors, top venues) for the loaded csrankings commit."""
    from fp.ingest import dblp

    con = db.connect()
    row = con.execute("SELECT version FROM snapshot WHERE source='csrankings' ORDER BY fetched_at DESC LIMIT 1").fetchone()
    if not row:
        raise typer.BadParameter("run `fp update csrankings` first")
    sha = row[0]
    done = con.execute("SELECT 1 FROM snapshot WHERE source='dblp' AND version=?", [sha]).fetchone()
    if done and not force:
        console.print(f"dblp already loaded for {sha[:12]}")
        return
    src = dblp.download(sha)
    out = dblp.snapshot_dir(sha)
    if force or not (out / "authorship.parquet").exists():
        console.print(dblp.parse(src, out, progress=lambda n: print(f"dblp: {n} records", flush=True)))
    console.print(dblp.load(con, sha))
    console.print({"parity_vs_csrankings_kr": dblp.check_against_csrankings(con)})


@update_app.command("degree-llm")
def update_degree_llm(
    model: str = typer.Option("opus", help="claude model for the knowledge lookup"),
    calibrate: bool = typer.Option(False, help="only measure accuracy on people with confirmed PhD years"),
    force: bool = typer.Option(False, help="re-ask people asked in the last 180 days"),
):
    """PhD year from LLM knowledge for faculty (KR + Korean abroad) with no ORCID/homepage degree."""
    from fp import pii
    from fp.ingest import llm_degree

    con = pii.connect()
    if calibrate:
        console.print(llm_degree.calibrate(con, model=model))
        return
    with _progress("degree-llm", every=1) as cb:
        console.print(llm_degree.update(con, model=model, force=force, progress=cb))


@update_app.command("korean")
def update_korean(
    since: int = typer.Option(THIS_YEAR - 10, help="pool: active since this year"),
    min_pubs: int = typer.Option(3, help="pool: min top-venue papers since --since"),
    no_llm: bool = typer.Option(False, "--no-llm", help="rules + co-authorship only"),
    verify: bool = typer.Option(True, help="second opinion (Sonnet) for faculty abroad + top non-faculty"),
    verify_top: int = typer.Option(300, help="how many top non-faculty to verify"),
):
    """Classify the candidate pool as Korean / not (stored in the encrypted PII DB)."""
    from fp import pii, pool

    con = pii.connect()
    with _progress("korean-llm", every=1) as cb:
        stats = pool.classify(con, since=since, min_pubs=min_pubs, use_llm=not no_llm, progress=cb)
    console.print(stats)
    if verify and not no_llm:
        with _progress("korean-verify", every=1) as cb:
            console.print(pool.verify(con, top_other=verify_top, progress=cb))


@update_app.command("orcid")
def update_orcid(
    scope: str = typer.Option("kr", help="kr = KR faculty; korean = Korean pool incl. non-faculty (ORCID from DBLP); world = all csrankings"),
    force: bool = typer.Option(False, help=f"refetch even if fetched recently"),
    limit: Optional[int] = typer.Option(None),
):
    """Fetch degree years from ORCID (public API) into the encrypted PII DB."""
    from fp import pii
    from fp.ingest import orcid

    con = pii.connect()
    with _progress("ORCID") as cb:
        stats = orcid.update(con, scope=scope, force=force, limit=limit, progress=cb)
    console.print(stats)


@update_app.command("openalex")
def update_openalex(
    per_person: int = typer.Option(3, help="most recent DOI papers per person (+ the earliest)"),
    force: bool = typer.Option(False, help="refetch even if fetched recently"),
    limit: Optional[int] = typer.Option(None, help="max people"),
    workers: int = typer.Option(4),
):
    """Affiliation history from OpenAlex via each person's own DOIs (stored in the encrypted PII DB)."""
    from fp import pii
    from fp.ingest import openalex

    con = pii.connect()
    with _progress("OpenAlex works") as cb:
        stats = openalex.update(con, per_person=per_person, force=force, limit=limit, workers=workers, progress=cb)
    console.print(stats)


@update_app.command("dblp-extra")
def update_dblp_extra(parse: bool = typer.Option(True, help="re-parse data/raw/dblp_full/dblp.xml.gz first")):
    """Venues of the top-venue catalog that CSrankings lacks (SoCG, CIKM, ...) from the full DBLP dump."""
    from fp import pii
    from fp.ingest import dblp_extra

    if parse:  # read-only while parsing (~20 min), so the app can keep running
        ro = pii.connect(read_only=True)
        prefixes = dblp_extra.wanted_keys(ro)
        ro.close()
        typer.echo(f"{len(prefixes)} venues to extract")
        with _progress("dblp.xml records", every=1) as cb:
            typer.echo(dblp_extra.parse(prefixes, progress=lambda n: cb(n, 0)))
    typer.echo(dblp_extra.reapply(pii.connect()))  # needs the write lock: stop the app first


@update_app.command("departments")
def update_departments(model: str = "opus", force: bool = False):
    """Department of each KR faculty member (LLM knowledge; encrypted pii.department)."""
    from fp import pii
    from fp.ingest import llm_dept

    con = pii.connect()
    with _progress("departments", every=25) as cb:
        typer.echo(llm_dept.update(con, model=model, force=force, progress=cb))


@update_app.command("positions")
def update_positions(model: str = "sonnet", limit: Optional[int] = typer.Option(None), workers: int = 4,
                     store_only: bool = typer.Option(False, help="only load data/snapshots/positions.jsonl")):
    """Current position of non-faculty Korean researchers (claude CLI + web search; pii.position)."""
    from fp import pii
    from fp.ingest import llm_position

    con = pii.connect()
    if store_only:
        typer.echo(llm_position.store(con))
        return
    with _progress("positions", every=25) as cb:
        typer.echo(llm_position.update(con, model=model, limit=limit, workers=workers, progress=cb))


@app.command("set-position")
def set_position(person: str, position: str = typer.Option(..., help="professor/postdoc/phd_student/industry/..."),
                 institution: Optional[str] = None, country: Optional[str] = typer.Option(None, help="kr, us, ..."),
                 title: Optional[str] = None, since: Optional[int] = None, korean_name: Optional[str] = None,
                 url: Optional[str] = None):
    """Hand-correct a person's current position / Korean name (DBLP name, e.g. "Jinheon Baek"). Takes effect after
    `fp update positions --store-only` (or the next pipeline run)."""
    from fp.ingest import llm_position

    typer.echo(llm_position.set_manual(person, position, institution, country, title, since, korean_name, url))


@update_app.command("title-citations")
def update_title_citations(force: bool = typer.Option(False), limit: Optional[int] = typer.Option(None)):
    """Citations for papers without a DOI (NeurIPS/ICLR/...), by exact title match on OpenAlex."""
    from fp import pii
    from fp.ingest import citations

    con = pii.connect()
    with _progress("OpenAlex title citations") as cb:
        stats = citations.update_titles(con, force=force, limit=limit, progress=cb)
    typer.echo(stats)


@update_app.command("citations")
def update_citations(force: bool = typer.Option(False), limit: Optional[int] = typer.Option(None, help="max DOIs")):
    """Citation counts (OpenAlex, 100 DOIs/request) for top-venue papers of tracked people."""
    from fp import pii
    from fp.ingest import citations

    con = pii.connect()
    with _progress("OpenAlex citations") as cb:
        stats = citations.update(con, force=force, limit=limit, progress=cb)
    console.print(stats)


@update_app.command("homepage")
def update_homepage(
    scope: str = typer.Option("kr"),
    force: bool = typer.Option(False, help="refetch even if fetched recently"),
    limit: Optional[int] = typer.Option(None),
    no_llm: bool = typer.Option(False, "--no-llm", help="regex only; nothing leaves this machine except page fetches"),
    workers: int = typer.Option(4),
    retry_no_page: bool = typer.Option(False, "--retry-no-page", help="also retry people whose page was missing"),
):
    """Extract degree/birth years from homepages/CVs of people still missing a PhD year."""
    from fp import pii
    from fp.ingest import homepage

    con = pii.connect()
    with _progress("homepage") as cb:
        stats = homepage.update(
            con, scope=scope, force=force, limit=limit, use_llm=not no_llm, workers=workers,
            retry_no_page=retry_no_page, progress=cb
        )
    console.print(stats)


@app.command()
def candidates(
    start: int = typer.Option(THIS_YEAR - 5, "--from"), end: int = End, area: Optional[str] = Area,
    next_tier: bool = NextTier,
    scope: str = typer.Option("kr", help="kr = KR faculty; korean = Korean researchers anywhere; world = all faculty"),
    school: Optional[str] = School,
    age_min: Optional[int] = typer.Option(None, help="min academic age (years since PhD)"),
    age_max: Optional[int] = typer.Option(None, help="max academic age"),
    min_pubs: int = typer.Option(0, help="min papers in the year range"),
    window: int = typer.Option(2, help="cohort = academic age ± window"),
    sort: str = typer.Option("pubs", help="rank by: pubs | cites | pctl"),
    limit: int = typer.Option(50), csv_out: Optional[str] = CsvOut,
    include_pii: bool = typer.Option(False, "--include-pii", help="keep age/degree columns in CSV"),
):
    """Search people by area output and academic age; percentile vs KR faculty of similar age."""
    from fp import age, pii

    con = pii.connect(read_only=True)
    rows, cols = age.candidates(
        con, _filter(start, end, area, next_tier, None, school),
        age_min=age_min, age_max=age_max, min_pubs=min_pubs, cohort_window=window, limit=limit, scope=scope,
        sort=sort,
    )
    if csv_out and not include_pii:
        keep = [i for i, c in enumerate(cols) if c not in age.PII_COLUMNS]
        rows, cols = [[r[i] for i in keep] for r in rows], [cols[i] for i in keep]
    elif not csv_out:
        rows, cols = _compact_age(rows, cols)
        console.print("[dim]# = rank; * = estimated; age = this year − birth year; pctl vs KR faculty with acad age ± window; 1st/last = author position[/dim]")
    _show(rows, cols, f"Candidates [{scope}, rank by {sort}] {school or ''} {start}-{end} {area or 'all'} age {age_min}-{age_max}", csv_out)


def _compact_age(rows, cols):
    """Terminal view: fold *_src columns into a '*' marker on estimated values."""
    ix = {c: i for i, c in enumerate(cols)}
    out_cols = ["name", "institution", "role", "age", "acad_age", "pubs", "1st/last", "cites", "pctl", "n"]
    out = []
    for r in rows:
        est_age = r[ix["age_src"]] is None or r[ix["age_src"]].startswith("est")
        est_birth = r[ix["birth_src"]] is None or r[ix["birth_src"]].startswith("est")
        mark = lambda v, est: "-" if v is None else f"{v}{'*' if est else ''}"
        out.append([
            r[ix["name"]], r[ix["institution"]], r[ix["role"]], mark(r[ix["age"]], est_birth),
            mark(r[ix["acad_age"]], est_age), r[ix["pubs"]],
            f"{r[ix['first_au']]}/{r[ix['last_au']]}",
            "-" if r[ix["cites"]] is None else r[ix["cites"]],
            "-" if r[ix["cohort_pctl"]] is None else int(r[ix["cohort_pctl"]]), r[ix["cohort_n"]],
        ])
    return out, out_cols


@pii_app.command("set-korean")
def pii_set_korean(person: str, korean: bool = typer.Option(..., "--yes/--no", help="is the person Korean?")):
    """Manual override of the Korean classification (wins over rules, LLM and verification)."""
    from fp import pii

    con = pii.connect()
    con.execute("INSERT OR REPLACE INTO pii.korean (person, is_korean, score, method, reason) "
                "VALUES (?, ?, NULL, 'manual', 'set by user')", [person, korean])
    console.print(f"{person}: {'Korean' if korean else 'not Korean'} (manual)")


@pii_app.command("show")
def pii_show(name: str):
    """Degrees / birth info on file for a person."""
    from fp import age, pii

    con = pii.connect(read_only=True)
    age.create_views(con)
    for title, q in [
        ("Degrees", "SELECT degree, year, institution, source, confidence, evidence FROM pii.degree WHERE person=? ORDER BY degree, source"),
        ("Birth", "SELECT year, source, confidence, evidence FROM pii.birth WHERE person=?"),
        ("Resolved", "SELECT phd_year, age_source, academic_age, birth_year, birth_source, first_top_year FROM person_age WHERE person=?"),
        ("Fetch log", "SELECT source, status, fetched_at FROM pii.fetch_log WHERE person=? ORDER BY fetched_at DESC LIMIT 5"),
    ]:
        cur = con.execute(q, [name])
        _show(cur.fetchall(), [d[0] for d in cur.description], title, None)


@pii_app.command("set-degree")
def pii_set_degree(name: str, degree: str, year: int, institution: Optional[str] = typer.Option(None),
                   evidence: str = typer.Option("manual entry")):
    """Manually record a degree (phd/ms/bs); manual beats orcid/homepage."""
    from fp import pii

    if degree not in ("phd", "ms", "bs"):
        raise typer.BadParameter("degree must be phd, ms or bs")
    con = pii.connect()
    con.execute("DELETE FROM pii.degree WHERE person=? AND degree=? AND source='manual'", [name, degree])
    con.execute(
        "INSERT INTO pii.degree (person, degree, year, institution, source, confidence, evidence) VALUES (?,?,?,?, 'manual', 1.0, ?)",
        [name, degree, year, institution, evidence],
    )
    console.print("saved")


@pii_app.command("set-birth")
def pii_set_birth(name: str, year: int, evidence: str = typer.Option("manual entry")):
    """Manually record a birth year."""
    from fp import pii

    con = pii.connect()
    con.execute("DELETE FROM pii.birth WHERE person=? AND source='manual'", [name])
    con.execute("INSERT INTO pii.birth (person, year, source, confidence, evidence) VALUES (?,?, 'manual', 1.0, ?)",
                [name, year, evidence])
    console.print("saved")


@pii_app.command("forget")
def pii_forget(name: str, yes: bool = typer.Option(False, "--yes", help="skip confirmation")):
    """Delete all personal data for a person and block re-collection."""
    from fp import pii

    if not yes:
        typer.confirm(f"Delete all PII for '{name}' and block re-collection?", abort=True)
    con = pii.connect()
    console.print(pii.forget(con, name))


@pii_app.command("stats")
def pii_stats():
    """Coverage of age sources among KR faculty."""
    from fp import age, pii

    con = pii.connect(read_only=True)
    age.create_views(con)
    cur = con.execute(
        """SELECT coalesce(a.age_source, 'none') AS age_source, count(*) AS faculty
           FROM person_age a JOIN institution i ON i.institution = a.affiliation
           WHERE i.country = 'kr' GROUP BY 1 ORDER BY 2 DESC"""
    )
    _show(cur.fetchall(), ["age_source", "kr_faculty"], "Academic-age source coverage (KR)", None)
    cur = con.execute("SELECT source, status, count(*) FROM pii.fetch_log GROUP BY ALL ORDER BY ALL")
    _show(cur.fetchall(), ["source", "status", "n"], "Fetch log", None)


if __name__ == "__main__":
    app()


@app.command("export-app-bundle")
def export_app_bundle():
    """Open-data bundle for the hosted app (streamlit.app) → exports/app_bundle/fp_data.tar.gz."""
    from fp import bundle

    typer.echo(bundle.export())
