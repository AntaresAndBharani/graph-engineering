from __future__ import annotations

from pathlib import Path
import pytest


def get_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def test_architecture_md_depicts_2_node_parallel_topology_and_optional_nodes():
    """
    Scenario: Documentation and Label Taxonomy Harmonization
      Given the updated codebase
      When reviewing .graph/architecture.md
      Then the documentation must accurately depict the 2-node parallel flow (Architect -> DevTest)
      And legacy reviewer/supervisor/bau guides must be marked with optional/disabled callout notices.
    """
    repo_root = get_repo_root()
    arch_md = repo_root / ".graph" / "architecture.md"
    assert arch_md.exists(), ".graph/architecture.md must exist"

    content = arch_md.read_text(encoding="utf-8")

    # Verify 2-node parallel topology depiction
    assert "2-Node Parallel Engine" in content or "2-node parallel" in content.lower()
    assert "Node 1: Architect" in content
    assert "Node 2: 3-Amigos DevTest" in content or "DevTest" in content

    # Verify optional/disabled markings for legacy governance nodes
    assert "Optional / Disabled" in content or "Optional / Disabled by Default" in content
    assert "Supervisor" in content and ("Optional" in content or "Disabled" in content)
    assert "Reviewer" in content and ("Optional" in content or "Disabled" in content)
    assert "BAU" in content and ("Optional" in content or "Disabled" in content)


def test_readme_md_depicts_2_node_parallel_topology():
    """
    Asserts README.md reflects the 2-node parallel engine by default and marks optional nodes as disabled.
    """
    repo_root = get_repo_root()
    readme_md = repo_root / "README.md"
    assert readme_md.exists(), "README.md must exist"

    content = readme_md.read_text(encoding="utf-8")

    # Verify 2-node parallel engine depiction
    assert "2-Node Parallel Engine" in content or "2-node parallel" in content.lower()
    assert "Architect" in content
    assert "DevTest" in content

    # Verify config example has supervisor, reviewer, and bau disabled by default
    assert "supervisor:\n        enabled: false" in content or "enabled: false" in content
    assert "reviewer:\n        enabled: false" in content
    assert "bau:\n        enabled: false" in content
    assert "architect:\n        enabled: true" in content
    assert "devtest:\n        enabled: true" in content


@pytest.mark.parametrize(
    "doc_filename,expected_node_name",
    [
        ("node-reviewer.md", "Reviewer"),
        ("node-supervisor.md", "Supervisor"),
        ("node-bau.md", "BAU"),
    ],
)
def test_legacy_node_docs_have_optional_disabled_callouts(doc_filename: str, expected_node_name: str):
    """
    Asserts that docs/node-reviewer.md, docs/node-supervisor.md, and docs/node-bau.md
    contain the optional/disabled callout notice at the top.
    """
    repo_root = get_repo_root()
    doc_path = repo_root / "docs" / doc_filename
    assert doc_path.exists(), f"{doc_path} must exist"

    content = doc_path.read_text(encoding="utf-8")

    # Verify Callout block
    assert "Optional / Disabled by Default" in content or "Optional / Disabled" in content
    assert "enabled: false" in content
    assert expected_node_name in content


def test_active_node_docs_exist():
    """
    Asserts that core active node docs (node-architect.md, node-devtest.md) exist and describe active behavior.
    """
    repo_root = get_repo_root()
    for doc in ["node-architect.md", "node-devtest.md"]:
        doc_path = repo_root / "docs" / doc
        assert doc_path.exists(), f"{doc_path} must exist"
        content = doc_path.read_text(encoding="utf-8")
        assert "enabled: true" in content


def test_local_cli_pipeline_doc_topology_harmonization():
    """
    Asserts that docs/local-cli-pipeline.md accurately depicts the 2-node parallel engine.
    """
    repo_root = get_repo_root()
    pipeline_doc = repo_root / "docs" / "local-cli-pipeline.md"
    assert pipeline_doc.exists(), "docs/local-cli-pipeline.md must exist"

    content = pipeline_doc.read_text(encoding="utf-8")
    assert "2-Node Parallel Engine" in content or "2-node parallel" in content.lower()
    assert "enabled: false" in content
