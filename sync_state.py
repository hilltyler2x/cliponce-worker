"""Save state/ to the repo without ever losing clips: merge our JSON with whatever other runs pushed meanwhile.
Usage: python sync_state.py "commit message"   (only does anything on GitHub Actions)."""
import json, os, subprocess, sys, time
from pathlib import Path

STATE = Path("state")


def sh(cmd, check=True):
    return subprocess.run(cmd, shell=True, text=True, capture_output=True, check=check)


def remote_json(rel):
    r = sh(f"git show origin/main:{rel}", check=False)
    try:
        return json.loads(r.stdout) if r.returncode == 0 else None
    except Exception:
        return None


def merge_posted(a, b):
    out = dict(a or {})
    for k, v in (b or {}).items():
        if k not in out or (k.endswith("_fails") and isinstance(v, int) and v > out.get(k, 0)) or \
           (not k.endswith(("_fails", "_error")) and isinstance(out.get(k), str) and out[k] == "skip" and v != "skip"):
            out[k] = v
    return out


def merge(name, mine, theirs):
    if theirs is None:
        return mine
    if name == "queue.json":
        by = {q.get("id"): q for q in theirs}
        order = [q.get("id") for q in theirs]
        for q in mine:
            i = q.get("id")
            if i in by:
                m = {**by[i], **q}
                m["posted"] = merge_posted(by[i].get("posted"), q.get("posted"))
                by[i] = m
            else:
                by[i] = q
                order.append(i)
        return [by[i] for i in order][-300:]
    if name == "post_counts.json":
        out = dict(theirs)
        for d, plats in mine.items():
            out[d] = {p: max(n, out.get(d, {}).get(p, 0)) for p, n in {**out.get(d, {}), **plats}.items()}
        return out
    if isinstance(mine, dict) and isinstance(theirs, dict):
        return {**theirs, **mine}
    return mine


def main():
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    msg = sys.argv[1] if len(sys.argv) > 1 else "state"
    sh('git config user.name "cliponce-bot" && git config user.email "cliponce-bot@users.noreply.github.com"')
    mine = {}
    for p in STATE.rglob("*.json"):
        try:
            mine[str(p)] = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            print("skipping unreadable", p)
    for attempt in range(5):
        sh("git fetch -q origin main")
        sh("git reset -q --hard origin/main")
        for rel, data in mine.items():
            merged = merge(Path(rel).name, data, remote_json(rel))
            Path(rel).parent.mkdir(parents=True, exist_ok=True)
            Path(rel).write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
        sh("git add state")
        if sh("git diff --cached --quiet", check=False).returncode == 0:
            print("state: nothing new")
            return
        sh(f"git commit -qm {json.dumps(msg)}")
        if sh("git push -q origin HEAD:main", check=False).returncode == 0:
            print("state saved")
            return
        time.sleep(3 + attempt * 3)
    print("state: push failed after retries")


if __name__ == "__main__":
    main()
