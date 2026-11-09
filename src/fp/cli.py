"""`fp` command-line interface."""

from __future__ import annotations

import csv
from datetime import date
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from fp import db, metrics, paths

app = typer.Typer(no_args_is_help=True, add_completion=False)
update_app = typer.Typer(no_args_is_help=True, help="Fetch sources and refresh the local DB.")
app.add_typer(update_app, name="update")
pii_app = typer.Typer(no_args_is_help=True, help="Local encrypted personal-data store (age, degrees).")
app.add_typer(pii_app, name="pii")
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
    """Rank institutions (CSrankings geometric-mean score)."""
    con = db.connect(read_only=True)
    rows, cols = metrics.school_ranking(con, _filter(start, end, area, next_tier, country, None))
    _show(rows, cols, f"Schools {country} {start}-{end} {area or 'all areas'}", csv_out)


@app.command()
def faculty(
    start: int = Start, end: int = End, area: Optional[str] = Area, next_tier: bool = NextTier,
    country: str = Country, school: Optional[str] = School, limit: int = typer.Option(50),
    csv_out: Optional[str] = CsvOut,
):
    """Rank faculty by adjusted count."""
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


@update_app.command("orcid")
def update_orcid(
    scope: str = typer.Option("kr", help="kr = faculty at KR institutions; world = all csrankings"),
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


@update_app.command("homepage")
def update_homepage(
    scope: str = typer.Option("kr"),
    force: bool = typer.Option(False, help="refetch even if fetched recently"),
    limit: Optional[int] = typer.Option(None),
    no_llm: bool = typer.Option(False, "--no-llm", help="regex only; nothing leaves this machine except page fetches"),
    workers: int = typer.Option(4),
):
    """Extract degree/birth years from homepages/CVs of people still missing a PhD year."""
    from fp import pii
    from fp.ingest import homepage

    con = pii.connect()
    with _progress("homepage") as cb:
        stats = homepage.update(
            con, scope=scope, force=force, limit=limit, use_llm=not no_llm, workers=workers, progress=cb
        )
    console.print(stats)


@app.command()
def candidates(
    start: int = typer.Option(THIS_YEAR - 5, "--from"), end: int = End, area: Optional[str] = Area,
    next_tier: bool = NextTier, country: str = Country, school: Optional[str] = School,
    age_min: Optional[int] = typer.Option(None, help="min academic age (years since PhD)"),
    age_max: Optional[int] = typer.Option(None, help="max academic age"),
    min_adj: float = typer.Option(0.0, help="min adjusted count in the year range"),
    window: int = typer.Option(2, help="cohort = academic age ± window"),
    limit: int = typer.Option(50), csv_out: Optional[str] = CsvOut,
    include_pii: bool = typer.Option(False, "--include-pii", help="keep age/degree columns in CSV"),
):
    """Search people by area output and academic age; percentile vs KR faculty of similar age."""
    from fp import age, pii

    con = pii.connect(read_only=True)
    rows, cols = age.candidates(
        con, _filter(start, end, area, next_tier, country, school),
        age_min=age_min, age_max=age_max, min_adj=min_adj, cohort_window=window, limit=limit,
    )
    if csv_out and not include_pii:
        keep = [i for i, c in enumerate(cols) if c not in age.PII_COLUMNS]
        rows, cols = [[r[i] for i in keep] for r in rows], [cols[i] for i in keep]
    elif not csv_out:
        rows, cols = _compact_age(rows, cols)
        console.print("[dim]* = estimated (no PhD/birth year on file); pctl vs KR faculty with acad age ± window[/dim]")
    _show(rows, cols, f"Candidates {school or country} {start}-{end} {area or 'all'} age {age_min}-{age_max}", csv_out)


def _compact_age(rows, cols):
    """Terminal view: fold *_src columns into a '*' marker on estimated values."""
    ix = {c: i for i, c in enumerate(cols)}
    out_cols = ["name", "institution", "acad_age", "birth", "pubs", "adj", "pctl", "cohort_n"]
    out = []
    for r in rows:
        est_age = (r[ix["age_src"]] or "").startswith("est") or r[ix["age_src"]] is None
        est_birth = (r[ix["birth_src"]] or "").startswith("est") or r[ix["birth_src"]] is None
        mark = lambda v, est: "-" if v is None else f"{v}{'*' if est else ''}"
        out.append([
            r[ix["name"]], r[ix["institution"]], mark(r[ix["acad_age"]], est_age),
            mark(r[ix["birth_year"]], est_birth), r[ix["pubs"]], r[ix["adj"]],
            "-" if r[ix["cohort_pctl"]] is None else int(r[ix["cohort_pctl"]]), r[ix["cohort_n"]],
        ])
    return out, out_cols


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
