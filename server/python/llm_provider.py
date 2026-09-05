"""Unified interface for LLM inference: Ollama (local), Groq (hosted) or Anthropic (hosted).

LLM_PROVIDER selects the backend; LLM_MODEL overrides each backend's default
model id. Every provider exposes the same generate() so llm_form_analysis and
llm_post_scorer never know which one they are talking to.
"""

import os
import json
import re
import time
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


DEFAULT_OLLAMA_MODEL = "llama3.2:3b"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

# The Claude provider mirrors consensus_agent.py's extraction call — the one
# Anthropic path this system has run in production, on the ANTHROPIC_API_KEY
# every task already receives. Thinking is explicitly disabled: these models
# otherwise reason inside the max_tokens budget and can hand back a truncated
# body. No sampling parameters are sent: a non-default temperature is rejected
# on claude-sonnet-5 and later, so generate()'s temperature argument is accepted
# for interface parity and ignored here.
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
ANTHROPIC_NO_THINKING = {"type": "disabled"}
ANTHROPIC_TIMEOUT_SECONDS = 120

# Rate limiting for Groq free tier (30 req/min)
GROQ_MIN_DELAY_SECONDS = 2.1  # ~28 req/min to stay safe

# A body cut off at max_tokens is deterministic, not a flake: the same prompt
# at the same ceiling truncates in the same place every time. generate_json's
# retry therefore spent a second call to fail identically. Raising the ceiling
# is the only retry that can succeed, and the ceiling below is where a prompt
# that wants unbounded output stops rather than runs away.
TRUNCATION_RETRY_FACTOR = 2
TRUNCATION_RETRY_CEILING = 16000


class LLMProviderError(Exception):
    pass



class LLMProvider:
    def __init__(self, model: str):
        self.model = model
        # Whether the last generate() ran out of budget instead of finishing.
        # Every provider already detected this and threw it away into a log
        # line, so no caller could act on it. Plain instance state is honest
        # here: the tips pipeline drives one provider from one thread, race
        # after race, and every generate() resets it before the call.
        self.last_truncated = False

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str:
        raise NotImplementedError

    def generate_json(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        retries: int = 1,
    ) -> Dict[str, Any]:
        """Generate and parse JSON, raising the ceiling if the body was cut off.

        A retry at the same ceiling cannot fix truncation — it is not a flake,
        it is the budget. That is how every ai_score this system ever asked for
        was lost: 2500 tokens for six horses of JSON prose, two identical
        failures, and an empty dict handed back without a word.
        """
        budget = max_tokens
        for attempt in range(1 + retries):
            raw = self.generate(prompt, system=system, temperature=temperature,
                                max_tokens=budget)
            result = _extract_json(raw)
            if result:
                return result
            if attempt >= retries:
                break
            if not self.last_truncated:
                logger.info("JSON parse failed, retrying...")
            elif budget >= TRUNCATION_RETRY_CEILING:
                logger.warning(
                    "JSON parse failed on a body cut off at the %d-token "
                    "ceiling; a retry cannot fix this", TRUNCATION_RETRY_CEILING)
            else:
                budget = min(budget * TRUNCATION_RETRY_FACTOR,
                             TRUNCATION_RETRY_CEILING)
                logger.warning(
                    "JSON parse failed on a body cut off at max_tokens; "
                    "retrying at %d", budget)
        if self.last_truncated:
            logger.warning(
                "giving up on JSON: response still truncated at max_tokens=%d",
                budget)
        return {}



class OllamaProvider(LLMProvider):
    def __init__(self, model: Optional[str] = None):
        super().__init__(model or os.environ.get("LLM_MODEL", DEFAULT_OLLAMA_MODEL))

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str:
        import requests

        self.last_truncated = False
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if system:
            payload["system"] = system

        try:
            resp = requests.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json=payload,
                timeout=120,
            )
            resp.raise_for_status()
            body = resp.json()
            self.last_truncated = body.get("done_reason") == "length"
            return body.get("response", "").strip()
        except requests.ConnectionError:
            raise LLMProviderError(
                f"Cannot connect to Ollama at {OLLAMA_BASE_URL}. "
                "Is Ollama running? Start it with: ollama serve"
            )
        except Exception as e:
            raise LLMProviderError(f"Ollama error: {e}")



class GroqProvider(LLMProvider):
    _last_call_time = 0.0

    def __init__(self, model: Optional[str] = None):
        super().__init__(model or os.environ.get("LLM_MODEL", DEFAULT_GROQ_MODEL))
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise LLMProviderError(
                "GROQ_API_KEY not set. Get a free key at https://console.groq.com"
            )
        try:
            from groq import Groq
            self._client = Groq(api_key=api_key)
        except ImportError:
            raise LLMProviderError("groq package not installed. Run: pip install groq")

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str:
        self.last_truncated = False
        elapsed = time.time() - GroqProvider._last_call_time
        if elapsed < GROQ_MIN_DELAY_SECONDS:
            time.sleep(GROQ_MIN_DELAY_SECONDS - elapsed)

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            GroqProvider._last_call_time = time.time()
            finish = resp.choices[0].finish_reason
            self.last_truncated = finish == "length"
            if finish == "length":
                logger.warning(f"Groq response truncated (max_tokens={max_tokens})")
            return resp.choices[0].message.content.strip()
        except Exception as e:
            raise LLMProviderError(f"Groq API error: {e}")



class AnthropicProvider(LLMProvider):
    """Claude through the Anthropic SDK. See the module constants for why
    thinking is off and no sampling parameters are passed."""

    def __init__(self, model: Optional[str] = None):
        super().__init__(model or os.environ.get("LLM_MODEL", DEFAULT_ANTHROPIC_MODEL))
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise LLMProviderError(
                "ANTHROPIC_API_KEY not set. It is the same key the consensus agent uses."
            )
        try:
            from anthropic import Anthropic
        except ImportError:
            raise LLMProviderError("anthropic package not installed. Run: pip install anthropic")
        self._client = Anthropic(api_key=api_key)

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str:
        self.last_truncated = False
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "thinking": ANTHROPIC_NO_THINKING,
            "messages": [{"role": "user", "content": prompt}],
            "timeout": ANTHROPIC_TIMEOUT_SECONDS,
        }
        if system:
            kwargs["system"] = system
        try:
            resp = self._client.messages.create(**kwargs)
        except Exception as e:
            raise LLMProviderError(f"Anthropic API error: {e}")

        stop_reason = getattr(resp, "stop_reason", None)
        self.last_truncated = stop_reason == "max_tokens"
        if stop_reason == "refusal":
            # HTTP 200 with no usable body. Raising keeps the caller's
            # fallback path honest instead of handing it an empty string that
            # reads like a short answer.
            raise LLMProviderError(f"Anthropic refused the request (model {self.model})")
        text = "".join(
            getattr(block, "text", "") or ""
            for block in (getattr(resp, "content", None) or [])
            if getattr(block, "type", "") == "text"
        )
        if stop_reason == "max_tokens":
            logger.warning(f"Anthropic response truncated (max_tokens={max_tokens})")
        if not text.strip():
            # An empty body is a failed call, not a short answer, and the
            # difference decides whether the caller falls back. GroqProvider
            # gets this for free: .strip() on a null body raises AttributeError
            # and becomes an LLMProviderError. Returning "" here instead would
            # reach generate_rich_insight's SUCCESS path, so ai_analysis is
            # never substituted, nothing is logged, and the pick is published
            # blank under a fresh ai_insight_generated_at — indistinguishable
            # from the 2026-09-02 rows this provider exists to prevent.
            raise LLMProviderError(
                f"Anthropic returned no text (stop_reason={stop_reason}, "
                f"model {self.model})")
        return text.strip()



_provider_instance: Optional[LLMProvider] = None


def get_provider(force_new: bool = False) -> LLMProvider:
    """Get or create the configured LLM provider (singleton)."""
    global _provider_instance
    if _provider_instance is not None and not force_new:
        return _provider_instance

    provider_name = os.environ.get("LLM_PROVIDER", "groq").lower()

    if provider_name == "ollama":
        _provider_instance = OllamaProvider()
    elif provider_name == "groq":
        _provider_instance = GroqProvider()
    elif provider_name in ("anthropic", "claude"):
        _provider_instance = AnthropicProvider()
    else:
        raise LLMProviderError(
            f"Unknown LLM_PROVIDER '{provider_name}'. Use 'ollama', 'groq' or 'anthropic'."
        )

    logger.info(f"LLM provider: {provider_name} ({_provider_instance.model})")
    return _provider_instance



def _clean_json_text(text: str) -> str:
    """Clean common LLM JSON issues."""
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = re.sub(r",\s*}", "}", text)
    text = re.sub(r",\s*]", "]", text)
    # Fix unescaped newlines inside JSON strings (replace literal newlines within values)
    # This is a rough heuristic — replace \n that's NOT at a JSON structural boundary
    return text


def _extract_json(text: str) -> Dict[str, Any]:
    """Extract JSON from LLM response, handling markdown code blocks and common errors."""
    text = text.strip()
    last_err = ""

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        last_err = str(e)

    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError as e:
            last_err = str(e)

    json_str = _find_balanced_braces(text)
    if json_str:
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            last_err = str(e)
        cleaned = _clean_json_text(json_str)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            last_err = str(e)

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        cleaned = _clean_json_text(match.group(0))
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            last_err = str(e)

    logger.warning(f"Failed to parse JSON (len={len(text)}, err={last_err}): {text[:300]}...")
    return {}


def _find_balanced_braces(text: str) -> Optional[str]:
    """Find the first balanced { ... } block using brace counting."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None



if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if "--test" in sys.argv:
        print("Testing LLM provider...")
        try:
            llm = get_provider()
            print(f"  Provider: {type(llm).__name__}")
            print(f"  Model: {llm.model}")

            result = llm.generate("Say 'hello' and nothing else.", max_tokens=10)
            print(f"  Response: {result}")

            json_result = llm.generate_json(
                'Return exactly this JSON: {"test": true, "status": "ok"}',
                max_tokens=50,
            )
            print(f"  JSON parse: {json_result}")

            print("\n  All tests passed!")
        except LLMProviderError as e:
            print(f"\n  ERROR: {e}")
            sys.exit(1)
    else:
        print("Usage: python llm_provider.py --test")
