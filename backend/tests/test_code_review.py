"""Tests for the AI Code Review module.

Covers:
  - Diff parser (file classification, PRContext construction)
  - Risk classifier (5-dimension risk profile)
  - Dedup / merge layer
  - Ranking / scoring layer
  - Agent spec selection and query building
  - Service orchestration (with mocked agents)
  - API endpoint schemas
  - Impact graph context injection
  - Adversarial verification (defense attorney pass)
"""

from unittest.mock import MagicMock, patch

from app.code_review.models import (
    ChangedFile,
    FileCategory,
    FindingCategory,
    PRContext,
    ReviewFinding,
    RiskLevel,
    RiskProfile,
    Severity,
)

# =========================================================================
# Diff Parser — file classification
# =========================================================================


class TestFileClassification:
    def test_python_test_file(self):
        from app.code_review.diff_parser import _classify_file

        assert _classify_file("tests/test_auth.py") == FileCategory.TEST

    def test_java_test_file(self):
        from app.code_review.diff_parser import _classify_file

        assert _classify_file("src/test/java/AuthServiceTest.java") == FileCategory.TEST

    def test_js_spec_file(self):
        from app.code_review.diff_parser import _classify_file

        assert _classify_file("src/auth.spec.ts") == FileCategory.TEST

    def test_yaml_config(self):
        from app.code_review.diff_parser import _classify_file

        assert _classify_file("config/application.yml") == FileCategory.CONFIG

    def test_env_file(self):
        from app.code_review.diff_parser import _classify_file

        assert _classify_file(".env.production") == FileCategory.CONFIG

    def test_dockerfile(self):
        from app.code_review.diff_parser import _classify_file

        assert _classify_file("Dockerfile") == FileCategory.INFRA

    def test_github_workflow(self):
        from app.code_review.diff_parser import _classify_file

        assert _classify_file(".github/workflows/ci.yml") == FileCategory.INFRA

    def test_migration_file(self):
        from app.code_review.diff_parser import _classify_file

        assert _classify_file("alembic/versions/001_init.py") == FileCategory.SCHEMA

    def test_sql_file(self):
        from app.code_review.diff_parser import _classify_file

        assert _classify_file("db/schema.sql") == FileCategory.SCHEMA

    def test_lock_file(self):
        from app.code_review.diff_parser import _classify_file

        assert _classify_file("package-lock.json") == FileCategory.GENERATED

    def test_vendor_dir(self):
        from app.code_review.diff_parser import _classify_file

        assert _classify_file("vendor/github.com/pkg/errors/errors.go") == FileCategory.GENERATED

    def test_business_logic(self):
        from app.code_review.diff_parser import _classify_file

        assert _classify_file("app/services/auth_service.py") == FileCategory.BUSINESS_LOGIC

    def test_java_controller(self):
        from app.code_review.diff_parser import _classify_file

        assert _classify_file("src/main/java/com/app/UserController.java") == FileCategory.BUSINESS_LOGIC

    def test_go_test(self):
        from app.code_review.diff_parser import _classify_file

        assert _classify_file("pkg/auth/handler_test.go") == FileCategory.TEST


class TestDiffParser:
    def test_parse_diff_empty(self):
        from app.code_review.diff_parser import parse_diff

        with patch("app.code_review.diff_parser.git_diff_files") as mock_gdf:
            mock_gdf.return_value = MagicMock(success=True, data=[])
            ctx = parse_diff("/fake/ws", "main...feature")
            assert ctx.file_count == 0
            assert ctx.total_changed_lines == 0

    def test_parse_diff_with_files(self):
        from app.code_review.diff_parser import parse_diff

        with patch("app.code_review.diff_parser.git_diff_files") as mock_gdf:
            mock_gdf.return_value = MagicMock(
                success=True,
                data=[
                    {"path": "app/service.py", "status": "modified", "additions": 30, "deletions": 10},
                    {"path": "tests/test_service.py", "status": "modified", "additions": 20, "deletions": 5},
                    {"path": "config/settings.yml", "status": "modified", "additions": 2, "deletions": 1},
                ],
            )
            ctx = parse_diff("/fake/ws", "main...feature")
            assert ctx.file_count == 3
            assert ctx.total_additions == 52
            assert ctx.total_deletions == 16
            assert ctx.total_changed_lines == 68
            assert len(ctx.business_logic_files()) == 1
            assert len(ctx.test_files()) == 1
            assert len(ctx.config_files()) == 1

    def test_parse_diff_failure(self):
        from app.code_review.diff_parser import parse_diff

        with patch("app.code_review.diff_parser.git_diff_files") as mock_gdf:
            mock_gdf.return_value = MagicMock(success=False, error="bad ref", data=None)
            ctx = parse_diff("/fake/ws", "bad...ref")
            assert ctx.file_count == 0


# =========================================================================
# Risk Classifier
# =========================================================================


class TestRiskClassifier:
    def _make_context(self, paths):
        files = [ChangedFile(path=p, additions=50, deletions=20) for p in paths]
        return PRContext(
            diff_spec="main...feature",
            files=files,
            total_additions=sum(f.additions for f in files),
            total_deletions=sum(f.deletions for f in files),
            total_changed_lines=sum(f.additions + f.deletions for f in files),
            file_count=len(files),
        )

    def test_low_risk_simple_change(self):
        from app.code_review.risk_classifier import classify_risk

        ctx = self._make_context(["app/utils.py", "app/helpers.py"])
        risk = classify_risk(ctx)
        assert risk.correctness == RiskLevel.LOW

    def test_security_risk_from_auth_file(self):
        from app.code_review.risk_classifier import classify_risk

        ctx = self._make_context(
            [
                "app/auth/login.py",
                "app/auth/session.py",
                "app/auth/jwt_handler.py",
            ]
        )
        risk = classify_risk(ctx)
        assert risk.security in (RiskLevel.MEDIUM, RiskLevel.HIGH)

    def test_concurrency_risk_from_queue_consumer(self):
        from app.code_review.risk_classifier import classify_risk

        ctx = self._make_context(
            [
                "app/consumers/order_consumer.py",
                "app/handlers/webhook_handler.py",
                "app/workers/retry_worker.py",
            ]
        )
        risk = classify_risk(ctx)
        assert risk.concurrency in (RiskLevel.MEDIUM, RiskLevel.HIGH)

    def test_correctness_boosted_for_large_prs(self):
        from app.code_review.risk_classifier import classify_risk

        files = [
            ChangedFile(path=f"app/service_{i}.py", additions=100, deletions=50, category=FileCategory.BUSINESS_LOGIC)
            for i in range(12)
        ]
        ctx = PRContext(
            diff_spec="main...feature",
            files=files,
            total_additions=1200,
            total_deletions=600,
            total_changed_lines=1800,
            file_count=12,
        )
        risk = classify_risk(ctx)
        assert risk.correctness in (RiskLevel.MEDIUM, RiskLevel.HIGH)

    def test_operational_risk_from_config_changes(self):
        from app.code_review.risk_classifier import classify_risk

        ctx = self._make_context(
            [
                "config/app.yml",
                "config/db.yml",
                "config/cache.yml",
                "app/service.py",
            ]
        )
        risk = classify_risk(ctx)
        assert risk.operational in (RiskLevel.MEDIUM, RiskLevel.HIGH)


# =========================================================================
# Dedup
# =========================================================================


class TestDedup:
    def test_no_dedup_for_different_files(self):
        from app.code_review.dedup import dedup_findings

        findings = [
            ReviewFinding(
                title="Bug in auth",
                category=FindingCategory.CORRECTNESS,
                severity=Severity.WARNING,
                file="auth.py",
                start_line=10,
                end_line=20,
            ),
            ReviewFinding(
                title="Bug in service",
                category=FindingCategory.CORRECTNESS,
                severity=Severity.WARNING,
                file="service.py",
                start_line=10,
                end_line=20,
            ),
        ]
        result = dedup_findings(findings)
        assert len(result) == 2

    def test_dedup_overlapping_lines(self):
        from app.code_review.dedup import dedup_findings

        findings = [
            ReviewFinding(
                title="Race condition",
                category=FindingCategory.CONCURRENCY,
                severity=Severity.CRITICAL,
                confidence=0.9,
                file="handler.py",
                start_line=10,
                end_line=30,
                evidence=["check then act"],
                agent="concurrency",
            ),
            ReviewFinding(
                title="Race condition risk",
                category=FindingCategory.SECURITY,
                severity=Severity.WARNING,
                confidence=0.7,
                file="handler.py",
                start_line=15,
                end_line=25,
                evidence=["replay attack"],
                agent="security",
            ),
        ]
        result = dedup_findings(findings)
        assert len(result) == 1
        # Should keep the critical severity
        assert result[0].severity == Severity.CRITICAL
        # Evidence merged
        assert len(result[0].evidence) == 2
        # Both agents attributed
        assert "concurrency" in result[0].agent
        assert "security" in result[0].agent

    def test_dedup_similar_titles(self):
        from app.code_review.dedup import dedup_findings

        findings = [
            ReviewFinding(
                title="Missing null check in handler",
                category=FindingCategory.CORRECTNESS,
                severity=Severity.WARNING,
                confidence=0.8,
                file="handler.py",
                agent="correctness",
            ),
            ReviewFinding(
                title="Null check missing in handler",
                category=FindingCategory.CORRECTNESS,
                severity=Severity.NIT,
                confidence=0.6,
                file="handler.py",
                agent="reliability",
            ),
        ]
        result = dedup_findings(findings)
        assert len(result) == 1

    def test_single_finding_no_dedup(self):
        from app.code_review.dedup import dedup_findings

        findings = [
            ReviewFinding(title="Test", category=FindingCategory.CORRECTNESS, severity=Severity.WARNING),
        ]
        assert len(dedup_findings(findings)) == 1

    def test_empty_findings(self):
        from app.code_review.dedup import dedup_findings

        assert dedup_findings([]) == []


# =========================================================================
# Ranking
# =========================================================================


class TestRanking:
    def test_critical_ranked_first(self):
        from app.code_review.ranking import score_and_rank

        pr_ctx = PRContext(
            diff_spec="main...f",
            files=[
                ChangedFile(path="auth.py", additions=50, deletions=10, category=FileCategory.BUSINESS_LOGIC),
            ],
            file_count=1,
        )

        findings = [
            ReviewFinding(
                title="Nit",
                category=FindingCategory.STYLE,
                severity=Severity.NIT,
                confidence=0.9,
                file="auth.py",
                start_line=1,
            ),
            ReviewFinding(
                title="Critical bug",
                category=FindingCategory.CORRECTNESS,
                severity=Severity.CRITICAL,
                confidence=0.9,
                file="auth.py",
                start_line=10,
            ),
            ReviewFinding(
                title="Warning",
                category=FindingCategory.SECURITY,
                severity=Severity.WARNING,
                confidence=0.8,
                file="auth.py",
                start_line=5,
            ),
        ]
        ranked = score_and_rank(findings, pr_ctx)
        assert ranked[0].severity == Severity.CRITICAL
        assert ranked[-1].severity == Severity.NIT

    def test_praise_ranked_last(self):
        from app.code_review.ranking import score_and_rank

        pr_ctx = PRContext(diff_spec="main...f", files=[], file_count=0)

        findings = [
            ReviewFinding(
                title="Good job", category=FindingCategory.CORRECTNESS, severity=Severity.PRAISE, file="x.py"
            ),
            ReviewFinding(
                title="Bug",
                category=FindingCategory.CORRECTNESS,
                severity=Severity.WARNING,
                confidence=0.8,
                file="x.py",
            ),
        ]
        ranked = score_and_rank(findings, pr_ctx)
        assert ranked[-1].severity == Severity.PRAISE

    def test_evidence_quality_boosts_score(self):
        from app.code_review.ranking import _evidence_quality

        # Finding with file, line, evidence, and fix
        f = ReviewFinding(
            title="Bug",
            category=FindingCategory.CORRECTNESS,
            severity=Severity.WARNING,
            file="a.py",
            start_line=10,
            evidence=["e1", "e2"],
            suggested_fix="fix it",
        )
        score = _evidence_quality(f)
        assert score >= 0.9


# =========================================================================
# PRContext helpers
# =========================================================================


class TestPRContext:
    def test_business_logic_files(self):
        ctx = PRContext(
            diff_spec="x",
            files=[
                ChangedFile(path="app/service.py", category=FileCategory.BUSINESS_LOGIC),
                ChangedFile(path="tests/test.py", category=FileCategory.TEST),
                ChangedFile(path="config/app.yml", category=FileCategory.CONFIG),
            ],
        )
        assert len(ctx.business_logic_files()) == 1
        assert len(ctx.test_files()) == 1
        assert len(ctx.config_files()) == 1

    def test_security_sensitive_files_matches_path_patterns(self):
        """Security scoping is category-agnostic — matches auth/crypto/session
        paths even when diff_parser classified them as INFRA or SCHEMA."""
        ctx = PRContext(
            diff_spec="x",
            files=[
                # Category-agnostic matches — pattern-matched regardless of classification
                ChangedFile(path="src/auth/middleware.py", category=FileCategory.INFRA),
                ChangedFile(path="lib/crypto/rsa.py", category=FileCategory.BUSINESS_LOGIC),
                ChangedFile(path="app/session_store.py", category=FileCategory.BUSINESS_LOGIC),
                ChangedFile(path="migrations/0042_add_permissions_table.sql", category=FileCategory.SCHEMA),
                ChangedFile(path="src/oauth2/callback.py", category=FileCategory.BUSINESS_LOGIC),
                ChangedFile(path="src/auth_service/token_refresh.py", category=FileCategory.BUSINESS_LOGIC),
                ChangedFile(path="config/secrets.yaml", category=FileCategory.CONFIG),
                # Non-matches — should NOT be included
                ChangedFile(path="src/payment/service.py", category=FileCategory.BUSINESS_LOGIC),
                ChangedFile(path="tests/test_auth.py", category=FileCategory.TEST),  # test file matches on 'auth'
                ChangedFile(path="README.md", category=FileCategory.INFRA),
            ],
        )
        sensitive = ctx.security_sensitive_files()
        paths = {f.path for f in sensitive}
        assert "src/auth/middleware.py" in paths
        assert "lib/crypto/rsa.py" in paths
        assert "app/session_store.py" in paths
        assert "migrations/0042_add_permissions_table.sql" in paths
        assert "src/oauth2/callback.py" in paths
        assert "src/auth_service/token_refresh.py" in paths
        assert "config/secrets.yaml" in paths
        # Matched on 'auth' in the filename — acceptable false positive
        # (security scoping is intentionally broad to avoid false negatives)
        assert "tests/test_auth.py" in paths
        # Non-matches
        assert "src/payment/service.py" not in paths
        assert "README.md" not in paths

    def test_security_sensitive_files_empty(self):
        """Returns empty list when no path matches."""
        ctx = PRContext(
            diff_spec="x",
            files=[
                ChangedFile(path="src/payment/service.py", category=FileCategory.BUSINESS_LOGIC),
                ChangedFile(path="README.md", category=FileCategory.INFRA),
            ],
        )
        assert ctx.security_sensitive_files() == []

    def test_finding_score(self):
        f = ReviewFinding(title="x", category=FindingCategory.CORRECTNESS, severity=Severity.CRITICAL, confidence=0.9)
        assert f.score() == 0.9  # 1.0 * 0.9

    def test_risk_profile_max(self):
        profile = RiskProfile(
            correctness=RiskLevel.LOW,
            concurrency=RiskLevel.HIGH,
            security=RiskLevel.MEDIUM,
        )
        assert profile.max_risk() == RiskLevel.HIGH


# =========================================================================
# API endpoint test
# =========================================================================


# =========================================================================
# Query classifier — diff_spec extraction
# =========================================================================


# =========================================================================
# Multi-agent review delegation
# =========================================================================


# TestMultiAgentDelegation and TestFormatReviewResult removed —
# multi-agent delegation and format_review_result moved to Brain orchestrator.
