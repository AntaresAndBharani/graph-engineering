from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import json
import math
import re
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from orchestrator.config import (
    DEFAULT_HARNESS_QUOTAS,
    GlobalConfig,
    HarnessQuotaConfig,
    NodeConfig,
    ProjectConfig,
    QuotaSettings,
)
from orchestrator.logging import strip_ansi


@runtime_checkable
class TokenUsageReader(Protocol):
    """Protocol defining the required state manager interface for token usage and quota checks."""

    async def get_window_token_usage(self, harness_name: str, window_hours: float = 1.0) -> int:
        ...

    async def get_multi_window_usage(
        self,
        harness_name: str,
        short_window_hours: float = 5.0,
        long_window_hours: float = 168.0,
    ) -> tuple[int, int]:
        ...

    async def get_usage_breakdown(self, harness_name: str, window_hours: float = 1.0) -> Dict[str, Any]:
        ...

    async def get_token_usage_events(
        self,
        harness_name: Optional[str] = None,
        window_hours: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        ...


def fallback_token_heuristic(prompt: str = "", stdout: str = "") -> int:
    """
    Empirical fallback heuristic: returns max(1000, floor((len(prompt) + len(stdout)) / 4))
    or 0 if both prompt and stdout are empty.
    """
    total_chars = len(prompt or "") + len(stdout or "")
    if total_chars == 0:
        return 0
    return max(1000, math.floor(total_chars / 4))


def extract_token_usage(stdout: str, prompt: str = "") -> tuple[int, int, int]:
    """
    Extracts structured token usage (prompt_tokens, completion_tokens, total_tokens)
    from harness stdout using JSON parsing and regex patterns, falling back to
    empirical character-length heuristic: max(1000, floor((len(prompt) + len(stdout)) / 4)).
    """
    clean_out = strip_ansi(stdout) if stdout else ""
    clean_prompt = prompt or ""

    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0

    # 1. Structured JSON parsing
    if "{" in clean_out and "}" in clean_out:
        for match in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', clean_out):
            try:
                data = json.loads(match.group(0))
                if isinstance(data, dict):
                    usage = data.get("usage", data)
                    if isinstance(usage, dict):
                        p = usage.get("prompt_tokens") or usage.get("input_tokens") or usage.get("promptTokens")
                        c = usage.get("completion_tokens") or usage.get("output_tokens") or usage.get("completionTokens")
                        t = usage.get("total_tokens") or usage.get("totalTokens")
                        if p is not None and isinstance(p, (int, float)):
                            prompt_tokens = int(p)
                        if c is not None and isinstance(c, (int, float)):
                            completion_tokens = int(c)
                        if t is not None and isinstance(t, (int, float)):
                            total_tokens = int(t)
                        if prompt_tokens > 0 or completion_tokens > 0 or total_tokens > 0:
                            break
            except Exception:
                pass

    # 2. Regex search
    if prompt_tokens == 0:
        p_match = (
            re.search(r'(?:prompt|input)\s+tokens?:\s*([0-9,]+)', clean_out, re.IGNORECASE)
            or re.search(r'tokens?\s+(?:in|input|prompt):\s*([0-9,]+)', clean_out, re.IGNORECASE)
            or re.search(r'([0-9,]+)\s+(?:prompt|input)\s+tokens?', clean_out, re.IGNORECASE)
        )
        if p_match:
            try:
                prompt_tokens = int(p_match.group(1).replace(",", ""))
            except ValueError:
                pass

    if completion_tokens == 0:
        c_match = (
            re.search(r'(?:completion|output)\s+tokens?:\s*([0-9,]+)', clean_out, re.IGNORECASE)
            or re.search(r'tokens?\s+(?:out|output|completion):\s*([0-9,]+)', clean_out, re.IGNORECASE)
            or re.search(r'([0-9,]+)\s+(?:completion|output)\s+tokens?', clean_out, re.IGNORECASE)
        )
        if c_match:
            try:
                completion_tokens = int(c_match.group(1).replace(",", ""))
            except ValueError:
                pass

    if total_tokens == 0:
        t_match = (
            re.search(r'(?:total)\s+tokens?:\s*([0-9,]+)', clean_out, re.IGNORECASE)
            or re.search(r'tokens?\s+(?:used|total):\s*([0-9,]+)', clean_out, re.IGNORECASE)
            or re.search(r'([0-9,]+)\s+total\s+tokens?', clean_out, re.IGNORECASE)
            or re.search(r'consumed\s+([0-9,]+)\s+tokens?', clean_out, re.IGNORECASE)
            or re.search(r'([0-9,]+)\s+tokens\s+consumed', clean_out, re.IGNORECASE)
            or re.search(r'([0-9,]+)\s+tokens\s+used', clean_out, re.IGNORECASE)
            or re.search(r'Total tokens:\s*([0-9,]+)', clean_out, re.IGNORECASE)
        )
        if t_match:
            try:
                total_tokens = int(t_match.group(1).replace(",", ""))
            except ValueError:
                pass

    # 3. Derive total from prompt + completion or vice-versa
    if total_tokens == 0 and (prompt_tokens > 0 or completion_tokens > 0):
        total_tokens = prompt_tokens + completion_tokens
    elif total_tokens > 0 and prompt_tokens == 0 and completion_tokens == 0:
        prompt_tokens = int(total_tokens * 0.8)
        completion_tokens = total_tokens - prompt_tokens

    # 4. Fallback empirical heuristic
    if total_tokens == 0 and (clean_prompt or clean_out):
        fallback_total = fallback_token_heuristic(clean_prompt, clean_out)
        total_chars = len(clean_prompt) + len(clean_out)
        if total_chars > 0:
            prompt_tokens = int(fallback_total * (len(clean_prompt) / total_chars))
            completion_tokens = fallback_total - prompt_tokens
        else:
            prompt_tokens = 0
            completion_tokens = fallback_total
        total_tokens = fallback_total

    return prompt_tokens, completion_tokens, total_tokens


extract_token_counts = extract_token_usage


def calculate_required_runway(avg_tokens_per_hour: int, buffer_minutes: int) -> int:
    """
    Calculates RequiredRunway = avg_tokens_per_hour * (buffer_minutes / 60.0).
    """
    return int(avg_tokens_per_hour * (buffer_minutes / 60.0))


def calculate_remaining(limit: int, used: int) -> int:
    """
    Calculates RemainingQuota = max(0, limit - used).
    """
    return max(0, limit - used)


def calculate_velocity(used: int, window_hours: float) -> float:
    """
    Calculates V_burn = used / window_hours.
    """
    if window_hours <= 0:
        return 0.0
    return round(used / window_hours, 2)


def calculate_replenishment_eta(
    events: list[dict[str, Any]],
    used_tokens: int,
    limit: int,
    required_runway: int,
    window_hours: float,
    now: Optional[datetime] = None,
    avg_tokens_per_hour: int = 0,
) -> int:
    """
    Calculates replenishment countdown (in seconds) until enough token usage
    events age out of the rolling window to satisfy RequiredRunway.
    """
    target_max_used = limit - required_runway
    excess = used_tokens - target_max_used
    if excess <= 0:
        return 0

    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    accumulated = 0
    for ev in events:
        accumulated += ev.get("total_tokens", 0)
        if accumulated >= excess:
            created_at_raw = ev.get("created_at")
            if isinstance(created_at_raw, datetime):
                ev_dt = created_at_raw if created_at_raw.tzinfo else created_at_raw.replace(tzinfo=timezone.utc)
            elif isinstance(created_at_raw, str):
                try:
                    ev_dt = datetime.strptime(created_at_raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                except Exception:
                    continue
            elif isinstance(created_at_raw, (int, float)):
                ev_dt = datetime.fromtimestamp(created_at_raw, tz=timezone.utc)
            else:
                continue

            expiry_dt = ev_dt + timedelta(hours=window_hours)
            diff = int((expiry_dt - now_utc).total_seconds())
            return max(0, diff)

    # Fallback when events are absent or incomplete
    if avg_tokens_per_hour > 0 and excess > 0:
        return max(0, int((excess / avg_tokens_per_hour) * 3600))
    return int(window_hours * 3600)


@dataclass
class QuotaCheckResult:
    harness_name: str
    allowed: bool
    remaining: int
    required: int
    used: int
    limit: int
    velocity: float
    eta_seconds: int
    deficit: int
    window_hours: float

    @property
    def formatted_eta(self) -> str:
        if self.eta_seconds <= 0:
            return "0s"
        hours, remainder = divmod(self.eta_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        parts = []
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0 or hours > 0:
            parts.append(f"{minutes}m")
        if seconds > 0 or not parts:
            parts.append(f"{seconds}s")
        return " ".join(parts)


class QuotaManager:
    """
    Core engine for multi-window rolling quota calculations, velocity,
    runway gating, and replenishment ETA projections.
    """

    def __init__(self, config: GlobalConfig | QuotaSettings, state_manager: TokenUsageReader):
        if hasattr(config, "quota") and hasattr(config, "projects"):
            self.config = config
            self.quota_settings = config.quota
        elif hasattr(config, "harnesses") and hasattr(config, "buffer_minutes"):
            self.config = GlobalConfig(quota=config)
            self.quota_settings = config
        elif isinstance(config, GlobalConfig):
            self.config = config
            self.quota_settings = config.quota
        elif isinstance(config, QuotaSettings):
            self.config = GlobalConfig(quota=config)
            self.quota_settings = config
        else:
            raise TypeError(
                f"Invalid config type provided to QuotaManager: expected GlobalConfig or QuotaSettings, got {type(config).__name__}"
            )
        if state_manager is None:
            raise TypeError("state_manager cannot be None; a valid TokenUsageReader instance is required.")
        self.state_manager = state_manager

    @property
    def quota(self) -> QuotaSettings:
        return self.quota_settings

    def resolve_harness_for_node(self, project: ProjectConfig, node_name: str) -> str:
        """
        Resolves harness for the given node using ProjectConfig.nodes or NodeConfig default.
        """
        node_cfg = project.nodes.get(node_name)
        if node_cfg is None:
            return NodeConfig().harness
        if isinstance(node_cfg, dict):
            return str(node_cfg.get("harness") or NodeConfig().harness)
        return str(node_cfg.harness or NodeConfig().harness)

    async def check_harness_capacity(self, harness_name: str) -> QuotaCheckResult:
        """
        Calculates capacity, rolling usage, required runway, burn velocity,
        and replenishment ETA for the specified harness.
        """
        quota_cfg = self.quota_settings.harnesses.get(
            harness_name,
            DEFAULT_HARNESS_QUOTAS.get(
                harness_name,
                HarnessQuotaConfig(window_hours=1.0, window_token_limit=2_000_000, avg_tokens_per_hour=400_000),
            ),
        )
        window_hours = quota_cfg.window_hours
        limit = quota_cfg.window_token_limit
        avg_tph = quota_cfg.avg_tokens_per_hour
        buffer_minutes = self.quota_settings.buffer_minutes

        required_runway = calculate_required_runway(avg_tph, buffer_minutes)
        used_tokens = await self.state_manager.get_window_token_usage(harness_name, window_hours)

        remaining = calculate_remaining(limit, used_tokens)
        velocity = calculate_velocity(used_tokens, window_hours)

        allowed = remaining >= required_runway
        deficit = max(0, required_runway - remaining)

        eta_seconds = 0
        if not allowed:
            events = await self.state_manager.get_token_usage_events(
                harness_name=harness_name,
                window_hours=window_hours,
            )

            eta_seconds = calculate_replenishment_eta(
                events=events,
                used_tokens=used_tokens,
                limit=limit,
                required_runway=required_runway,
                window_hours=window_hours,
                avg_tokens_per_hour=avg_tph,
            )

        return QuotaCheckResult(
            harness_name=harness_name,
            allowed=allowed,
            remaining=remaining,
            required=required_runway,
            used=used_tokens,
            limit=limit,
            velocity=velocity,
            eta_seconds=eta_seconds,
            deficit=deficit,
            window_hours=window_hours,
        )

    async def get_informative_breakdown(self, harness_name: str) -> dict[str, Any]:
        """
        Returns percentage breakdowns by project_name and by node_name summing to 100%.
        """
        quota_cfg = self.quota_settings.harnesses.get(
            harness_name,
            DEFAULT_HARNESS_QUOTAS.get(
                harness_name,
                HarnessQuotaConfig(window_hours=1.0, window_token_limit=2_000_000, avg_tokens_per_hour=400_000),
            ),
        )
        raw = await self.state_manager.get_usage_breakdown(harness_name, quota_cfg.window_hours)

        raw_by_project = raw.get("by_project") or raw.get("projects") or {}
        raw_by_node = raw.get("by_node") or raw.get("nodes") or {}

        total_p = sum(raw_by_project.values())
        total_n = sum(raw_by_node.values())

        by_project: dict[str, float] = {}
        if total_p > 0:
            for p, val in raw_by_project.items():
                by_project[p] = round((val / total_p) * 100, 1)

        by_node: dict[str, float] = {}
        if total_n > 0:
            for n, val in raw_by_node.items():
                by_node[n] = round((val / total_n) * 100, 1)

        return {
            "by_project": by_project,
            "by_node": by_node,
            "raw_by_project": raw_by_project,
            "raw_by_node": raw_by_node,
            "total_tokens": total_p,
        }
