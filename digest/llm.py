"""LLM client: Gemini (free tier, default) or Anthropic Claude. Returns parsed JSON."""
import json
import logging
import os
import re
import time

import requests

log = logging.getLogger("digest")


class LLM:
    def __init__(self):
        self.provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower() or "gemini"
        if self.provider == "gemini":
            self.key = os.environ["GEMINI_API_KEY"]
            self.model = os.getenv("LLM_MODEL") or "gemini-3.8-flash"
        elif self.provider == "anthropic":
            self.key = os.environ["ANTHROPIC_API_KEY"]
            self.model = os.getenv("LLM_MODEL") or "claude-haiku-4-5-20251001"
        else:
            raise ValueError(f"Unknown LLM_PROVIDER: {self.provider}")
        # free-tier rate limits: keep a gap between calls (Gemini Flash free tier ~10 requests/min)
        default_gap = "7" if self.provider == "gemini" else "0"
        self.min_gap = float(os.getenv("LLM_MIN_INTERVAL", default_gap))
        self._last = 0.0
        self.calls = 0
        log.info("LLM provider=%s model=%s", self.provider, self.model)

    # ---------------------------------------------------------------
    def json(self, system: str, user: str, max_tokens: int = 12000, retries: int = 5):
        last = None
        for attempt in range(1, retries + 1):
            try:
                gap = time.time() - self._last
                if gap < self.min_gap:
                    time.sleep(self.min_gap - gap)
                self._last = time.time()
                self.calls += 1
                text = self._call(system, user, max_tokens)
                return _parse_json(text)
            except Exception as e:  # noqa: BLE001
                last = e
                wait = min(90, 15 * attempt)
                log.warning("LLM call failed (attempt %d/%d): %s — retry in %ds", attempt, retries, e, wait)
                time.sleep(wait)
        raise RuntimeError(f"LLM failed after {retries} attempts: {last}")

    # ---------------------------------------------------------------
    def _call(self, system: str, user: str, max_tokens: int) -> str:
        if self.provider == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            gen = {"responseMimeType": "application/json", "maxOutputTokens": max_tokens}
            if self.model.startswith("gemini-2"):
                # 2.x models: switch thinking off to save quota; newer models manage it themselves
                gen.update(temperature=0.3, thinkingConfig={"thinkingBudget": 0})
            body = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": gen,
            }
            r = requests.post(url, params={"key": self.key}, json=body, timeout=300)
            if r.status_code == 400 and len(gen) > 2:
                # an option this model does not accept → retry once with the minimal config
                body["generationConfig"] = {"responseMimeType": "application/json", "maxOutputTokens": max_tokens}
                r = requests.post(url, params={"key": self.key}, json=body, timeout=300)
            if r.status_code == 404:
                raise RuntimeError(f"Gemini model '{self.model}' not available (404). "
                                   f"Set the repository Variable LLM_MODEL to the model named here: {r.text[:300]}")
            if r.status_code >= 400:
                raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:300]}")
            data = r.json()
            parts = data["candidates"][0]["content"]["parts"]
            # skip thought parts some models return alongside the answer
            return "".join(p.get("text", "") for p in parts if not p.get("thought"))

        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": 0.3,
                "system": system + "\n\nRespond with valid JSON only. No markdown fences, no preamble.",
                "messages": [{"role": "user", "content": user}],
            },
            timeout=300,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Anthropic HTTP {r.status_code}: {r.text[:300]}")
        return "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")


def _parse_json(text: str):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        starts = [i for i in (text.find("["), text.find("{")) if i != -1]
        if not starts:
            raise
        start = min(starts)
        end = max(text.rfind("]"), text.rfind("}"))
        return json.loads(text[start : end + 1])
