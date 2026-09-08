"""
Orchestrator pipeline nodes:
- Node 0: Consistency Supervisor (orchestrator.nodes.supervisor)
- Node 1: Architect (orchestrator.nodes.architect)
- Node 2: 3AmigosDevTest (orchestrator.nodes.devtest)
- Node 3: Reviewer Gatekeeper (orchestrator.nodes.reviewer)
- Node 4: BAU Maintenance (orchestrator.nodes.bau)
- Technical Debt Node: (orchestrator.nodes.tech_debt)
"""

from orchestrator.nodes.tech_debt import run_tech_debt_node

__all__ = ["run_tech_debt_node"]
