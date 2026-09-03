from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import json
import logging
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

_logger = logging.getLogger("orchestrator")


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


def calculate_runway(remaining_tokens: int, burn_rate: float | int) -> float:
    """
    Calculates operational runway in hours = remaining_tokens / burn_rate.
    Returns float('inf') when burn_rate <= 0 (idle) without raising ZeroDivisionError.
    """
    if burn_rate <= 0:
        return float("inf")
    return remaining_tokens / burn_rate


def format_runway(runway_hours: float) -> str:
    """
    Formats operational runway into human-readable string:
    - infinite / <= 0 / NaN: "Idle"
    - otherwise e.g. "12.7h" (or "12.6h")
    """
    if math.isinf(runway_hours) or math.isnan(runway_hours) or runway_hours < 0:
        return "Idle"
    return f"{runway_hours:.1f}h"


@dataclass
class RunwayForecast:
    runway_hours: float
    formatted: str
    burn_rate: float
    remaining_tokens: int

    @property
    def display(self) -> str:
        return self.formatted

    @property
    def is_idle(self) -> bool:
        return math.isinf(self.runway_hours) or self.burn_rate <= 0

    def __str__(self) -> str:
        return self.formatted


def calculate_operational_runway(
    remaining_tokens: int,
    burn_rate: float | int,
) -> RunwayForecast:
    """
    Calculates predictive operational runway forecast from remaining tokens and burn rate.
    Safely handles idle / zero burn rate without raising ZeroDivisionError.
    """
    hours = calculate_runway(remaining_tokens, burn_rate)
    formatted = format_runway(hours)
    return RunwayForecast(
        runway_hours=hours,
        formatted=formatted,
        burn_rate=float(burn_rate),
        remaining_tokens=remaining_tokens,
    )


def format_replenishment_countdown(
    eta_seconds: int,
    now: Optional[datetime] = None,
    reset_time: Optional[datetime] = None,
    window_hours: Optional[float] = None,
) -> str:
    """
    Formats replenishment countdown into human-readable string:
    - eta_seconds <= 0: "Full Capacity (0s)"
    - short ETAs (<24h / <86400s when no reset_time and window_hours < 24):
        - <3600s: "Resets in <N> min" (or "Resets in <N>s" if <60s)
        - >=3600s: "Resets in <H>h <M>m" or "Resets in <H>h"
    - longer/weekly ETAs (>=86400s or window_hours >= 24 or reset_time provided):
        - "Resets Sun, 00:00" (formatted as %a, %H:%M)
    """
    if eta_seconds <= 0:
        return "Full Capacity (0s)"

    # Longer / weekly ETA formatting
    if reset_time is not None or eta_seconds >= 86400 or (window_hours is not None and window_hours >= 24):
        target_dt = reset_time
        if target_dt is None:
            ref_now = now or datetime.now(timezone.utc)
            if ref_now.tzinfo is None:
                ref_now = ref_now.replace(tzinfo=timezone.utc)
            target_dt = ref_now + timedelta(seconds=eta_seconds)
        return target_dt.strftime("Resets %a, %H:%M")

    # Short ETA formatting
    if eta_seconds < 60:
        return f"Resets in {eta_seconds}s"
    elif eta_seconds < 3600:
        minutes = max(1, round(eta_seconds / 60))
        return f"Resets in {minutes} min"
    else:
        hours = eta_seconds // 3600
        mins = round((eta_seconds % 3600) / 60)
        if mins > 0:
            return f"Resets in {hours}h {mins}m"
        return f"Resets in {hours}h"


format_reset_countdown = format_replenishment_countdown


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
class WindowMetric:
    window_hours: float
    limit: int
    used: int
    remaining: int
    percentage: float
    eta_seconds: int = 0
    formatted_countdown: str = "Full Capacity (0s)"

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)


@dataclass
class DashboardQuotaMetrics:
    harness_name: str
    short_window: WindowMetric
    weekly_window: Optional[WindowMetric] = None
    runway_forecast: Optional[RunwayForecast] = None

    @property
    def short(self) -> WindowMetric:
        return self.short_window

    @property
    def weekly(self) -> Optional[WindowMetric]:
        return self.weekly_window

    @property
    def runway_hours(self) -> float:
        return self.runway_forecast.runway_hours if self.runway_forecast else float("inf")

    @property
    def runway_display(self) -> str:
        return self.runway_forecast.formatted if self.runway_forecast else "Idle"

    @property
    def remaining_short(self) -> int:
        return self.short_window.remaining

    @property
    def remaining_weekly(self) -> int:
        return self.weekly_window.remaining if self.weekly_window else 0

    @property
    def percentage_short(self) -> float:
        return self.short_window.percentage

    @property
    def percentage_weekly(self) -> float:
        return self.weekly_window.percentage if self.weekly_window else 0.0

    def __getitem__(self, item: str) -> Any:
        if item in ("short", "short_window"):
            return self.short_window
        if item in ("weekly", "weekly_window"):
            return self.weekly_window
        return getattr(self, item)


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
    def is_critical(self) -> bool:
        return self.limit > 0 and (self.remaining / self.limit) < 0.15

    @property
    def formatted_countdown(self) -> str:
        return format_replenishment_countdown(self.eta_seconds, window_hours=self.window_hours)

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

    def update_config(self, config: GlobalConfig | QuotaSettings) -> None:
        """Dynamically updates held GlobalConfig and QuotaSettings."""
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

        if limit > 0 and (remaining / limit) < 0.15:
            _logger.warning("[quota:%s] Quota critical (<15%% remaining).", harness_name)

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

    async def calculate_dashboard_metrics(
        self,
        harness_name: str,
        burn_rate: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> DashboardQuotaMetrics:
        """
        Evaluates dual-window (short-window and weekly) quota metrics, predictive
        operational runway forecast, and human-readable replenishment countdowns.
        """
        quota_cfg = self.quota_settings.harnesses.get(
            harness_name,
            DEFAULT_HARNESS_QUOTAS.get(
                harness_name,
                HarnessQuotaConfig(window_hours=1.0, window_token_limit=2_000_000, avg_tokens_per_hour=400_000),
            ),
        )
        short_hours = quota_cfg.window_hours
        short_limit = quota_cfg.window_token_limit
        weekly_cfg = quota_cfg.weekly
        long_hours = weekly_cfg.hours if weekly_cfg else 168.0
        long_limit = weekly_cfg.token_limit if weekly_cfg else 0

        short_used, long_used = await self.state_manager.get_multi_window_usage(
            harness_name,
            short_window_hours=short_hours,
            long_window_hours=long_hours,
        )

        short_remaining = calculate_remaining(short_limit, short_used)
        short_percentage = round((short_remaining / short_limit) * 100, 1) if short_limit > 0 else 0.0

        if short_limit > 0 and (short_remaining / short_limit) < 0.15:
            _logger.warning("[quota:%s] Quota critical (<15%% remaining).", harness_name)

        required_runway = calculate_required_runway(quota_cfg.avg_tokens_per_hour, self.quota_settings.buffer_minutes)
        short_eta = 0
        if short_remaining < required_runway:
            events = await self.state_manager.get_token_usage_events(
                harness_name=harness_name,
                window_hours=short_hours,
            )
            short_eta = calculate_replenishment_eta(
                events=events,
                used_tokens=short_used,
                limit=short_limit,
                required_runway=required_runway,
                window_hours=short_hours,
                now=now,
                avg_tokens_per_hour=quota_cfg.avg_tokens_per_hour,
            )

        short_countdown = format_replenishment_countdown(short_eta, now=now, window_hours=short_hours)
        short_metric = WindowMetric(
            window_hours=short_hours,
            limit=short_limit,
            used=short_used,
            remaining=short_remaining,
            percentage=short_percentage,
            eta_seconds=short_eta,
            formatted_countdown=short_countdown,
        )

        weekly_metric: Optional[WindowMetric] = None
        if weekly_cfg:
            weekly_remaining = calculate_remaining(long_limit, long_used)
            weekly_percentage = round((weekly_remaining / long_limit) * 100, 1) if long_limit > 0 else 0.0
            weekly_eta = 0
            if weekly_remaining < required_runway:
                events_weekly = await self.state_manager.get_token_usage_events(
                    harness_name=harness_name,
                    window_hours=long_hours,
                )
                weekly_eta = calculate_replenishment_eta(
                    events=events_weekly,
                    used_tokens=long_used,
                    limit=long_limit,
                    required_runway=required_runway,
                    window_hours=long_hours,
                    now=now,
                    avg_tokens_per_hour=quota_cfg.avg_tokens_per_hour,
                )
            weekly_countdown = format_replenishment_countdown(weekly_eta, now=now, window_hours=long_hours)
            weekly_metric = WindowMetric(
                window_hours=long_hours,
                limit=long_limit,
                used=long_used,
                remaining=weekly_remaining,
                percentage=weekly_percentage,
                eta_seconds=weekly_eta,
                formatted_countdown=weekly_countdown,
            )

        eff_burn_rate = burn_rate if burn_rate is not None else float(quota_cfg.avg_tokens_per_hour)
        forecast = calculate_operational_runway(short_remaining, eff_burn_rate)

        return DashboardQuotaMetrics(
            harness_name=harness_name,
            short_window=short_metric,
            weekly_window=weekly_metric,
            runway_forecast=forecast,
        )

    async def calculate_runway_forecast(
        self,
        harness_name: str,
        burn_rate: Optional[float] = None,
    ) -> RunwayForecast:
        """
        Calculates operational runway forecast for the specified harness.
        """
        quota_cfg = self.quota_settings.harnesses.get(
            harness_name,
            DEFAULT_HARNESS_QUOTAS.get(
                harness_name,
                HarnessQuotaConfig(window_hours=1.0, window_token_limit=2_000_000, avg_tokens_per_hour=400_000),
            ),
        )
        used_tokens = await self.state_manager.get_window_token_usage(harness_name, quota_cfg.window_hours)
        remaining = calculate_remaining(quota_cfg.window_token_limit, used_tokens)
        eff_burn_rate = burn_rate if burn_rate is not None else float(quota_cfg.avg_tokens_per_hour)
        return calculate_operational_runway(remaining, eff_burn_rate)

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
