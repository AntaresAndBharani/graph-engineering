from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any

from orchestrator.config import GlobalConfig, HarnessQuotaConfig, ProjectConfig
from orchestrator.db import StateManager


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

    def __init__(self, config: GlobalConfig, state_manager: StateManager):
        self.config = config
        self.state_manager = state_manager

    def resolve_harness_for_node(self, project: ProjectConfig, node_name: str) -> str:
        """
        Inspects project.nodes[node_name].harness or falls back to global default.
        """
        if node_name in project.nodes:
            node_cfg = project.nodes[node_name]
            if node_cfg.harness:
                return node_cfg.harness
        # Fallback default per architecture standards
        if node_name in ("architect", "supervisor", "reviewer"):
            return "claude"
        return "antigravity"

    async def check_harness_capacity(self, harness_name: str) -> QuotaCheckResult:
        """
        Calculates capacity, rolling usage, required runway, burn velocity,
        and replenishment ETA for the specified harness.
        """
        quota_cfg = self.config.quota.harnesses.get(
            harness_name,
            HarnessQuotaConfig(window_hours=1.0, window_token_limit=2_000_000, avg_tokens_per_hour=400_000),
        )
        window_hours = quota_cfg.window_hours
        limit = quota_cfg.window_token_limit
        avg_tph = quota_cfg.avg_tokens_per_hour
        buffer_minutes = self.config.quota.buffer_minutes

        required_runway = int(avg_tph * (buffer_minutes / 60.0))
        used_tokens = await self.state_manager.get_window_token_sum(harness_name, window_hours)
        remaining = max(0, limit - used_tokens)
        velocity = round(used_tokens / window_hours, 2) if window_hours > 0 else 0.0

        allowed = remaining >= required_runway
        deficit = max(0, required_runway - remaining)

        eta_seconds = 0
        if not allowed:
            # Need used_tokens to drop so remaining >= required_runway
            # i.e., limit - new_used >= required_runway  =>  new_used <= limit - required_runway
            # excess tokens to age out = used_tokens - (limit - required_runway)
            target_max_used = limit - required_runway
            excess = used_tokens - target_max_used
            events = await self.state_manager.get_window_events(harness_name, window_hours)

            now_utc = datetime.now(timezone.utc)
            accumulated = 0
            found_eta = False

            for ev in events:
                accumulated += ev.get("total_tokens", 0)
                if accumulated >= excess:
                    # This event aging out clears the throttle
                    created_at_str = ev.get("created_at", "")
                    try:
                        ev_dt = datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                        expiry_dt = ev_dt + timedelta(hours=window_hours)
                        diff = int((expiry_dt - now_utc).total_seconds())
                        eta_seconds = max(0, diff)
                        found_eta = True
                        break
                    except Exception:
                        pass

            if not found_eta:
                # Fallback estimation based on avg burn rate or window fraction
                if avg_tph > 0 and excess > 0:
                    eta_seconds = max(0, int((excess / avg_tph) * 3600))
                else:
                    eta_seconds = int(window_hours * 3600)

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
        quota_cfg = self.config.quota.harnesses.get(
            harness_name,
            HarnessQuotaConfig(window_hours=1.0, window_token_limit=2_000_000, avg_tokens_per_hour=400_000),
        )
        raw = await self.state_manager.get_window_breakdown(harness_name, quota_cfg.window_hours)
        total_p = sum(raw["by_project"].values())
        total_n = sum(raw["by_node"].values())

        by_project: dict[str, float] = {}
        if total_p > 0:
            for p, val in raw["by_project"].items():
                by_project[p] = round((val / total_p) * 100, 1)

        by_node: dict[str, float] = {}
        if total_n > 0:
            for n, val in raw["by_node"].items():
                by_node[n] = round((val / total_n) * 100, 1)

        return {
            "by_project": by_project,
            "by_node": by_node,
            "raw_by_project": raw["by_project"],
            "raw_by_node": raw["by_node"],
            "total_tokens": total_p,
        }
