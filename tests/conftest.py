from __future__ import annotations

import pytest

from orchestrator.harness import AsyncHarnessAdapter
from orchestrator.logging import ProjectLogBufferManager


@pytest.fixture(autouse=True)
def isolate_test_state():
    AsyncHarnessAdapter._stream_listeners.clear()
    ProjectLogBufferManager.reset()
    yield
    AsyncHarnessAdapter._stream_listeners.clear()
    ProjectLogBufferManager.reset()
