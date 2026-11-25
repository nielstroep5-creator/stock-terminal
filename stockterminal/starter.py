"""Starter pack: everything the app shows for every loaded company, in one compressed file, so a new
user doesn't have to download the S&P 500 from scratch (hours).

    python -m stockterminal.starter export      # make data/export/stock-terminal-data-YYYY-MM-DD.zip
    python -m stockterminal.starter import FILE  # unpack a starter pack (only into an empty data folder)

On a new PC the app unpacks it by itself: put the .zip in the Stock Terminal folder (or in data/) before
the first start (see import_if_needed, called by db.connect).

What is left out: the raw SEC data (every number of every filing and the raw segment numbers, ~6 GB).
The app only needs it to rebuild a company's numbers. A company from a starter pack is marked in
slim_companies; the first time anything needs its raw data (a new filing, a recalculation, a stock
split) ensure_raw downloads it from the SEC and re-reads its filings, once, then the mark is removed.
"""
import datetime as dt
import os
import sqlite3
import sys
import time
import zipfile

from .config import DATA_DIR, DB_PATH, ROOT

RAW_TABLES = ("xbrl_facts", "segment_facts", "parsed_filings", "news_cache", "holders_cache")   # rebuilt when needed (SEC; news: Yahoo)
SKIP_TABLES = ("sqlite_sequence", "slim_companies")
PRIVATE_PREFIX = "pf_"     # personal portfolio tables (Trading 212): never in a starter pack
EXPORT_DIR = DATA_DIR / "export"
PACK_PREFIX = "stock-terminal-data"


# ------------------------------------------------------------------ export
def export(say=print):
    """Copy the database without the raw tables into a zip. Reads only; the app may keep running."""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    tmp_db = EXPORT_DIR / "terminal.db"
    if tmp_db.exists():
        tmp_db.unlink()
    src = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=60)
    dst = sqlite3.connect(tmp_db)
    dst.execute("PRAGMA journal_mode=OFF")
    dst.execute("PRAGMA synchronous=OFF")
    objs = src.execute("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL").fetchall()
    objs = [o for o in objs if not o[2].startswith(PRIVATE_PREFIX)]   # tbl_name: the table (of an index)
    tables = [n for typ, n, _, _ in objs if typ == "table" and n not in SKIP_TABLES]
    for typ, name, _, sql in objs:                 # same schema, raw tables included (empty)
        if typ == "table" and name not in SKIP_TABLES:
            dst.execute(sql)
    t0 = time.time()
    for t in tables:
        if t in RAW_TABLES:
            continue
        cols = len(src.execute(f'SELECT * FROM "{t}" LIMIT 0').description)
        cur = src.execute(f'SELECT * FROM "{t}"')
        n = 0
        while rows := cur.fetchmany(100_000):
            dst.executemany(f'INSERT INTO "{t}" VALUES ({",".join("?" * cols)})', rows)
            n += len(rows)
        dst.commit()
        say(f"  {t}: {n:,} rows")
    # every company in the pack still has to get its raw SEC data once
    dst.execute("CREATE TABLE slim_companies (cik INTEGER PRIMARY KEY)")
    dst.execute("INSERT INTO slim_companies SELECT DISTINCT cik FROM companies WHERE cik IS NOT NULL")
    dst.commit()
    say("  building indexes")
    for typ, name, _, sql in objs:
        if typ == "index" and not name.startswith("sqlite_autoindex"):
            dst.execute(sql)
    dst.commit()
    dst.execute("VACUUM")
    dst.close()
    src.close()
    out = EXPORT_DIR / f"{PACK_PREFIX}-{dt.date.today().isoformat()}.zip"
    say(f"  compressing ({tmp_db.stat().st_size / 1e6:,.0f} MB)")
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_LZMA) as z:
        z.write(tmp_db, "terminal.db")
    tmp_db.unlink()
    say(f"Starter pack: {out} ({out.stat().st_size / 1e6:,.0f} MB, {time.time() - t0:.0f}s)")
    return out


# ------------------------------------------------------------------ import
def find_pack():
    """Newest starter pack in the Stock Terminal folder or in data/, or None."""
    packs = [p for d in (ROOT, DATA_DIR) if d.exists() for p in d.glob(f"{PACK_PREFIX}*.zip")]
    return max(packs, key=lambda p: p.name) if packs else None


def import_pack(path, say=print):
    if DB_PATH.exists():
        raise RuntimeError(f"{DB_PATH} already exists: a starter pack only goes into an empty data folder")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    say(f"Unpacking starter pack {path.name} (about a minute)...")
    part = DB_PATH.with_suffix(".unpacking")
    with zipfile.ZipFile(path) as z, z.open("terminal.db") as f, open(part, "wb") as out:
        while chunk := f.read(16 << 20):
            out.write(chunk)
    part.replace(DB_PATH)                          # only a complete database ever gets the real name
    say("Starter pack ready.")


def import_if_needed():
    """First start on a new PC: no database yet but a starter pack is there -> unpack it."""
    if DB_PATH.exists():
        return False
    pack = find_pack()
    if not pack:
        return False
    import_pack(pack)
    return True


# ------------------------------------------------------------------ raw data on demand
def is_slim(conn, cik):
    try:
        return cik is not None and conn.execute("SELECT 1 FROM slim_companies WHERE cik=?", (cik,)).fetchone() is not None
    except sqlite3.OperationalError:               # no starter pack used on this PC
        return False


def ensure_raw(conn, ticker, cik):
    """If this company came from a starter pack: download its raw SEC data and re-read its filings now
    (once, ~1 minute). Returns True when it did."""
    if not is_slim(conn, cik):
        return False
    from . import fetch
    print(f"{ticker}: from the starter pack - downloading its raw SEC data once")
    conn.execute("DELETE FROM parsed_filings WHERE cik=? OR cik IN "
                 "(SELECT predecessor_cik FROM predecessors WHERE cik=?)", (cik, cik))
    conn.commit()
    fetch.fetch_ticker(conn, ticker, with_prices=False)
    if conn.execute("SELECT 1 FROM xbrl_facts WHERE cik=? LIMIT 1", (cik,)).fetchone():
        conn.execute("DELETE FROM slim_companies WHERE cik=?", (cik,))
        conn.commit()
        return True
    raise RuntimeError(f"{ticker}: raw SEC data could not be downloaded; the starter-pack numbers are kept")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["export"]:
        export()
    elif args[:1] == ["import"]:
        import_pack(__import__("pathlib").Path(args[1]) if len(args) > 1 else find_pack())
    else:
        print(__doc__)
