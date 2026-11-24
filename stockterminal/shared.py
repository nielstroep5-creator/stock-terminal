"""Shared company list: companies.txt in the GitHub repository, one ticker per line.

Everyone who works on the project shares one list of companies. Each person still downloads the numbers
themselves (free, from the SEC / Yahoo); only the list travels through GitHub.

    python -m stockterminal.shared          # sync now: upload new local companies, load new shared ones

- sync() reads the list straight from GitHub (git fetch + git show), so it never touches your working
  copy, your branch or any code changes you have open.
- New local companies are uploaded as a commit that changes only companies.txt, made on top of the
  latest GitHub version and pushed directly (no checkout, no merge). A later `git pull` brings it in.
- The list only grows for now (the app has no "remove company" yet), so two people can never undo
  each other's additions.
- Without git, a GitHub login or push rights (someone who just downloaded the app), the list that came
  with the app is used and nothing is uploaded.
"""
import os
import subprocess
import tempfile

from .config import ROOT

FILE = "companies.txt"
PATH = ROOT / FILE
HEADER = ("# Shared company list of Stock Terminal: one ticker per line.\n"
          "# The app keeps this file up to date (see stockterminal/shared.py); no need to edit it by hand.\n")
_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def parse(text):
    out = set()
    for line in (text or "").splitlines():
        t = line.split("#", 1)[0].strip().upper()
        if t:
            out.add(t)
    return out


def render(tickers):
    return HEADER + "".join(f"{t}\n" for t in sorted(tickers))


def _git(*args, input=None, env=None, timeout=60):
    r = subprocess.run(["git", *args], cwd=ROOT, input=input, capture_output=True, text=True,
                       timeout=timeout, env=env, creationflags=_FLAGS)
    if r.returncode:
        raise RuntimeError(f"git {args[0]}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def _upstream():
    """('origin', 'master') for the current branch, or None when there is no git / no GitHub copy."""
    try:
        up = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").strip()
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return None
    remote, _, branch = up.partition("/")
    return (remote, branch) if branch else None


def remote_list():
    """(tickers on GitHub, (remote, branch, commit)) or (None, None) when GitHub can't be reached."""
    up = _upstream()
    if not up:
        return None, None
    remote, branch = up
    try:
        _git("fetch", "--quiet", remote, branch)
        sha = _git("rev-parse", f"{remote}/{branch}").strip()
        try:
            text = _git("show", f"{sha}:{FILE}")
        except RuntimeError:                       # not on GitHub yet
            text = ""
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return None, None
    return parse(text), (remote, branch, sha)


def _push(tickers, where, added):
    """Commit a new companies.txt on top of the GitHub version and push it, without a checkout."""
    remote, branch, parent = where
    env = dict(os.environ)
    for key, fallback in (("name", "Stock Terminal"), ("email", "stockterminal@localhost")):
        try:
            mine = _git("config", f"user.{key}").strip()
        except (OSError, RuntimeError, subprocess.SubprocessError):
            mine = ""
        if not mine:                              # git without a name/email set up: commit anyway
            env.setdefault(f"GIT_AUTHOR_{key.upper()}", fallback)
            env.setdefault(f"GIT_COMMITTER_{key.upper()}", fallback)
    fd, index = tempfile.mkstemp(prefix="st-index-")
    os.close(fd)
    os.unlink(index)                              # git wants to create the index file itself
    env["GIT_INDEX_FILE"] = index
    try:
        blob = _git("hash-object", "-w", "--stdin", input=render(tickers)).strip()
        _git("read-tree", parent, env=env)
        _git("update-index", "--add", "--cacheinfo", f"100644,{blob},{FILE}", env=env)
        tree = _git("write-tree", env=env).strip()
        names = ", ".join(sorted(added)[:10]) + (f" and {len(added) - 10} more" if len(added) > 10 else "")
        commit = _git("commit-tree", tree, "-p", parent, "-m", f"Shared company list: add {names}", env=env).strip()
        _git("push", "--quiet", remote, f"{commit}:refs/heads/{branch}", timeout=120)
    finally:
        try:
            os.unlink(index)
        except OSError:
            pass


def local_tickers(conn):
    from . import update
    return set(update.all_tickers(conn))


def sync(conn, say=print, load_missing=True):
    """Upload companies that only this PC has; load companies that only the shared list has.
    Never raises: problems are reported through `say` and the next sync tries again."""
    mine = local_tickers(conn)
    shared, where = remote_list()
    if shared is None:                            # offline / no git: the list that came with the app
        try:
            shared = parse(PATH.read_text(encoding="utf-8"))
        except OSError:
            shared = set()
    new_here = mine - shared
    if new_here and where:
        for attempt in range(2):                  # someone else pushed in between: read again, retry once
            try:
                _push(shared | new_here, where, new_here)
                say(f"shared list: uploaded {', '.join(sorted(new_here)[:10])}"
                    + (f" (+{len(new_here) - 10} more)" if len(new_here) > 10 else ""))
                break
            except (OSError, RuntimeError, subprocess.SubprocessError) as e:
                if attempt:
                    say(f"shared list: upload failed ({e})")
                else:
                    shared2, where2 = remote_list()
                    if shared2 is None:
                        say(f"shared list: upload failed ({e})")
                        break
                    shared, where = shared2, where2
                    new_here = mine - shared
                    if not new_here:
                        break
    missing = sorted(shared - mine)
    if missing and load_missing:
        from . import fetch, standardize
        say(f"shared list: loading {len(missing)} new companies: {', '.join(missing[:10])}")
        for t in missing:
            try:
                fetch.fetch_ticker(conn, t)
                standardize.run(conn, [t])
            except Exception as e:  # noqa: BLE001
                say(f"shared list: {t} failed - {e}")
    return dict(uploaded=sorted(new_here) if where else [], loaded=missing if load_missing else [])


if __name__ == "__main__":
    from . import db
    _c = db.connect()
    print(sync(_c))
