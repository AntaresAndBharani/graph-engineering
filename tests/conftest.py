from __future__ import annotations

import sys
from types import ModuleType

import pytest

import orchestrator.harness as harness_module
from orchestrator.logging import ProjectLogBufferManager


def _orchestrator_modules() -> dict[str, ModuleType]:
    return {
        name: mod
        for name, mod in list(sys.modules.items())
        if mod is not None and (name == "orchestrator" or name.startswith("orchestrator."))
    }


def _restore_module_namespaces(snapshots: dict[str, tuple[ModuleType, dict]]) -> None:
    """
    Undoes module-level rebinding done during a test, most notably `hot_reload_runtime()`,
    which `importlib.reload()`s orchestrator modules in place. A reload rebinds module globals
    (e.g. `harness._console`, `AsyncHarnessAdapter`) to fresh objects, leaving every test module
    that imported the originals holding stale references for the rest of the session.
    """
    for mod, namespace in snapshots.values():
        current = mod.__dict__
        changed = current.keys() != namespace.keys() or any(current[k] is not v for k, v in namespace.items())
        if changed:
            current.clear()
            current.update(namespace)


@pytest.fixture(autouse=True)
def isolate_test_state():
    snapshots = {name: (mod, dict(mod.__dict__)) for name, mod in _orchestrator_modules().items()}
    harness_module.AsyncHarnessAdapter.set_tui_mode(False)
    ProjectLogBufferManager.reset()
    yield
    _restore_module_namespaces(snapshots)
    harness_module.AsyncHarnessAdapter.set_tui_mode(False)
    harness_module.AsyncHarnessAdapter._stream_listeners.clear()
    ProjectLogBufferManager.reset()
