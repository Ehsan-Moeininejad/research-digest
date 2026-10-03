"""
Source registry with self-maintenance:
- broken feed  → auto-discover a new feed URL from the site's homepage (repair)
- keeps failing → disabled, retried weekly, re-enabled when it works again
- consistently low quality → paused for 30 days
- weekly scout → LLM proposes new sources, each one verified live before it is added
"""
import datetime as dt
import html
import json
import logging
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser
import requests

log = logging.getLogger("digest")
UA = "Mozilla/5.0 (compatible; ResearchDigestBot/1.0; +https://github.com)"
TEHRAN = dt.timezone(dt.timedelta(hours=3, minutes=30))
TODAY = dt.datetime.now(TEHRAN).date()


def domain(url: str) -> str:
    d = urlparse(url or "").netloc.lower()
    return d[4:] if d.startswith("www.") else d


def days_since(iso: str | None) -> int:
    if not iso:
        return 10_000
    return (TODAY - dt.date.fromisoformat(iso[:10])).days


def entry_date(e):
    for key in ("published_parsed", "updated_parsed"):
        t = e.get(key)
        if t:
            return dt.datetime(*t[:6], tzinfo=dt.timezone.utc)
    return None


def parse_feed(url: str, allow_empty: bool = False):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
    r.raise_for_status()
    parsed = feedparser.parse(r.content)
    if not parsed.entries and not allow_empty:
        raise ValueError("feed has no entries")
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"not a feed: {parsed.bozo_exception}")
    return parsed


def discover_feeds(url: str) -> list[str]:
    """Find RSS/Atom URLs advertised on the site's homepage, plus common feed paths."""
    p = urlparse(url)
    home = f"{p.scheme}://{p.netloc}/"
    found = []
    try:
        r = requests.get(home, headers={"User-Agent": UA}, timeout=20)
        for m in re.finditer(r"<link[^>]+>", r.text[:300_000], re.I):
            tag = m.group(0)
            if re.search(r"(rss|atom)\+xml", tag, re.I):
                h = re.search(r"href=[\"']([^\"']+)", tag, re.I)
                if h:
                    found.append(urljoin(home, html.unescape(h.group(1))))
    except Exception:  # noqa: BLE001
        pass
    for suffix in ("feed", "feed/", "rss", "rss/", "rss.xml", "feed.xml", "atom.xml", "index.xml", "blog/feed", "blog/rss"):
        found.append(urljoin(home, suffix))
    out = []
    for u in found:
        if u not in out and u != url:
            out.append(u)
    return out[:12]


class SourceRegistry:
    def __init__(self, root: Path, cfg: dict, settings: dict):
        self.state_path = root / "data" / "sources_state.json"
        self.auto_path = root / "data" / "sources_auto.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        self.auto = json.loads(self.auto_path.read_text()) if self.auto_path.exists() else []
        self.meta = self.state.setdefault("_meta", {})
        self.cfg = cfg
        self.h = settings["sources_health"]
        self.events: list[str] = []

    # ---------------------------------------------------------------- listing
    def feeds(self):
        out = [dict(f, kind="article", origin="config") for f in self.cfg.get("feeds", [])]
        out += [dict(f, kind="article", origin="scout") for f in self.auto]
        return out

    def st(self, src):
        s = self.state.setdefault(src["url"], {"status": "active", "fails": 0, "scores": [], "picks": 0})
        s.update(name=src["name"], hint=src.get("hint", ""), origin=src.get("origin", "config"), kind=src.get("kind", "article"))
        return s

    def should_try(self, s) -> bool:
        if s["status"] == "paused":
            if s.get("until", "") <= TODAY.isoformat():
                s["status"], s["scores"] = "active", []
                self.events.append(f"منبع {s['name']} بعد از توقف کیفی دوباره فعال شد")
                return True
            return False
        if s["status"] == "disabled":
            return days_since(s.get("last_try")) >= 7
        return True

    # ---------------------------------------------------------------- fetching
    def fetch(self, src, since, to_items):
        """Fetch one source with automatic repair. Returns a list of items (possibly empty)."""
        s = self.st(src)
        if not self.should_try(s):
            return []
        s["last_try"] = TODAY.isoformat()
        is_paper = src.get("kind") == "paper"
        urls = [s.get("resolved_url") or src["url"]]
        if s.get("resolved_url"):
            urls.append(src["url"])
        err = None
        for u in urls:
            try:
                return self._ok(src, s, parse_feed(u, allow_empty=is_paper), since, to_items)
            except Exception as e:  # noqa: BLE001
                err = e
        if not is_paper:
            for u in discover_feeds(src["url"]):
                try:
                    parsed = parse_feed(u)
                except Exception:  # noqa: BLE001
                    continue
                s["resolved_url"] = u
                log.info("FEED REPAIRED %-26s → %s", src["name"], u)
                self.events.append(f"آدرس فید {src['name']} خودکار اصلاح شد")
                return self._ok(src, s, parsed, since, to_items)
        s["fails"] = s.get("fails", 0) + 1
        log.warning("FEED FAILED  %-28s %s (fail #%d)", src["name"], err, s["fails"])
        if s["fails"] >= self.h["disable_after_failures"] and s["status"] != "disabled":
            s["status"] = "disabled"
            self.events.append(f"منبع {src['name']} بعد از {s['fails']} خطای پیاپی غیرفعال شد")
        return []

    def _ok(self, src, s, parsed, since, to_items):
        if s["status"] == "disabled":
            self.events.append(f"منبع {src['name']} دوباره در دسترس است و فعال شد")
        s.update(status="active", fails=0, last_ok=TODAY.isoformat())
        dates = [d for d in (entry_date(e) for e in parsed.entries) if d]
        if dates:
            s["last_item"] = max(dates).date().isoformat()
        items = to_items(parsed, src, since)
        for it in items:
            it["src_key"] = src["url"]
        log.info("feed ok      %-28s %d items", src["name"], len(items))
        return items

    # ---------------------------------------------------------------- quality
    def record_scores(self, items):
        for it in items:
            s = self.state.get(it.get("src_key"))
            if s is not None:
                s["scores"] = (s.get("scores", []) + [round(it.get("quality", 0), 1)])[-60:]

    def record_picks(self, articles):
        for a in articles:
            s = self.state.get(a.get("src_key"))
            if s is not None:
                s["picks"] = s.get("picks", 0) + 1
                s["last_pick"] = TODAY.isoformat()

    def evaluate_quality(self):
        for key, s in self.state.items():
            if key == "_meta" or s.get("kind") == "paper" or s.get("status") != "active":
                continue
            sc = s.get("scores", [])
            if len(sc) >= self.h["min_scored"]:
                avg = sum(sc) / len(sc)
                if avg < self.h["low_quality_avg"]:
                    s["status"] = "paused"
                    s["until"] = (TODAY + dt.timedelta(days=30)).isoformat()
                    self.events.append(f"منبع {s['name']} به دلیل میانگین کیفیت {avg:.1f} برای 30 روز متوقف شد")

    # ---------------------------------------------------------------- scouting
    def scout(self, llm, settings):
        if days_since(self.meta.get("last_scout")) < self.h["scout_every_days"]:
            return
        self.meta["last_scout"] = TODAY.isoformat()
        cats = settings["categories"]
        existing = set()
        healthy = {k: 0 for k in cats}
        for f in self.feeds():
            existing.add(domain(f["url"]))
            s = self.state.get(f["url"], {})
            existing.add(domain(s.get("resolved_url", "")))
            if s.get("status", "active") == "active" and f.get("hint") in healthy:
                healthy[f["hint"]] += 1
        system = ("You are a research librarian curating sources for a marketing team. "
                  "Only propose sources you are confident exist and are actively publishing.")
        user = (
            f"Scope:\n{settings['scope']}\n\n"
            f"Healthy sources per category: {json.dumps(healthy)}\n"
            f"Domains already used (never repeat): {sorted(d for d in existing if d)}\n\n"
            "Propose up to 10 additional sources, prioritising categories with the fewest sources. "
            "Criteria: consistently excellent, evidence-based or deeply practical content; free to read; "
            "English; publishing regularly in 2025-2026. Good: company data/engineering blogs on marketing "
            "problems, academic marketing groups, respected practitioner newsletters. "
            "Bad: aggregators, vendor press releases, SEO content farms.\n"
            'Return a JSON array: [{"name": "...", "homepage": "https://...", '
            f'"category": one of {list(cats)}, "type": "tutorial" or "insight"}}]'
        )
        try:
            proposals = llm.json(system, user, max_tokens=3000)
        except Exception as e:  # noqa: BLE001
            log.warning("scout failed: %s", e)
            return
        added = 0
        recent_cut = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=45)
        for p in proposals if isinstance(proposals, list) else []:
            if added >= self.h["scout_max_new"]:
                break
            hp, cat = p.get("homepage", ""), p.get("category")
            if not hp.startswith("http") or domain(hp) in existing or cat not in cats:
                continue
            for u in [hp] + discover_feeds(hp):
                try:
                    parsed = parse_feed(u)
                except Exception:  # noqa: BLE001
                    continue
                recent = [e for e in parsed.entries if (entry_date(e) or recent_cut) > recent_cut]
                if len(parsed.entries) >= 3 and recent:
                    entry = {"name": str(p.get("name", domain(hp)))[:40], "url": u, "hint": cat, "added": TODAY.isoformat()}
                    if p.get("type") == "tutorial":
                        entry["type"] = "tutorial"
                    self.auto.append(entry)
                    existing.add(domain(hp))
                    added += 1
                    log.info("SOURCE ADDED  %s → %s", entry["name"], u)
                    self.events.append(f"منبع جدید اضافه شد: {entry['name']}")
                    break

    # ---------------------------------------------------------------- reporting
    def rows(self, arxiv_sources):
        rows = []
        for f in self.feeds() + list(arxiv_sources):
            s = self.state.get(f["url"], {})
            sc = s.get("scores", [])
            rows.append({
                "name": f["name"], "hint": f.get("hint", ""), "origin": f.get("origin", "config"),
                "status": s.get("status", "new"), "last_item": s.get("last_item", ""),
                "avg": round(sum(sc) / len(sc), 1) if sc else None, "picks": s.get("picks", 0),
                "repaired": bool(s.get("resolved_url")), "kind": f.get("kind", "article"),
            })
        order = {"active": 0, "new": 1, "paused": 2, "disabled": 3}
        return sorted(rows, key=lambda r: (order.get(r["status"], 9), -(r["picks"] or 0), r["name"]))

    def save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        self.auto_path.write_text(json.dumps(self.auto, ensure_ascii=False, indent=1), encoding="utf-8")
