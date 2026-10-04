#!/usr/bin/env python3
"""Jev relevance ranking for codebase exploration.

Stdlib only (Python >= 3.9). Talks to TypeSafe's System One endpoint:
https://docs.typesafe.ai/api

Commands:
  check                   verify the API key and reachability
  rank  QUERY [CAND...]   score candidates (path or path:line) against QUERY;
                          with no CAND args, reads them from stdin, one per line
  lines QUERY FILE        point to the lines of FILE that answer QUERY

Key lookup order: $TYPESAFE_API_KEY, then ~/.config/typesafe/api_key.
Exit code 2 means "jev is not configured / unreachable": callers fall back
to plain search.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE_URL = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/")
MODEL = os.environ.get("JEV_MODEL", "jev-latest")
KEY_FILE = os.path.expanduser("~/.config/typesafe/api_key")
EXCERPT_CHARS = 12000  # keeps state well under Jev's 32k-token state budget
LINE_CHARS = 300
WINDOW = 250  # Choice questions accept at most 255 options
EXIT_UNCONFIGURED = 2


class JevUnavailable(Exception):
    pass


def api_key():
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not key and os.path.isfile(KEY_FILE):
        with open(KEY_FILE) as f:
            key = f.read().strip()
    if not key:
        raise JevUnavailable(
            "no API key: export TYPESAFE_API_KEY or write it to " + KEY_FILE
        )
    return key


def request(method, path, body=None, retries=4):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE_URL + path,
        data=data,
        method=method,
        headers={
            "Authorization": "Bearer " + api_key(),
            "Content-Type": "application/json",
            "User-Agent": "jev-explore/1.0",
        },
    )
    delay = 1.0
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 529) and attempt < retries:
                wait = float(e.headers.get("retry-after") or delay)
                time.sleep(wait)
                delay *= 2
                continue
            detail = e.read().decode(errors="replace")[:300]
            if e.code in (401, 403):
                raise JevUnavailable("API key rejected (%d): %s" % (e.code, detail))
            raise RuntimeError("HTTP %d: %s" % (e.code, detail))
        except (urllib.error.URLError, OSError) as e:  # incl. timeouts
            raise JevUnavailable("cannot reach %s: %s" % (BASE_URL, getattr(e, "reason", e)))


def system_one(state, questions):
    return request(
        "POST", "/v1/systemone", {"state": state, "model": MODEL, "questions": questions}
    )["answers"]


# ---------------------------------------------------------------- excerpts


def read_lines(path):
    with open(path, "rb") as f:
        raw = f.read()
    if b"\0" in raw[:8192]:
        return None  # binary
    return raw.decode("utf-8", errors="replace").splitlines()


def excerpt(candidate, context, head):
    path, line = candidate, None
    if ":" in candidate:
        maybe_path, _, rest = candidate.partition(":")
        num = rest.split(":", 1)[0]
        if num.isdigit() and os.path.isfile(maybe_path):
            path, line = maybe_path, int(num)
    if not os.path.isfile(path):
        return None
    lines = read_lines(path)
    if lines is None:
        return None
    if line is None:
        start, end = 1, min(len(lines), head)
    else:
        start, end = max(1, line - context), min(len(lines), line + context)
    body = "\n".join(
        "%d| %s" % (n, lines[n - 1][:LINE_CHARS]) for n in range(start, end + 1)
    )
    return {"path": path, "lines": "%d-%d" % (start, end), "body": body[:EXCERPT_CHARS]}


# ---------------------------------------------------------------- commands


def cmd_check(_args):
    models = request("GET", "/v1/models").get("models", [])
    names = ", ".join(m.get("name", "?") for m in models) or "none listed"
    print("ok: key accepted at %s; model=%s; available: %s" % (BASE_URL, MODEL, names))


def relevance_question(query):
    return {
        "type": "noul",
        "instructions": {
            "query": query,
            "question": "Does `excerpt` contain code or text that answers `query`, "
            "or that someone answering `query` must read?",
        },
        "criteria": {
            "true": "The excerpt defines, implements, configures, or directly "
            "handles what the query asks about",
            "false": "The excerpt only shares words or a general topic with the query",
        },
    }


def cmd_rank(args):
    cands = list(args.candidates)
    if not cands and not sys.stdin.isatty():
        cands = [c.strip() for c in sys.stdin if c.strip()]
    seen, pool = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c)
            pool.append(c)
    if len(pool) > args.max:
        print("note: %d candidates, scoring the first %d" % (len(pool), args.max),
              file=sys.stderr)
        pool = pool[: args.max]

    question = relevance_question(args.query)

    def score(cand):
        ex = excerpt(cand, args.context, args.head)
        if ex is None:
            return cand, None, "unreadable"
        state = {"path": ex["path"], "lines": ex["lines"], "excerpt": ex["body"]}
        try:
            ans = system_one(state, {"relevant": question})
            return cand, ans["relevant"]["noul"], ex["lines"]
        except JevUnavailable:
            raise
        except Exception as e:  # one bad candidate must not sink the batch
            return cand, None, "error: %s" % e

    with ThreadPoolExecutor(max_workers=args.workers) as pool_exec:
        results = list(pool_exec.map(score, pool))

    scored = sorted((r for r in results if r[1] is not None), key=lambda r: -r[1])
    shown = [r for r in scored if r[1] >= args.min][: args.top]
    for cand, noul, span in shown:
        print("%.2f\t%s\t(lines %s)" % (noul, cand, span))
    hidden = len(scored) - len(shown)
    failed = [r for r in results if r[1] is None]
    print("-- %d scored, %d shown, %d below cutoff, %d skipped"
          % (len(scored), len(shown), hidden, len(failed)), file=sys.stderr)
    for cand, _, why in failed[:10]:
        print("   skipped %s: %s" % (cand, why), file=sys.stderr)


def cmd_lines(args):
    lines = read_lines(args.file)
    if lines is None:
        sys.exit("binary file: " + args.file)
    hits = []
    for start in range(0, len(lines), WINDOW):
        window = lines[start : start + WINDOW]
        ids = ["L%d" % (start + i + 1) for i in range(len(window))]
        doc = "\n".join("%s| %s" % (i, l[:LINE_CHARS]) for i, l in zip(ids, window))
        answers = system_one(doc, {
            "where": {
                "type": "choice",
                "instructions": 'Which line of the document best answers: "%s"?'
                % args.query,
                "criteria": {i: None for i in ids},
            },
            "exists": {
                "type": "noul",
                "instructions": 'Does any line of the document address or answer: "%s"?'
                % args.query,
                "criteria": {
                    "true": "At least one line states, implements, or directly "
                    "handles the answer",
                    "false": "No line addresses this",
                },
            },
        })
        exists = answers["exists"]["noul"]
        for lid, p in answers["where"]["probabilities"].items():
            hits.append((exists * p, exists, p, int(lid[1:])))
    hits.sort(reverse=True)
    for combined, exists, p, n in hits[: args.top]:
        if combined < 0.01:
            break
        print("%.2f\t%s:%d\t(exists %.2f, line %.2f)\t%s"
              % (combined, args.file, n, exists, p, lines[n - 1].strip()[:100]))
    best = hits[0][1] if hits else 0.0
    verdict = ("answered" if best >= 0.7 else "absent" if best < 0.35 else "partial")
    print("-- verdict: %s in this file (best exists %.2f)" % (verdict, best),
          file=sys.stderr)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="verify key and connectivity")

    r = sub.add_parser("rank", help="score candidates against a query")
    r.add_argument("query")
    r.add_argument("candidates", nargs="*", help="path or path:line (stdin too)")
    r.add_argument("--top", type=int, default=15)
    r.add_argument("--min", type=float, default=0.05, help="hide scores below this")
    r.add_argument("--max", type=int, default=200, help="cap on candidates scored")
    r.add_argument("--context", type=int, default=20, help="lines around path:line")
    r.add_argument("--head", type=int, default=80, help="lines read from a bare path")
    r.add_argument("--workers", type=int, default=8)

    l = sub.add_parser("lines", help="find the lines of one file answering a query")
    l.add_argument("query")
    l.add_argument("file")
    l.add_argument("--top", type=int, default=8)

    args = p.parse_args()
    try:
        {"check": cmd_check, "rank": cmd_rank, "lines": cmd_lines}[args.cmd](args)
    except JevUnavailable as e:
        print("jev unavailable: %s" % e, file=sys.stderr)
        sys.exit(EXIT_UNCONFIGURED)


if __name__ == "__main__":
    main()
