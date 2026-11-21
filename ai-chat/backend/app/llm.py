"""LiteLLM gateway: key pool, automatic failover and built-in response caching."""
import logging
import os
from dataclasses import dataclass

# Disable telemetry and remote cost map fetching so startup and tests are instantaneous and offline-safe
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

import litellm

litellm.telemetry = False
litellm.disable_version_check = True
litellm.drop_params = True

log = logging.getLogger("app.llm")


class AllProvidersFailed(Exception):
    """Every key in the pool failed for this request."""


class SimulatedProviderError(Exception):
    """Raised for keys listed in X-Simulate-Failure; behaves like a provider HTTP error."""

    def __init__(self, status_code: int):
        super().__init__(f"simulated provider failure (HTTP {status_code})")
        self.status_code = status_code


@dataclass
class LLMResult:
    reply: str
    input_tokens: int
    output_tokens: int
    cache_hit: bool
    key_id: str | None
    fallback_attempts: int


def setup_cache(settings: Settings) -> None:
    """Enable LiteLLM's built-in cache. Key = model + full message list (+ fixed params)."""
    if settings.cache_type == "disk":
        litellm.cache = litellm.Cache(type="disk", disk_cache_dir=settings.cache_dir)
    else:
        litellm.cache = litellm.Cache(type="local")


def cache_enabled() -> bool:
    return litellm.cache is not None


async def _provider_call(settings: Settings, messages: list[dict], api_key: str, bypass_cache: bool):
    kwargs = dict(
        model=settings.model,
        messages=messages,
        api_key=api_key,
        max_tokens=settings.max_tokens,
        timeout=settings.request_timeout,
        num_retries=0,  # failover across keys replaces in-key retries
    )
    if bypass_cache:
        kwargs["cache"] = {"no-cache": True}  # skip cache read, still writes result
    return await litellm.acompletion(**kwargs)


async def complete(settings: Settings, messages: list[dict],
                   simulate: dict[str, int] | None = None) -> LLMResult:
    simulate = simulate or {}
    failures = 0
    for index, api_key in enumerate(settings.api_keys, start=1):
        key_id = f"key_{index}"
        try:
            status = simulate.get(key_id, simulate.get("*"))
            if status is not None:
                raise SimulatedProviderError(status)
            response = await _provider_call(settings, messages, api_key, bypass_cache=bool(simulate))
            reply = (response.choices[0].message.content or "").strip()
            if not reply:
                raise RuntimeError("provider returned an empty reply")
            usage = getattr(response, "usage", None)
            hit = bool((getattr(response, "_hidden_params", None) or {}).get("cache_hit"))
            return LLMResult(
                reply=reply,
                input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                cache_hit=hit,
                key_id=None if hit else key_id,
                fallback_attempts=0 if hit else failures,
            )
        except Exception as exc:  # any key-level failure -> try the next key
            failures += 1
            log.warning("%s failed (%s: %s); trying next key", key_id, type(exc).__name__, str(exc)[:200])
    raise AllProvidersFailed()
