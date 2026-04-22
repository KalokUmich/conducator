"""Shared utilities for PR code review.

This module no longer hosts an orchestrator — the legacy
``CodeReviewService`` multi-agent pipeline was removed in favour of the
Brain-as-coordinator design (``app.agent_loop.pr_brain.PRBrainOrchestrator``).
What remains here is pure utilities reused by the v2 orchestrator:

* ``models`` — ``PRContext``, ``ChangedFile``, ``ReviewFinding``, ``RiskProfile``
* ``diff_parser`` — ``parse_diff`` + file-category classification
* ``risk_classifier`` — ``classify_risk`` over 5 dimensions
* ``dedup`` — ``dedup_findings`` merge pass
* ``ranking`` — ``score_and_rank`` composite scoring
* ``shared`` — ``parse_findings`` / ``evidence_gate`` shared output parsing
"""
