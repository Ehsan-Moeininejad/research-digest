"""
Source registry with self-maintenance and authority tiers:
- tier 1 = primary (original research, company data/engineering blogs, platform announcements)
- tier 2 = credible analysis (top business schools, consultancies, respected practitioners)
- tier 3 = news and vendor content
- new sources start in probation: scored but never published until they prove their quality
- primary sources cited repeatedly by other articles are discovered and added (tier 1, probation)
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
        self.meta.setdefault("log", [])
        self.cfg = cfg
        self.h = settings["sources_health"]
        self.events: list[str] = []

    def event(self, text):
        self.events.append(text)
        self.meta["log"] = (self.meta["log"] + [[TODAY.isoformat(), text]])[-200:]

    # ---------------------------------------------------------------- listing
    def feeds(self):
        out = [dict(f, kind="article", origin="config", tier=int(f.get("tier", 2))) for f in self.cfg.get("feeds", [])]
        out += [dict(f, kind="article", origin=f.get("origin", "scout"), tier=int(f.get("tier", 2))) for f in self.auto]
        return out

    def st(self, src):
        initial = "probation" if src.get("origin", "config") != "config" else "active"
        s = self.state.setdefault(src["url"], {"status": initial, "fails": 0, "scores": [], "picks": 0})
        s.update(name=src["name"], hint=src.get("hint", ""), origin=src.get("origin", "config"),
                 kind=src.get("kind", "article"), tier=int(src.get("tier", 2)))
        return s

    def publishable(self, src_key) -> bool:
        s = self.state.get(src_key)
        return not s or s.get("status") != "probation"

    def should_try(self, s) -> bool:
        if s["status"] == "paused":
            if s.get("until", "") <= TODAY.isoformat():
                s["status"], s["scores"], s["rev"], s["acc"] = "active", [], 0, 0
                self.event(f"منبع {s['name']} بعد از توقف کیفی دوباره فعال شد")
                return True
            return False
        if s["status"] == "rejected":
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
                self.event(f"آدرس فید {src['name']} خودکار اصلاح شد")
                return self._ok(src, s, parsed, since, to_items)
        s["fails"] = s.get("fails", 0) + 1
        log.warning("FEED FAILED  %-28s %s (fail #%d)", src["name"], err, s["fails"])
        if s["fails"] >= self.h["disable_after_failures"] and s["status"] not in ("disabled", "probation"):
            s["prev_status"] = s["status"]
            s["status"] = "disabled"
            self.event(f"منبع {src['name']} بعد از {s['fails']} خطای پیاپی غیرفعال شد")
        return []

    def _ok(self, src, s, parsed, since, to_items):
        if s["status"] == "disabled":
            s["status"] = s.pop("prev_status", "active")
            self.event(f"منبع {src['name']} دوباره در دسترس است و فعال شد")
        s.update(fails=0, last_ok=TODAY.isoformat())
        dates = [d for d in (entry_date(e) for e in parsed.entries) if d]
        if dates:
            s["last_item"] = max(dates).date().isoformat()
        items = to_items(parsed, src, since)
        for it in items:
            it["src_key"] = src["url"]
            it["tier"] = int(src.get("tier", 2))
        log.info("feed ok      %-28s %d items%s", src["name"], len(items), " (probation)" if s["status"] == "probation" else "")
        return items

    # ---------------------------------------------------------------- quality signals
    def record_scores(self, items):
        for it in items:
            s = self.state.get(it.get("src_key"))
            if s is not None:
                s["scores"] = (s.get("scores", []) + [round(it.get("quality", 0), 1)])[-60:]

    def record_reviews(self, items, ok_ids):
        for it in items:
            s = self.state.get(it.get("src_key"))
            if s is not None:
                s["rev"] = s.get("rev", 0) + 1
                s["acc"] = s.get("acc", 0) + (1 if it["id"] in ok_ids else 0)

    def record_picks(self, articles):
        for a in articles:
            s = self.state.get(a.get("src_key"))
            if s is not None:
                s["picks"] = s.get("picks", 0) + 1
                s["last_pick"] = TODAY.isoformat()

    def evaluate_quality(self):
        h = self.h
        for key, s in list(self.state.items()):
            if key == "_meta" or s.get("kind") == "paper":
                continue
            sc = s.get("scores", [])
            avg = sum(sc) / len(sc) if sc else 0
            if s.get("status") == "probation":
                if len(sc) >= h.get("probation_min_items", 10):
                    if avg >= h.get("probation_promote_avg", 7):
                        s["status"] = "active"
                        self.event(f"منبع {s['name']} دورهٔ آزمایشی را با میانگین کیفیت {avg:.1f} گذراند و فعال شد")
                    elif avg < h.get("probation_reject_avg", 5):
                        s["status"] = "rejected"
                        self.auto = [a for a in self.auto if a["url"] != key]
                        self.event(f"منبع {s['name']} در دورهٔ آزمایشی با میانگین {avg:.1f} رد شد")
                continue
            if s.get("status") != "active":
                continue
            rev, acc = s.get("rev", 0), s.get("acc", 0)
            low_q = len(sc) >= h["min_scored"] and avg < h["low_quality_avg"]
            low_acc = rev >= h["min_scored"] and acc / rev < h.get("min_acceptance", 0.05)
            if low_q or low_acc:
                s["status"] = "paused"
                s["until"] = (TODAY + dt.timedelta(days=30)).isoformat()
                why = f"میانگین کیفیت {avg:.1f}" if low_q else f"نرخ قبولی {100 * acc / rev:.0f}%"
                self.event(f"منبع {s['name']} به دلیل {why} برای 30 روز متوقف شد")

    # ---------------------------------------------------------------- growing the source list
    def _known_domains(self):
        known = set()
        for f in self.feeds():
            known.add(domain(f["url"]))
            known.add(domain(self.state.get(f["url"], {}).get("resolved_url", "")))
        known |= {domain(k) for k, v in self.state.items() if k != "_meta" and v.get("status") == "rejected"}
        return {d for d in known if d}

    def _try_add(self, name, homepage, cat, tier, origin, kind_type=None):
        recent_cut = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=45)
        for u in [homepage] + discover_feeds(homepage):
            try:
                parsed = parse_feed(u)
            except Exception:  # noqa: BLE001
                continue
            recent = [e for e in parsed.entries if (entry_date(e) or recent_cut) > recent_cut]
            if len(parsed.entries) >= 3 and recent:
                entry = {"name": str(name)[:40], "url": u, "hint": cat, "tier": tier, "origin": origin,
                         "added": TODAY.isoformat()}
                if kind_type == "tutorial":
                    entry["type"] = "tutorial"
                self.auto.append(entry)
                log.info("SOURCE ADDED (probation) %s → %s", entry["name"], u)
                self.event(f"منبع جدید در دورهٔ آزمایشی: {entry['name']} (ردهٔ {tier})")
                return True
        return False

    def note_primary(self, url, category):
        """Remember domains that other articles cite as the original source."""
        d = domain(url)
        if not d:
            return
        pm = self.meta.setdefault("primary_cites", {})
        e = pm.setdefault(d, {"dates": [], "cat": category, "home": f"{urlparse(url).scheme}://{urlparse(url).netloc}/"})
        e["dates"] = (e["dates"] + [TODAY.isoformat()])[-20:]
        e["cat"] = category

    def discover_primary_sources(self, categories):
        """Primary sources cited repeatedly in the last 30 days become tier-1 candidates (probation)."""
        pm = self.meta.get("primary_cites", {})
        known = self._known_domains()
        cut = (TODAY - dt.timedelta(days=30)).isoformat()
        added = 0
        for d, e in sorted(pm.items(), key=lambda kv: -len(kv[1]["dates"])):
            if added >= self.h.get("primary_discover_per_run", 2):
                break
            recent = [x for x in e["dates"] if x >= cut]
            if d in known or len(recent) < self.h.get("primary_discover_min", 2) or e.get("tried"):
                continue
            e["tried"] = TODAY.isoformat()
            cat = e["cat"] if e["cat"] in categories else "marketing"
            if self._try_add(d, e["home"], cat, 1, "primary"):
                added += 1

    def scout(self, llm, settings):
        if days_since(self.meta.get("last_scout")) < self.h["scout_every_days"]:
            return
        self.meta["last_scout"] = TODAY.isoformat()
        self.discover_primary_sources(settings["categories"])
        cats = settings["categories"]
        existing = self._known_domains()
        healthy = {k: 0 for k in cats}
        for f in self.feeds():
            s = self.state.get(f["url"], {})
            if s.get("status", "active") == "active" and f.get("hint") in healthy and f.get("tier", 2) <= 2:
                healthy[f["hint"]] += 1
        system = ("You are a research librarian curating authoritative sources for a marketing team. "
                  "Only propose sources you are confident exist and are actively publishing.")
        user = (
            f"Scope:\n{settings['scope']}\n\n"
            f"Healthy tier-1/2 sources per category: {json.dumps(healthy)}\n"
            f"Domains already used or rejected (never repeat): {sorted(existing)}\n\n"
            "Propose up to 10 additional sources, prioritising categories with the fewest sources and, above all, "
            "PRIMARY sources: company data science / engineering / growth blogs that publish their own experiments, "
            "academic marketing research groups and journals with free access, platform announcement blogs, "
            "original industry research. Respected practitioner analysis is second choice. "
            "Never propose aggregators, news sites, vendor marketing blogs or SEO content farms.\n"
            'Return a JSON array: [{"name": "...", "homepage": "https://...", '
            f'"category": one of {list(cats)}, "tier": 1 or 2, "type": "tutorial" or "insight"}}]'
        )
        try:
            proposals = llm.json(system, user, max_tokens=3000)
        except Exception as e:  # noqa: BLE001
            log.warning("scout failed: %s", e)
            return
        added = 0
        for pr in proposals if isinstance(proposals, list) else []:
            if added >= self.h["scout_max_new"]:
                break
            hp, cat = str(pr.get("homepage", "")), pr.get("category")
            if not hp.startswith("http") or domain(hp) in existing or cat not in cats:
                continue
            tier = 1 if pr.get("tier") == 1 else 2
            if self._try_add(pr.get("name", domain(hp)), hp, cat, tier, "scout", pr.get("type")):
                existing.add(domain(hp))
                added += 1

    # ---------------------------------------------------------------- reporting
    def rows(self, arxiv_sources):
        rows = []
        for f in self.feeds() + list(arxiv_sources):
            s = self.state.get(f["url"], {})
            sc = s.get("scores", [])
            rev, acc = s.get("rev", 0), s.get("acc", 0)
            rows.append({
                "name": f["name"], "hint": f.get("hint", ""), "origin": f.get("origin", "config"),
                "tier": int(f.get("tier", 2)), "status": s.get("status", "new"), "last_item": s.get("last_item", ""),
                "avg": round(sum(sc) / len(sc), 1) if sc else None, "picks": s.get("picks", 0),
                "acc_rate": round(100 * acc / rev) if rev else None, "reviewed": rev,
                "suggest": self._suggest(int(f.get("tier", 2)), sc, rev, acc),
                "repaired": bool(s.get("resolved_url")), "kind": f.get("kind", "article"),
            })
        order = {"active": 0, "probation": 1, "new": 2, "paused": 3, "disabled": 4, "rejected": 5}
        return sorted(rows, key=lambda r: (order.get(r["status"], 9), r["tier"], -(r["picks"] or 0), r["name"]))

    def _suggest(self, tier, scores, rev, acc):
        """A tier-change hint for the team (never applied automatically: tier describes what a source IS)."""
        if rev < self.h.get("suggest_min_reviewed", 15) or not scores:
            return ""
        avg, rate = sum(scores) / len(scores), acc / rev
        if tier < 3 and rate < 0.10 and avg < 6:
            return "بررسی کاهش رده"
        if tier == 3 and rate >= 0.40 and avg >= 7.5:
            return "بررسی افزایش رده"
        return ""

    def monthly_report(self, arxiv_sources):
        """Every N days (weekly by default): the source table, tier hints and everything that changed, for the team to review."""
        if days_since(self.meta.get("last_report")) < self.h.get("report_every_days", 30):
            return None
        since = self.meta.get("last_report") or "0000"
        self.meta["last_report"] = TODAY.isoformat()
        changes = [t for d, t in self.meta.get("log", []) if d > since]
        return {"rows": self.rows(arxiv_sources), "changes": changes}

    def save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        self.auto_path.write_text(json.dumps(self.auto, ensure_ascii=False, indent=1), encoding="utf-8")
