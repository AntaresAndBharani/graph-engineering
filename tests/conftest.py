from __future__ import annotations

import pytest

from orchestrator.harness import AsyncHarnessAdapter
from orchestrator.logging import ProjectLogBufferManager


@pytest.fixture(autouse=True)
def isolate_test_state():
    AsyncHarnessAdapter.set_tui_mode(False)
    ProjectLogBufferManager.reset()
    yield
    AsyncHarnessAdapter.set_tui_mode(False)
    AsyncHarnessAdapter._stream_listeners.clear()
    ProjectLogBufferManager.reset()
