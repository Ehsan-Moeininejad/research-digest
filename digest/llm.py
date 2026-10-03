"""
LLM client with automatic fallback, returns parsed JSON.

Default chain (LLM_PROVIDER, comma-separated): github,gemini
- github    → GitHub Models (free, uses the workflow's own GITHUB_TOKEN; no key to manage)
- gemini    → Google Gemini free tier (GEMINI_API_KEY)
- anthropic → Claude (ANTHROPIC_API_KEY, paid)
If a provider keeps failing (overload, quota, unknown model) the run switches to the next one.
"""
import json
import logging
import os
import re
import time

import requests

log = logging.getLogger("digest")

GITHUB_MODELS = ["openai/gpt-4.1-mini", "openai/gpt-4o-mini", "openai/gpt-4.1"]
GEMINI_MODELS = ["gemini-3.8-flash"]


class ProviderDown(Exception):
    """Provider unusable for the rest of this run."""


class LLM:
    def __init__(self):
        chain = os.getenv("LLM_PROVIDER", "").strip().lower() or "github,gemini"
        self.providers = []
        for name in [p.strip() for p in chain.split(",") if p.strip()]:
            if name == "github" and os.getenv("GITHUB_TOKEN"):
                models = [os.getenv("GITHUB_MODEL")] if os.getenv("GITHUB_MODEL") else GITHUB_MODELS
                self.providers.append({"name": "github", "models": list(models), "gap": 5.0})
            elif name == "gemini" and os.getenv("GEMINI_API_KEY"):
                models = [os.getenv("LLM_MODEL")] if os.getenv("LLM_MODEL") else GEMINI_MODELS
                self.providers.append({"name": "gemini", "models": list(models), "gap": 7.0})
            elif name == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
                self.providers.append({"name": "anthropic", "models": [os.getenv("ANTHROPIC_MODEL") or "claude-haiku-4-5-20251001"], "gap": 0.0})
        if not self.providers:
            raise RuntimeError("No usable LLM provider: set GITHUB_TOKEN (automatic in Actions) or GEMINI_API_KEY")
        self.calls = 0
        self._last = 0.0
        # GitHub Models free tier has small per-request limits; callers size their batches with this
        self.small = self.providers[0]["name"] == "github"
        log.info("LLM chain: %s", " → ".join(f"{p['name']}:{p['models'][0]}" for p in self.providers))

    @property
    def model(self):
        return f"{self.providers[0]['name']}:{self.providers[0]['models'][0]}" if self.providers else "none"

    # ---------------------------------------------------------------
    def json(self, system: str, user: str, max_tokens: int = 4000, retries: int = 6):
        """Try providers in order. A provider that is down/out of quota is dropped for the rest of the run;
        one that only returned unparseable text twice is skipped for this call but kept for the next."""
        for p in list(self.providers):
            last, bad_json, dead = None, 0, False
            for attempt in range(1, retries + 1):
                gap = time.time() - self._last
                if gap < p["gap"]:
                    time.sleep(p["gap"] - gap)
                self._last = time.time()
                self.calls += 1
                raw = ""
                try:
                    raw = self._call(p, system, user, max_tokens)
                    return _parse_json(raw)
                except ProviderDown as e:
                    last, dead = e, True
                    break
                except json.JSONDecodeError as e:
                    last = e
                    bad_json += 1
                    log.warning("LLM %s returned unparseable output (%d): %s | raw: %r",
                                p["name"], bad_json, e, (raw or "")[:300])
                    if bad_json >= 2:
                        break
                    time.sleep(5)
                except Exception as e:  # noqa: BLE001
                    last = e
                    wait = getattr(e, "retry_after", None) or min(120, 20 * attempt)
                    if wait > 300:
                        log.warning("%s asks to wait %ss — switching provider", p["name"], wait)
                        dead = True
                        break
                    log.warning("LLM %s failed (attempt %d/%d): %s — retry in %ds",
                                p["name"], attempt, retries, str(e)[:300], wait)
                    time.sleep(wait)
            else:
                dead = True  # retries exhausted
            if dead and p in self.providers:
                self.providers.remove(p)
                log.error("provider %s dropped for this run (%s)", p["name"], str(last)[:200])
            else:
                log.warning("provider %s skipped for this call (%s)", p["name"], str(last)[:200])
        raise RuntimeError("All LLM providers failed for this call")

    # ---------------------------------------------------------------
    def _call(self, p, system, user, max_tokens):
        if p["name"] == "github":
            return self._github(p, system, user, min(max_tokens, 4000))
        if p["name"] == "gemini":
            return self._gemini(p, system, user, max_tokens)
        return self._anthropic(p, system, user, max_tokens)

    def _github(self, p, system, user, max_tokens):
        r = requests.post(
            "https://models.github.ai/inference/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Content-Type": "application/json",
                     "Accept": "application/json"},
            json={"model": p["models"][0], "max_tokens": max_tokens, "temperature": 0.3,
                  "messages": [{"role": "system", "content": system + "\n\nRespond with valid JSON only. No markdown fences."},
                               {"role": "user", "content": user}]},
            timeout=300,
        )
        if r.status_code in (400, 404) and ("model" in r.text.lower() and ("unknown" in r.text.lower() or "not found" in r.text.lower() or "unavailable" in r.text.lower())):
            bad = p["models"].pop(0)
            log.warning("GitHub model %s not available — trying next", bad)
            if not p["models"]:
                raise ProviderDown("no GitHub model available")
            raise RuntimeError(f"model {bad} unavailable")
        if r.status_code in (401, 403):
            raise ProviderDown(f"GitHub Models auth {r.status_code}: {r.text[:200]} (workflow needs permissions: models: read)")
        if r.status_code == 429:
            e = RuntimeError(f"GitHub Models 429: {r.text[:200]}")
            e.retry_after = int(r.headers.get("retry-after", "60") or 60)
            raise e
        if r.status_code == 413 or "tokens_limit_reached" in r.text:
            raise RuntimeError(f"GitHub Models request too large: {r.text[:200]}")
        if r.status_code >= 400:
            raise RuntimeError(f"GitHub Models HTTP {r.status_code}: {r.text[:300]}")
        try:
            data = r.json()
        except ValueError:
            raise RuntimeError(f"GitHub Models non-JSON reply (HTTP {r.status_code}): {r.text[:300]!r}")
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        if not content.strip():
            raise RuntimeError(f"GitHub Models empty answer (finish_reason={choice.get('finish_reason')}): {r.text[:300]!r}")
        return content

    def _gemini(self, p, system, user, max_tokens):
        model = p["models"][0]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        gen = {"responseMimeType": "application/json", "maxOutputTokens": max(max_tokens, 8000)}
        if model.startswith("gemini-2"):
            gen.update(temperature=0.3, thinkingConfig={"thinkingBudget": 0})
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}], "generationConfig": gen}
        r = requests.post(url, params={"key": os.environ["GEMINI_API_KEY"]}, json=body, timeout=300)
        if r.status_code == 400 and len(gen) > 2:
            body["generationConfig"] = {"responseMimeType": "application/json", "maxOutputTokens": gen["maxOutputTokens"]}
            r = requests.post(url, params={"key": os.environ["GEMINI_API_KEY"]}, json=body, timeout=300)
        if r.status_code == 404:
            raise ProviderDown(f"Gemini model '{model}' not available: {r.text[:250]} — set Variable LLM_MODEL")
        if r.status_code >= 400:
            raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:240]}")
        parts = r.json()["candidates"][0]["content"]["parts"]
        return "".join(x.get("text", "") for x in parts if not x.get("thought"))

    def _anthropic(self, p, system, user, max_tokens):
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": p["models"][0], "max_tokens": max_tokens, "temperature": 0.3,
                  "system": system + "\n\nRespond with valid JSON only. No markdown fences, no preamble.",
                  "messages": [{"role": "user", "content": user}]},
            timeout=300,
        )
        if r.status_code in (401, 403, 404):
            raise ProviderDown(f"Anthropic HTTP {r.status_code}: {r.text[:200]}")
        if r.status_code >= 400:
            raise RuntimeError(f"Anthropic HTTP {r.status_code}: {r.text[:300]}")
        return "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")


def _parse_json(text: str):
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        starts = [i for i in (text.find("["), text.find("{")) if i != -1]
        if not starts:
            raise
        start = min(starts)
        end = max(text.rfind("]"), text.rfind("}"))
        return json.loads(text[start: end + 1])
