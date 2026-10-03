"""All-time top-N per category. New articles enter only if they beat the current list."""
import datetime as dt
import json
import logging
from pathlib import Path

import requests

log = logging.getLogger("digest")
TEHRAN = dt.timezone(dt.timedelta(hours=3, minutes=30))
UA = "Mozilla/5.0 (compatible; ResearchDigestBot/1.0)"

KEEP = ("id", "url", "source", "kind", "title", "title_fa", "summary", "key_points",
        "why_it_matters", "tags", "read_minutes", "category", "score", "published")


class Leaderboard:
    def __init__(self, root: Path, settings: dict):
        self.path = root / "data" / "leaderboard.json"
        self.board = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.cfg = settings["leaderboard"]
        self.cats = settings["categories"]
        self.today = dt.datetime.now(TEHRAN).date()

    def _expire(self, entries):
        days = self.cfg.get("max_age_days", 0)
        if not days:
            return entries
        cut = (self.today - dt.timedelta(days=days)).isoformat()
        return [e for e in entries if e.get("added", "9999") >= cut]

    def update(self, llm, articles) -> set:
        size, min_score = self.cfg["size"], self.cfg["min_score"]
        entered = set()
        for cat, meta in self.cats.items():
            current = self._expire(self.board.get(cat, []))
            cands = [dict({k: a.get(k) for k in KEEP}, added=self.today.isoformat())
                     for a in articles if a["category"] == cat and a["score"] >= min_score]
            if not cands:
                self.board[cat] = current
                continue
            pool = current + cands
            if len(pool) <= size:
                ranked = sorted(pool, key=lambda e: -e["score"])
            else:
                ranked = self._compare(llm, meta, pool, size)
            self.board[cat] = ranked[:size]
            kept = {e["id"] for e in self.board[cat]}
            entered |= {c["id"] for c in cands if c["id"] in kept}
        log.info("leaderboard: %d new entries", len(entered))
        return entered

    def _compare(self, llm, meta, pool, size):
        """Head-to-head judgement: is the newcomer really better than what is already on the list?"""
        brief = [{"id": e["id"], "source": e["source"], "type": e.get("kind"), "title": e["title"],
                  "summary": e.get("summary", "")[:500], "score": e["score"]} for e in pool]
        system = ("You curate an all-time top-%d reference list for a marketing team, category: %s. "
                  "Judge lasting reference value: depth, evidence, originality, practical applicability. "
                  "Recency is NOT a criterion; a newer item enters only if it is genuinely better." % (size, meta["en"]))
        user = ('Rank these items, best first. Return JSON {"ranking": [ids]} with exactly %d ids.\n\n' % size
                + json.dumps(brief, ensure_ascii=False))
        by_id = {e["id"]: e for e in pool}
        try:
            ids = llm.json(system, user, max_tokens=1500).get("ranking", [])
        except Exception as e:  # noqa: BLE001
            log.warning("leaderboard compare failed (%s) — falling back to scores", e)
            ids = []
        ranked = [by_id[i] for i in dict.fromkeys(ids) if i in by_id]
        for e in sorted(pool, key=lambda x: -x["score"]):
            if e not in ranked:
                ranked.append(e)
        return ranked

    def check_links(self):
        """Drop entries whose page is gone (404/410). Network errors keep the entry."""
        removed = []
        for cat, entries in self.board.items():
            alive = []
            for e in entries:
                try:
                    r = requests.get(e["url"], headers={"User-Agent": UA}, timeout=20, allow_redirects=True, stream=True)
                    gone = r.status_code in (404, 410)
                    r.close()
                except Exception:  # noqa: BLE001
                    gone = False
                (removed if gone else alive).append(e)
            self.board[cat] = alive
        return removed

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.board, ensure_ascii=False, indent=1), encoding="utf-8")
