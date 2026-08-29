from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import re
from typing import Any

from orchestrator.config import GlobalConfig, HarnessQuotaConfig, ProjectConfig
from orchestrator.db import StateManager


def extract_token_usage(output: str, prompt: str = "") -> tuple[int, int, int]:
    """
    Extracts (prompt_tokens, completion_tokens, total_tokens) from CLI output
    using regex patterns, with fallback character-length estimation.
    """
    clean_out = output or ""
    clean_prompt = prompt or ""

    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0

    # 1. Search for JSON structured token metrics
    json_prompt = re.search(r'"(?:prompt_tokens|input_tokens)"\s*:\s*([0-9]+)', clean_out, re.IGNORECASE)
    json_comp = re.search(r'"(?:completion_tokens|output_tokens)"\s*:\s*([0-9]+)', clean_out, re.IGNORECASE)
    json_total = re.search(r'"total_tokens"\s*:\s*([0-9]+)', clean_out, re.IGNORECASE)

    if json_prompt:
        prompt_tokens = int(json_prompt.group(1))
    if json_comp:
        completion_tokens = int(json_comp.group(1))
    if json_total:
        total_tokens = int(json_total.group(1))

    # 2. Text regex patterns
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

    # 4. Fallback estimation based on character length (~4 chars per token)
    if total_tokens == 0 and (clean_prompt or clean_out):
        p_len = len(clean_prompt)
        o_len = len(clean_out)
        prompt_tokens = max(1, p_len // 4) if p_len > 0 else 0
        completion_tokens = max(1, o_len // 4) if o_len > 0 else 0
        total_tokens = prompt_tokens + completion_tokens

    return prompt_tokens, completion_tokens, total_tokens



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
