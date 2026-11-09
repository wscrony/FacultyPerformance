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
console = Console()

THIS_YEAR = date.today().year


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


if __name__ == "__main__":
    app()
