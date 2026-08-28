from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator


class HarnessConfig(BaseModel):
    binary: str
    args: List[str] = Field(default_factory=list)
    model_flag: Optional[str] = None
    effort_flag: Optional[str] = None
    timeout_minutes: int = 30
    retry_on_failure: int = 1
    env_vars: Dict[str, str] = Field(default_factory=dict)


class LabelConfig(BaseModel):
    name: str
    color: str = "ededed"
    description: str = ""


class NodeConfig(BaseModel):
    enabled: bool = True
    harness: str = "claude"
    model: Optional[str] = None
    effort: Optional[str] = None
    label_trigger: Optional[str] = None
    label_output: Optional[str] = None
    processed_label: Optional[str] = None
    branch_prefix: Optional[str] = "feat/issue-"
    auto_merge_approved: bool = True


def resolve_path(v: str | Path) -> Path:
    """
    Robustly expands paths across platforms, supporting:
    - Tilde (~) expansion
    - POSIX $HOME / ${HOME} variables even when running in Windows shells
    - Windows %USERPROFILE% / %APPDATA% variables on Linux/Windows
    - Any custom environment variables ($VAR, %VAR%)
    - Relative paths resolved against current working directory
    """
    if isinstance(v, Path):
        raw = str(v)
    else:
        raw = str(v)

    # 1. Expand ~ to user home
    expanded = os.path.expanduser(raw)

    # 2. Cross-platform home alias resolution ($HOME on Windows or %USERPROFILE% on POSIX)
    home_dir = str(Path.home())
    if "$HOME" in expanded:
        expanded = expanded.replace("$HOME", home_dir)
    if "${HOME}" in expanded:
        expanded = expanded.replace("${HOME}", home_dir)
    if "%USERPROFILE%" in expanded:
        expanded = expanded.replace("%USERPROFILE%", home_dir)

    # 3. Expand remaining environment variables
    expanded = os.path.expandvars(expanded)

    return Path(expanded).resolve()


class ProjectConfig(BaseModel):
    name: str
    repo: str
    local_path: Path
    enabled: bool = True
    context_files: List[str] = Field(default_factory=list)
    nodes: Dict[str, NodeConfig] = Field(default_factory=dict)

    @field_validator("local_path", mode="before")
    @classmethod
    def expand_local_path(cls, v: str | Path) -> Path:
        return resolve_path(v)


class SettingsConfig(BaseModel):
    poll_interval_seconds: int = 300
    supervisor_interval_seconds: int = 3600
    max_concurrent_jobs: int = 4
    db_path: str = "~/.config/orchestrator/state.db"
    log_dir: str = "~/.config/orchestrator/logs"
    log_level: str = "INFO"

    @property
    def resolved_db_path(self) -> Path:
        p = resolve_path(self.db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def resolved_log_dir(self) -> Path:
        p = resolve_path(self.log_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


DEFAULT_MANAGED_LABELS: List[LabelConfig] = [
    LabelConfig(name="needs-triage", color="E2B7E1", description="Awaiting Architect Node triage"),
    LabelConfig(name="ready-for-dev", color="0E8A16", description="Awaiting 3Amigos DevTest implementation"),
    LabelConfig(name="needs-architect-review", color="FBCA04", description="PR submitted, ready for review"),
    LabelConfig(name="dev-implemented", color="C2E0C6", description="Implementation completed by DevTest node"),
    LabelConfig(name="orchestration-failed", color="B60205", description="AI Harness execution failed"),
    LabelConfig(name="needs-po-review", color="D93F0B", description="Supervisor flagged methodological conflict"),
    LabelConfig(name="architect-processed", color="D4C5F9", description="Architect decomposition complete"),
]

DEFAULT_HARNESSES: Dict[str, HarnessConfig] = {
    "claude": HarnessConfig(
        binary="claude",
        args=["-p", "{prompt}", "--dangerously-skip-permissions"],
        model_flag="--model",
        effort_flag="--effort",
        timeout_minutes=30,
        retry_on_failure=1,
    ),
    "antigravity": HarnessConfig(
        binary="agy",
        args=["-p", "{prompt}", "--dangerously-skip-permissions", "--print-timeout", "45m"],
        model_flag="--model",
        effort_flag="--effort",
        timeout_minutes=45,
        retry_on_failure=1,
    ),
    "devin": HarnessConfig(
        binary="devin",
        args=["-p", "{prompt}", "--permission-mode", "bypass"],
        model_flag="--model",
        timeout_minutes=60,
        retry_on_failure=1,
    ),
}


class GlobalConfig(BaseModel):
    version: int = 2
    settings: SettingsConfig = Field(default_factory=SettingsConfig)
    managed_labels: List[LabelConfig] = Field(default_factory=lambda: list(DEFAULT_MANAGED_LABELS))
    harnesses: Dict[str, HarnessConfig] = Field(default_factory=lambda: dict(DEFAULT_HARNESSES))
    projects: List[ProjectConfig] = Field(default_factory=list)


def get_default_config_search_paths() -> List[Path]:
    paths: List[Path] = []
    # 1. Custom / Current directory
    paths.append(Path("config.yaml").resolve())
    paths.append(Path(".orchestrator.yaml").resolve())

    # 2. User config path ~/.config/orchestrator/config.yaml
    home = Path.home()
    paths.append(home / ".config" / "orchestrator" / "config.yaml")

    # 3. Windows userprofile fallback ~/.orchestrator/config.yaml
    paths.append(home / ".orchestrator" / "config.yaml")
    return paths


def find_config_file(custom_path: Optional[Path | str] = None) -> Optional[Path]:
    if custom_path:
        p = resolve_path(custom_path)
        if p.exists() and p.is_file():
            return p
        raise FileNotFoundError(f"Specified configuration file does not exist: {p}")

    for candidate in get_default_config_search_paths():
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def load_config(custom_path: Optional[Path | str] = None) -> GlobalConfig:
    """
    Loads configuration from custom path or default paths, loading .env if present.
    """
    load_dotenv()

    config_path = find_config_file(custom_path)
    if not config_path:
        # Return default config if no file found yet
        return GlobalConfig()

    with open(config_path, "r", encoding="utf-8") as f:
        raw_data = yaml.safe_load(f) or {}

    # Merge default labels and harnesses if omitted in user config
    if "managed_labels" not in raw_data or not raw_data["managed_labels"]:
        raw_data["managed_labels"] = [l.model_dump() for l in DEFAULT_MANAGED_LABELS]

    if "harnesses" not in raw_data or not raw_data["harnesses"]:
        raw_data["harnesses"] = {k: v.model_dump() for k, v in DEFAULT_HARNESSES.items()}
    else:
        # Merge defaults for standard names
        for k, v in DEFAULT_HARNESSES.items():
            if k not in raw_data["harnesses"]:
                raw_data["harnesses"][k] = v.model_dump()

    return GlobalConfig(**raw_data)
