"""Command line of the launch wrapper (`insertplay-run`), run with the system python3:

    pull <key> <game folder>                   the wrapper holds the key's lock
    push <key> <game folder> [--locked | --wait SECONDS]
    status <key>

Exit code 3: no sync this time (sync off, wrong card, no strategy, busy); the wrapper then
runs the game directly. Any other result exits 0: the game always starts.
"""
import json
import os
import sys
import time

from . import sync

LOG_LIMIT = 256 * 1024


def _log(paths: sync.Paths, record: dict) -> None:
    """One line per operation in saves/sync.log, without the token (it never is in a record)."""
    path = os.path.join(paths.saves, "sync.log")
    try:
        os.makedirs(paths.saves, exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > LOG_LIMIT:
            os.replace(path, path + ".1")
        line = {k: record.get(k) for k in ("key", "op", "result", "message", "strategy", "hash", "rev")}
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {json.dumps(line, ensure_ascii=False)}\n")
    except OSError:
        pass


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    paths = sync.Paths(os.environ.get("INSERTPLAY_HOME") or None)
    if len(argv) >= 2 and argv[0] == "status":
        print(json.dumps(sync.last_result(paths, argv[1])))
        return 0
    if len(argv) < 3 or argv[0] not in ("pull", "push"):
        print(__doc__, file=sys.stderr)
        return 2
    op, key, root = argv[:3]
    rest = argv[3:]
    root = os.path.realpath(root)
    if op == "pull":
        record = sync.Sync(paths, key, root).pull()
    elif "--locked" in rest:
        record = sync.Sync(paths, key, root).push()
    else:
        wait = 0.0
        if "--wait" in rest:
            try:
                wait = float(rest[rest.index("--wait") + 1])
            except (IndexError, ValueError):
                wait = 0.0
        record = sync.push(paths, key, root, wait)
    _log(paths, record)
    print(json.dumps(record, ensure_ascii=False))
    return 3 if record["result"] in sync.SKIP else 0
