"""
Daily Research Digest
sources (self-maintaining) → dedupe → LLM triage (relevance + quality) → shortlist
→ full-text editorial review → balanced top 10 → Persian summaries → leaderboard
→ static site (GitHub Pages) → email
"""
import datetime as dt
import hashlib
import html
import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from leaderboard import Leaderboard  # noqa: E402
from llm import LLM  # noqa: E402
from notify import send_email  # noqa: E402
from render import render_site  # noqa: E402
from sources import SourceRegistry, days_since, entry_date  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TEHRAN = dt.timezone(dt.timedelta(hours=3, minutes=30))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("digest")


# ------------------------------------------------------------------ helpers
def load_yaml(name):
    return yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))


def clean_text(raw: str, limit: int = 600) -> str:
    txt = re.sub(r"<[^>]+>", " ", raw or "")
    txt = html.unescape(re.sub(r"\s+", " ", txt)).strip()
    return txt[:limit]


def uid(url: str) -> str:
    url = re.sub(r"[?#].*$", "", url.strip().lower()).rstrip("/")
    url = re.sub(r"arxiv\.org/abs/([\d.]+)v\d+", r"arxiv.org/abs/\1", url)
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def to_items(parsed, src, since):
    items = []
    for e in parsed.entries[:40]:
        link, published = e.get("link"), entry_date(e)
        if not link or not e.get("title"):
            continue
        if published and published < since:
            continue
        summary = e.get("summary") or ""
        if not summary and e.get("content"):
            summary = e["content"][0].get("value", "")
        items.append({
            "id": uid(link), "title": clean_text(e.title, 300), "url": link,
            "source": src["name"], "hint": src.get("hint", ""), "kind": src.get("kind", "article"),
            "hint_kind": src.get("type", ""), "snippet": clean_text(summary, 700),
            "published": (published or dt.datetime.now(dt.timezone.utc)).isoformat(),
        })
    return items


def arxiv_sources(cfg):
    n = cfg.get("arxiv_max_results", 25)
    return [{"name": q["name"], "hint": q.get("hint", ""), "kind": "paper", "origin": "config",
             "url": "http://export.arxiv.org/api/query?search_query=" + quote(q["query"]) +
                    f"&sortBy=submittedDate&sortOrder=descending&max_results={n}"}
            for q in cfg.get("arxiv", [])]


def collect(reg: SourceRegistry, arxiv, since):
    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(lambda s: reg.fetch(s, since, to_items), reg.feeds()))
    for s in arxiv:  # arXiv asks for ~3s between API calls
        results.append(reg.fetch(s, since, to_items))
        time.sleep(3)
    seen, items = set(), []
    for batch in results:
        for it in batch:
            if it["id"] not in seen:
                seen.add(it["id"])
                items.append(it)
    return items


# ------------------------------------------------------------------ stage 1: triage
def triage(llm, items, settings):
    cats = settings["categories"]
    cat_list = "\n".join(f"- {k}: {v['en']}" for k, v in cats.items())
    system = (
        "You are the research editor for this audience:\n"
        f"{settings['audience']}\n\nIn-scope topics:\n{settings['scope']}\n\n"
        "Give each item two scores (0-10):\n"
        "relevance = how central the piece is to the in-scope marketing topics. Off-topic or generic "
        "pieces (pure ML infrastructure, politics, finance, HR, consumer gadgets) score 0-3 even if excellent.\n"
        "quality = original insight, evidence (experiments, data, real company case studies), rigour, "
        "practical depth, credible author. Promotional posts, product announcements without insight, "
        "listicles and news recaps score 0-3.\n\n"
        f"Category key (exactly one):\n{cat_list}\n\n"
        'Type: "tutorial" (hands-on, reproducible steps, playbooks, code), "paper" (research), '
        'or "insight" (analysis, case study, essay).'
    )
    out = {}
    for i in range(0, len(items), 60):
        chunk = items[i: i + 60]
        payload = [{"id": it["id"], "source": it["source"], "hint": it["hint"],
                    "title": it["title"], "snippet": it["snippet"][:350]} for it in chunk]
        user = ('Return a JSON array: [{"id": "...", "relevance": 0-10, "quality": 0-10, '
                '"category": "<key>", "type": "tutorial|paper|insight"}]\n\n' + json.dumps(payload, ensure_ascii=False))
        for r in llm.json(system, user, max_tokens=9000):
            if isinstance(r, dict) and r.get("id"):
                out[r["id"]] = r
        log.info("triaged %d/%d", min(i + 60, len(items)), len(items))
    for it in items:
        r = out.get(it["id"], {})
        it["relevance"] = float(r.get("relevance", 0) or 0)
        it["quality"] = float(r.get("quality", 0) or 0)
        it["pre_score"] = round(0.4 * it["relevance"] + 0.6 * it["quality"], 2)
        cat = r.get("category") or it["hint"]
        it["category"] = cat if cat in cats else (it["hint"] if it["hint"] in cats else "marketing")
        ctype = r.get("type")
        if it["kind"] != "paper" and ctype in ("tutorial", "insight"):
            it["kind"] = "tutorial" if ctype == "tutorial" else "article"
        if it.get("hint_kind") == "tutorial" and ctype != "insight" and it["kind"] != "paper":
            it["kind"] = "tutorial"
    return items


# ------------------------------------------------------------------ selection
def select(items, n, sel, categories, key, min_tutorials=0):
    """Balanced pick: best per category → a few tutorials → fill by score; max N per source."""
    pool = sorted(items, key=lambda x: x[key], reverse=True)
    picked, ids, per_source = [], set(), {}

    def ok(it):
        return it["id"] not in ids and per_source.get(it["source"], 0) < sel["max_per_source"]

    def take(it):
        picked.append(it)
        ids.add(it["id"])
        per_source[it["source"]] = per_source.get(it["source"], 0) + 1

    for cat in categories:
        if len(picked) >= n:
            break
        for it in pool:
            if it["category"] == cat and ok(it):
                take(it)
                break
    t = sum(1 for p in picked if p["kind"] == "tutorial")
    for it in pool:
        if t >= min_tutorials or len(picked) >= n:
            break
        if it["kind"] == "tutorial" and ok(it):
            take(it)
            t += 1
    for it in pool:
        if len(picked) >= n:
            break
        if ok(it):
            take(it)
    return picked


def fetch_fulltext(it):
    if it["kind"] == "paper" or it.get("body"):
        it.setdefault("body", it["snippet"])
        return it
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(it["url"])
        text = trafilatura.extract(downloaded, include_comments=False, include_tables=False) if downloaded else None
    except Exception:  # noqa: BLE001
        text = None
    it["body"] = (text or it["snippet"])[:7000]
    it["fulltext"] = bool(text and len(text) > 800)
    return it


# ------------------------------------------------------------------ stage 2: editorial review
def review(llm, items, settings):
    rv = settings["review"]
    system = (
        "You are a demanding senior editor. You read the full text and decide whether each piece "
        "deserves a slot in a daily digest that a marketing leadership team relies on.\n\n"
        f"Audience:\n{settings['audience']}\n\nIn-scope topics:\n{settings['scope']}\n\n"
        "ACCEPT only if BOTH are true: (1) marketing relevance is central, not incidental; "
        "(2) it is genuinely excellent: new insight, real evidence or data, rigorous method, or a "
        "practical playbook a team could apply. When in doubt, REJECT.\n"
        "Always REJECT: vendor marketing or thinly disguised product pitches, generic advice, "
        "rehashed basics, news without analysis, opinion without substance, clickbait, AI hype, "
        "and pieces whose text is too thin to judge (unless it is a research abstract with clear findings)."
    )
    accepted = []
    for i in range(0, len(items), 5):
        chunk = items[i: i + 5]
        payload = [{"id": it["id"], "source": it["source"], "type": it["kind"], "title": it["title"],
                    "full_text_available": it.get("fulltext", it["kind"] == "paper"), "text": it["body"][:5000]}
                   for it in chunk]
        user = ('Return a JSON array: [{"id": "...", "relevance": 0-10, "quality": 0-10, '
                '"verdict": "accept|reject", "reason": "max 15 words"}]\n\n' + json.dumps(payload, ensure_ascii=False))
        res = {r.get("id"): r for r in llm.json(system, user, max_tokens=4000) if isinstance(r, dict)}
        for it in chunk:
            r = res.get(it["id"], {})
            R, Q = float(r.get("relevance", 0) or 0), float(r.get("quality", 0) or 0)
            it["score"] = round(0.35 * R + 0.65 * Q, 1)
            ok = r.get("verdict") == "accept" and R >= rv["min_relevance"] and Q >= rv["min_quality"]
            log.info("%s  R%.0f Q%.0f  %-24s %s | %s", "ACCEPT" if ok else "reject", R, Q,
                     it["source"][:24], it["title"][:60], r.get("reason", ""))
            if ok:
                accepted.append(it)
    return accepted


# ------------------------------------------------------------------ stage 3: write-up
def summarize(llm, picked, settings):
    system = (
        "You write a daily research digest in Persian (Farsi) for this audience:\n"
        f"{settings['audience']}\n\n"
        "Rules:\n"
        "- Fluent, natural Persian; report-style analytical tone; do not address the reader directly.\n"
        "- Use Latin/English digits (0-9) for all numbers, never Persian digits.\n"
        "- Keep established English terms in English (A/B test, ROAS, LTV, uplift, CRM, LLM, agent).\n"
        "- Be concrete: keep numbers, results, method names and company names from the text. No hype, no filler.\n"
        "- If the text is only an abstract or snippet, summarise only what it says; never invent results.\n"
        "- For hands-on tutorials (kind=tutorial), key_points list the main steps or techniques, "
        "and why_it_matters names where the team could apply it."
    )
    results = {}
    for i in range(0, len(picked), 4):
        chunk = picked[i: i + 4]
        payload = [{"id": it["id"], "kind": it["kind"], "source": it["source"], "title": it["title"],
                    "text": it["body"]} for it in chunk]
        user = (
            "For each item return a JSON array of objects:\n"
            '{"id": "...", "title_fa": "Persian title, max 14 words",\n'
            ' "summary": "3-4 sentence Persian summary of the core argument and evidence",\n'
            ' "key_points": ["3 short Persian takeaways"],\n'
            ' "why_it_matters": "1-2 sentences: concrete application for this team",\n'
            ' "tags": ["2-4 short English tags"], "read_minutes": integer}\n\n'
            + json.dumps(payload, ensure_ascii=False)
        )
        for r in llm.json(system, user, max_tokens=10000):
            if isinstance(r, dict):
                results[r.get("id")] = r
        log.info("summarised %d/%d", min(i + 4, len(picked)), len(picked))
    final = []
    for it in picked:
        s = results.get(it["id"])
        if not s:
            continue
        final.append({
            "id": it["id"], "url": it["url"], "source": it["source"], "src_key": it.get("src_key"),
            "kind": it["kind"], "title": it["title"], "published": it["published"],
            "category": it["category"], "score": it["score"],
            "title_fa": s.get("title_fa") or it["title"], "summary": s.get("summary", ""),
            "key_points": s.get("key_points", [])[:4], "why_it_matters": s.get("why_it_matters", ""),
            "tags": s.get("tags", [])[:4], "read_minutes": int(s.get("read_minutes") or 5),
        })
    order = list(settings["categories"])
    return sorted(final, key=lambda a: (order.index(a["category"]), -a["score"]))


def editor_note(llm, articles):
    if not articles:
        return ""
    brief = [{"title": a["title_fa"], "summary": a["summary"]} for a in articles]
    system = ("You are the editor of a Persian research digest. Write in Persian, Latin digits, "
              "report-style, no direct address to the reader, no hype.")
    user = ('Write a JSON object {"note": "..."}: a 2-3 sentence Persian editorial naming the most '
            "important theme or pattern across today's items.\n\n" + json.dumps(brief, ensure_ascii=False))
    try:
        return llm.json(system, user, max_tokens=1500).get("note", "")
    except Exception as e:  # noqa: BLE001
        log.warning("editor note failed: %s", e)
        return ""


# ------------------------------------------------------------------ main
def main():
    settings = load_yaml("settings.yaml")
    sources_cfg = load_yaml("sources.yaml")
    sel = settings["selection"]
    today = dt.datetime.now(TEHRAN).date().isoformat()
    data_file = ROOT / "docs" / "data" / f"{today}.json"
    force = os.getenv("FORCE", "").lower() in ("1", "true", "yes")
    if data_file.exists() and not force:
        log.info("Digest for %s already exists — skipping (set FORCE=1 to rebuild).", today)
        return

    seen_path = ROOT / "data" / "seen.json"
    seen = json.loads(seen_path.read_text()) if seen_path.exists() else {}
    reg = SourceRegistry(ROOT, sources_cfg, settings)
    board = Leaderboard(ROOT, settings)
    arxiv = arxiv_sources(sources_cfg)
    llm = LLM()

    # weekly upkeep: discover new sources, drop dead leaderboard links
    reg.scout(llm, settings)
    if days_since(reg.meta.get("last_linkcheck")) >= 7:
        reg.meta["last_linkcheck"] = today
        for e in board.check_links():
            reg.events.append(f"لینک «{e['title_fa'][:40]}» از دسترس خارج شده بود و از لیدربورد حذف شد")

    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=sel["lookback_days"])
    items = [it for it in collect(reg, arxiv, since) if it["id"] not in seen]
    items = sorted(items, key=lambda x: x["published"], reverse=True)[: sel["max_candidates"]]
    log.info("fresh candidates: %d", len(items))
    if not items:
        reg.save()
        log.error("No fresh items found — check feeds.")
        sys.exit(1)

    items = triage(llm, items, settings)
    reg.record_scores(items)
    pool = [it for it in items if it["relevance"] >= sel["min_relevance"] and it["quality"] >= sel["score_threshold"]]
    log.info("passed triage: %d", len(pool))

    accepted, reviewed = [], set()
    for rnd in range(settings["review"]["rounds"]):
        rest = [it for it in pool if it["id"] not in reviewed]
        shortlist = select(rest, sel["shortlist_size"], sel, settings["categories"], "pre_score")
        if not shortlist:
            break
        with ThreadPoolExecutor(max_workers=6) as ex:
            shortlist = list(ex.map(fetch_fulltext, shortlist))
        reviewed |= {it["id"] for it in shortlist}
        accepted += review(llm, shortlist, settings)
        log.info("review round %d: %d accepted so far", rnd + 1, len(accepted))
        if len(accepted) >= sel["min_articles"] + 2:
            break

    final = select(accepted, sel["target_articles"], sel, settings["categories"], "score", sel.get("min_tutorials", 0))
    articles = summarize(llm, final, settings)
    if len(articles) < sel["min_articles"]:
        log.warning("only %d articles passed the bar today — quality is not lowered to fill the quota", len(articles))
    note = editor_note(llm, articles)

    entered = board.update(llm, articles)
    for a in articles:
        a["board"] = a["id"] in entered
    reg.record_picks(articles)
    reg.evaluate_quality()

    digest = {
        "date": today,
        "generated_at": dt.datetime.now(TEHRAN).isoformat(timespec="minutes"),
        "candidates": len(items), "reviewed": len(reviewed),
        "note": note, "articles": articles, "events": reg.events,
    }
    data_file.parent.mkdir(parents=True, exist_ok=True)
    data_file.write_text(json.dumps(digest, ensure_ascii=False, indent=1), encoding="utf-8")

    cutoff = (dt.date.today() - dt.timedelta(days=120)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    pool_ids = {it["id"] for it in pool}
    for it in items:
        if it["id"] in reviewed or it["id"] not in pool_ids:
            seen[it["id"]] = today   # judged once is enough; unreviewed good leftovers may compete tomorrow
    seen_path.parent.mkdir(parents=True, exist_ok=True)
    seen_path.write_text(json.dumps(seen), encoding="utf-8")
    board.save()
    reg.save()

    render_site(ROOT, settings, board.board, reg.rows(arxiv))
    log.info("site rendered | LLM calls: %d", llm.calls)

    if os.getenv("SMTP_USER") and os.getenv("MAIL_TO"):
        send_email(digest, settings)
    else:
        log.info("SMTP not configured — email skipped")


if __name__ == "__main__":
    main()
