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
    step = 25 if llm.small else 60
    for i in range(0, len(items), step):
        chunk = items[i: i + step]
        payload = [{"id": it["id"], "source": it["source"], "hint": it["hint"],
                    "title": it["title"], "snippet": it["snippet"][:350]} for it in chunk]
        user = ('Return a JSON array: [{"id": "...", "relevance": 0-10, "quality": 0-10, '
                '"category": "<key>", "type": "tutorial|paper|insight"}]\n\n' + json.dumps(payload, ensure_ascii=False))
        for r in llm.json(system, user, max_tokens=4000):
            if isinstance(r, dict) and r.get("id"):
                out[r["id"]] = r
        log.info("triaged %d/%d", min(i + step, len(items)), len(items))
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
    step, chars = (2, 3500) if llm.small else (5, 5000)
    for i in range(0, len(items), step):
        chunk = items[i: i + step]
        payload = [{"id": it["id"], "source": it["source"], "type": it["kind"], "title": it["title"],
                    "full_text_available": it.get("fulltext", it["kind"] == "paper"), "text": it["body"][:chars]}
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
TEAMS = ["Ads", "Affiliate", "eCRM", "Engagement", "Data", "Product", "Leadership"]


FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def latin_digits(v):
    if isinstance(v, str):
        return v.translate(FA_DIGITS)
    if isinstance(v, list):
        return [latin_digits(x) for x in v]
    if isinstance(v, dict):
        return {k: latin_digits(x) for k, x in v.items()}
    return v


def summarize(llm, picked, settings):
    """One-page Persian brief per article: what it is about, what was done, what was found, the conclusion."""
    system = (
        "You write one-page Persian (Farsi) research briefs so a marketing team can understand an article "
        "fully without reading the original. Audience:\n"
        f"{settings['audience']}\n\n"
        "Each brief must answer clearly: what the piece is about, what problem or question it tackles, "
        "what exactly the authors did or worked on (data, method, experiment, framework, steps), what they "
        "found (with the concrete numbers), and what they conclude. Then what it means for this team.\n\n"
        "Rules:\n"
        "- Fluent, natural Persian prose; analytical report style; no direct address to the reader; no hype, no filler.\n"
        "- Use ONLY Latin digits 0-9 for every number. Never use Persian or Arabic digits.\n"
        "- Keep established English terms in English (A/B test, ROAS, LTV, uplift, holdout, LLM, agent, SEO).\n"
        "- Keep specifics: numbers, effect sizes, sample sizes, company, product, tool and method names, step order.\n"
        "- Only state what the text supports. If the text is only an abstract or snippet, write a shorter brief and say so in caveats.\n"
        "- Paragraph fields are real paragraphs (3-6 sentences). Bullets are one line each.\n"
        "- Total length of one brief: roughly 350-550 Persian words."
    )
    schema = (
        "Return a JSON array; one object per item:\n"
        '{"id": "...",\n'
        ' "title_fa": "Persian title, max 14 words",\n'
        ' "tldr": "ONE Persian sentence with the single most important takeaway",\n'
        ' "about": "paragraph: what the piece is about, who wrote it, what kind of piece it is",\n'
        ' "problem": "paragraph: the problem or question it addresses and why it matters",\n'
        ' "approach": "paragraph: what they actually did or worked on: data, method, experiment, framework",\n'
        ' "steps": ["2-6 bullets: method steps, framework parts or tutorial steps, in order"],\n'
        ' "findings": ["2-6 bullets: results and numbers; empty list if the text has none"],\n'
        ' "conclusion": "paragraph: the authors\' conclusion and argument in their own logic",\n'
        ' "for_us": ["2-4 bullets: what this means for the team and what to try or check; start with a verb"],\n'
        ' "caveats": ["0-3 bullets: limits, conditions, missing evidence"],\n'
        f' "teams": subset of {TEAMS},\n'
        ' "level": "intro" | "practitioner" | "advanced",\n'
        ' "tags": ["2-4 short English tags"], "read_minutes": integer (original article)}\n\n'
    )
    results = {}
    step, chars, out_tokens = (1, 12000, 4000) if llm.small else (2, 12000, 16000)
    for i in range(0, len(picked), step):
        chunk = picked[i: i + step]
        payload = [{"id": it["id"], "kind": it["kind"], "source": it["source"], "title": it["title"],
                    "text": it["body"][:chars]} for it in chunk]
        try:
            res = llm.json(system, schema + json.dumps(payload, ensure_ascii=False), max_tokens=out_tokens)
        except Exception as e:  # noqa: BLE001
            log.warning("brief failed for %s: %s", [c["id"] for c in chunk], e)
            continue
        if isinstance(res, dict) and len(chunk) == 1 and not res.get("id"):
            res = dict(res, id=chunk[0]["id"])
        for r in (res if isinstance(res, list) else [res]):
            if isinstance(r, dict):
                if len(chunk) == 1 and not r.get("id"):
                    r["id"] = chunk[0]["id"]
                results[r.get("id")] = latin_digits(r)
        log.info("briefed %d/%d", min(i + step, len(picked)), len(picked))

    def lst(v, n):
        return [str(x).strip() for x in (v or []) if str(x).strip()][:n]

    final = []
    for it in picked:
        s = results.get(it["id"])
        if not s:
            continue
        final.append({
            "id": it["id"], "url": it["url"], "source": it["source"], "src_key": it.get("src_key"),
            "kind": it["kind"], "title": it["title"], "published": it["published"],
            "category": it["category"], "score": it["score"],
            "title_fa": s.get("title_fa") or it["title"], "tldr": s.get("tldr", ""),
            "about": s.get("about", ""), "problem": s.get("problem", ""), "approach": s.get("approach", ""),
            "steps": lst(s.get("steps"), 6), "findings": lst(s.get("findings"), 6),
            "conclusion": s.get("conclusion", ""), "for_us": lst(s.get("for_us"), 4),
            "caveats": lst(s.get("caveats"), 3),
            "teams": [t for t in (s.get("teams") or []) if t in TEAMS][:4],
            "level": s.get("level") if s.get("level") in ("intro", "practitioner", "advanced") else "practitioner",
            "tags": lst(s.get("tags"), 4), "read_minutes": int(s.get("read_minutes") or 5),
        })
    order = list(settings["categories"])
    return sorted(final, key=lambda a: (order.index(a["category"]), -a["score"]))


def editor_note(llm, articles):
    if not articles:
        return ""
    brief = [{"title": a["title_fa"], "takeaway": a.get("tldr", ""), "conclusion": a.get("conclusion", "")[:400]} for a in articles]
    system = ("You are the editor of a Persian research digest. Write in Persian using ONLY Latin digits 0-9, "
              "report-style, no direct address to the reader, no hype.")
    user = ('Write a JSON object {"note": "..."}: a 2-3 sentence Persian editorial naming the most '
            "important theme or pattern across today's items and what it means in practice for the team.\n\n"
            + json.dumps(brief, ensure_ascii=False))
    try:
        return latin_digits(llm.json(system, user, max_tokens=1500).get("note", ""))
    except Exception as e:  # noqa: BLE001
        log.warning("editor note failed: %s", e)
        return ""


# ------------------------------------------------------------------ leaderboard backfill
def backfill_board(llm, board, settings, leftovers=()):
    """Fill categories that have fewer than `size` entries with the best available pieces:
    today's reviewed-but-unpicked items first, then all-time reference pieces proposed by the model,
    each verified live, reviewed with the same quality gate and briefed before it can enter."""
    cfg = settings["leaderboard"]
    size, min_score = cfg["size"], cfg["min_score"]
    budget = cfg.get("backfill_per_run", 15)
    cats = settings["categories"]
    need = {c: size - len(board.board.get(c, [])) for c in cats if len(board.board.get(c, [])) < size}
    if not need or budget <= 0:
        return set()
    log.info("leaderboard backfill needed: %s", need)
    tried_path = ROOT / "data" / "backfill_tried.json"
    tried = set(json.loads(tried_path.read_text())) if tried_path.exists() else set()
    on_board = {e["id"] for v in board.board.values() for e in v}

    by_cat = {}
    for it in leftovers:
        if it["id"] not in on_board and it.get("score", 0) >= min_score and it["category"] in need:
            by_cat.setdefault(it["category"], []).append(it)

    canon = []
    for cat in sorted(need, key=need.get, reverse=True)[: cfg.get("canon_categories_per_run", 4)]:
        system = ("You are a senior marketing research librarian. Only list pieces you are highly confident "
                  "exist at the exact URL you give.")
        user = (
            f"Audience:\n{settings['audience']}\n\nCategory: {cats[cat]['en']}\n\n"
            "List up to 8 of the most valuable, widely cited, freely readable pieces ever published for this "
            "category: research papers (arXiv, SSRN, journal open access), company engineering/data blog posts "
            "with real experiments or case studies, and definitive practitioner guides. Prefer timeless, "
            "evidence-rich work from credible sources. No paywalled pages, no vendor landing pages.\n"
            'Return a JSON array: [{"title": "...", "url": "https://...", "source": "...", '
            '"type": "paper|tutorial|insight"}]'
        )
        try:
            props = llm.json(system, user, max_tokens=2500)
        except Exception as e:  # noqa: BLE001
            log.warning("canon proposal failed for %s: %s", cat, e)
            continue
        for pr in props if isinstance(props, list) else []:
            url = str(pr.get("url", "")).strip()
            if not url.startswith("http") or url in tried or uid(url) in on_board:
                continue
            tried.add(url)
            kind = {"paper": "paper", "tutorial": "tutorial"}.get(pr.get("type"), "article")
            canon.append({"id": uid(url), "url": url, "title": clean_text(pr.get("title", ""), 300),
                          "source": clean_text(pr.get("source", "") or url.split("/")[2], 60), "kind": "article",
                          "real_kind": kind, "category": cat, "snippet": "", "published": "", "src_key": None})
    if canon:
        with ThreadPoolExecutor(max_workers=6) as ex:
            canon = list(ex.map(fetch_fulltext, canon))
        canon = [c for c in canon if c.get("fulltext")]          # page must exist and have real text
        for c in canon:
            c["kind"] = c.pop("real_kind")
        log.info("canon candidates live with full text: %d", len(canon))
        for c in review(llm, canon, settings):
            if c["score"] >= min_score:
                by_cat.setdefault(c["category"], []).append(c)
    tried_path.write_text(json.dumps(sorted(tried)), encoding="utf-8")

    picks = []
    for cat in sorted(need, key=need.get, reverse=True):
        for it in sorted(by_cat.get(cat, []), key=lambda x: -x["score"])[: need[cat]]:
            if len(picks) < budget:
                picks.append(it)
    if not picks:
        return set()
    briefs = summarize(llm, picks, settings)
    entered = board.update(llm, briefs)
    log.info("leaderboard backfill: %d entered", len(entered))
    if entered:
        board.save()
    return entered


# ------------------------------------------------------------------ main
def main():
    settings = load_yaml("settings.yaml")
    sources_cfg = load_yaml("sources.yaml")
    sel = settings["selection"]
    today = dt.datetime.now(TEHRAN).date().isoformat()
    data_file = ROOT / "docs" / "data" / f"{today}.json"
    force = os.getenv("FORCE", "").lower() in ("1", "true", "yes")
    if data_file.exists() and not force:
        # digest already built today: use the run to keep filling the leaderboard instead
        log.info("Digest for %s already exists — running leaderboard backfill only.", today)
        board = Leaderboard(ROOT, settings)
        reg = SourceRegistry(ROOT, sources_cfg, settings)
        entered = backfill_board(LLM(), board, settings)
        if entered:
            render_site(ROOT, settings, board.board, reg.rows(arxiv_sources(sources_cfg)))
        return

    seen_path = ROOT / "data" / "seen.json"
    seen = json.loads(seen_path.read_text()) if seen_path.exists() else {}
    if force:
        seen = {k: v for k, v in seen.items() if v != today}
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
    picked_ids = {a["id"] for a in articles}
    try:
        backfill_board(llm, board, settings, [it for it in accepted if it["id"] not in picked_ids])
    except Exception as e:  # noqa: BLE001
        log.warning("leaderboard backfill skipped: %s", e)
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
