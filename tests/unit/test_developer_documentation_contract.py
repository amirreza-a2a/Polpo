# ============================================================
#  tests/unit/test_developer_documentation_contract.py
#  Rigorous contract tests for Polpo developer documentation (DOC-02)
# ============================================================

from pathlib import Path
import re
import unittest


class TestDeveloperDocumentationContract(unittest.TestCase):
    """Asserts architectural invariants, glossary accuracy, and operational integrity of developer docs."""

    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parents[2]
        cls.agents_path = cls.repo_root / "AGENTS.md"
        cls.context_path = cls.repo_root / "CONTEXT.md"
        cls.skills_path = cls.repo_root / "docs" / "agents" / "skills.md"
        cls.ci_rules_path = cls.repo_root / "docs" / "ci-repository-rules.md"
        cls.ci_workflow_path = cls.repo_root / ".github" / "workflows" / "ci.yml"
        cls.adr_004_path = cls.repo_root / "docs" / "adr" / "2026-10-22-adr-004-pipeline2-artifact-lineage-and-canonical-publication-contract.md"

        cls.agents_text = cls.agents_path.read_text(encoding="utf-8")
        cls.context_text = cls.context_path.read_text(encoding="utf-8")
        cls.skills_text = cls.skills_path.read_text(encoding="utf-8")
        cls.ci_rules_text = cls.ci_rules_path.read_text(encoding="utf-8")
        cls.ci_workflow_text = cls.ci_workflow_path.read_text(encoding="utf-8")
        cls.adr_004_text = cls.adr_004_path.read_text(encoding="utf-8")

    def test_product_branding_consistency(self):
        """Current-facing documentation titles and system overviews must refer to Polpo rather than legacy PolpoT."""
        self.assertTrue(
            self.agents_text.startswith("# AGENTS.md — Standing Rules for Polpo"),
            "AGENTS.md header must cite Polpo",
        )
        self.assertTrue(
            self.context_text.startswith("# CONTEXT.md — Polpo Canonical Domain Glossary & Architectural Seams"),
            "CONTEXT.md header must cite Polpo",
        )
        self.assertIn("Polpo is a **desktop-first, local-first, embedded application**", self.agents_text)
        self.assertIn("**Polpo** is a desktop-first, local-first, serverless Document Intelligence", self.context_text)

    def test_agents_standing_rules_core_invariants(self):
        """AGENTS.md must define clean architecture, local-first persistence, credential security, and frozen legacy boundaries."""
        # Rule 2 & 3: Serverless / desktop-first
        self.assertIn("Desktop-First, Local-First, Serverless Architecture", self.agents_text)
        self.assertIn("No Server Dependency", self.agents_text)
        self.assertIn("localhost:8000", self.agents_text)

        # Rule 4: Frozen legacy transport
        self.assertIn("Telegram Is a Frozen Legacy Transport", self.agents_text)
        self.assertIn("interfaces/telegram/", self.agents_text)
        self.assertIn("handlers/", self.agents_text)
        self.assertIn("main.py", self.agents_text)

        # Rule 6: Clean Architecture dependency direction
        self.assertIn("Clean Architecture and Dependency Direction", self.agents_text)

        # Rule 8 & 10: SQLite WAL and Credential security
        self.assertIn("SQLite is the canonical local application database", self.agents_text)
        self.assertIn("CredentialRef", self.agents_text)
        self.assertIn("OS Keyring", self.agents_text)

        # Rule 13, 14, 15: Concurrency, cooperative cancellation, and startup recovery locking
        self.assertIn("Atomic Job Ownership", self.agents_text)
        self.assertIn("Cancellation Must Be Cooperative", self.agents_text)
        self.assertIn("JobRecoveryService", self.agents_text)
        self.assertIn("BEGIN IMMEDIATE", self.agents_text)

        # Rule 33: Canonical publication & OCC
        self.assertIn("Canonical Document Publication and Optimistic Concurrency Control (OCC)", self.agents_text)
        self.assertIn("DocumentPublicationService", self.agents_text)
        self.assertIn("StaleDocumentVersionError", self.agents_text)
        self.assertIn("PIPELINE2_MARKDOWN", self.agents_text)

        # Rule 34: Branch protection & release freeze
        self.assertIn("Strict Branch Protection Merge Gates and Release Freeze", self.agents_text)
        self.assertIn("quality-gates", self.agents_text)
        self.assertIn("secret-scan", self.agents_text)
        self.assertIn("test-linux", self.agents_text)
        self.assertIn("test-windows", self.agents_text)

        # Rule 35: Agent skills no-vendoring
        self.assertIn("Agent Skills Routing and Prohibition of Vendoring", self.agents_text)
        self.assertIn("Zero Skill Vendoring", self.agents_text)

    def test_context_md_pipeline2_semantics_agree_with_adr004(self):
        """CONTEXT.md must describe Pipeline 2 as typography refinement with derivative artifacts and OCC adoption per ADR-004."""
        # Must not describe Pipeline 2 as unified single-pass layout OCR
        self.assertNotIn("Single-pass layout decomposition combining visual entity localization", self.context_text)

        # Must describe Pipeline 2 typography refinement and derivative artifact semantics
        self.assertIn("typography refinement", self.context_text)
        self.assertIn("ArtifactType.PIPELINE2_MARKDOWN", self.context_text)
        self.assertIn("source_version", self.context_text)
        self.assertIn("DocumentPublicationService", self.context_text)
        self.assertIn("PIPELINE_2_ADOPTION", self.context_text)
        self.assertIn("StaleDocumentVersionError", self.context_text)
        self.assertIn("Pipeline2CompletedEvent", self.context_text)

        # Must define Pipeline2Job entity
        self.assertIn("### 2.2 `Pipeline2Job`", self.context_text)
        self.assertIn("core/entities/job.py", self.context_text)

    def test_context_md_canonical_immutability_and_two_phase_publication(self):
        """CONTEXT.md must document that canonical version files are immutable and use atomic two-phase publication."""
        self.assertIn("output_{job_id}_v{version}.md", self.context_text)
        self.assertIn("Existing versioned markdown files are never overwritten in-place", self.context_text)
        self.assertIn("BEGIN IMMEDIATE", self.context_text)
        self.assertIn("DocumentVersionRecord", self.context_text)

    def test_context_md_legacy_surfaces_isolated(self):
        """CONTEXT.md must catalog all legacy surfaces as frozen compatibility code."""
        for surface in [
            "interfaces/telegram/",
            "handlers/",
            "main.py",
            "services/worker.py",
            "infrastructure/composition.py",
        ]:
            with self.subTest(surface=surface):
                self.assertIn(surface, self.context_text, f"CONTEXT.md must list legacy surface {surface}")

    def test_skills_routing_guide_covers_16_skills_and_prohibits_vendoring(self):
        """docs/agents/skills.md must catalog exactly 16 skills, verified against environment, and forbid vendoring."""
        expected_skills = [
            "grilling",
            "grill-with-docs",
            "to-tickets",
            "wayfinder",
            "implement",
            "tdd",
            "code-review",
            "diagnosing-bugs",
            "triage",
            "retro",
            "domain-modeling",
            "improve-codebase-architecture",
            "codebase-design",
            "research",
            "resolving-merge-conflicts",
            "handoff",
        ]
        for skill in expected_skills:
            with self.subTest(skill=skill):
                self.assertIn(f"`{skill}`", self.skills_text, f"skills.md must catalog skill `{skill}`")

        # Prohibition of vendoring
        self.assertIn("Zero skill vendoring", self.skills_text)
        cleaned_text = re.sub(r"[*_`]", "", self.skills_text)
        self.assertTrue(
            bool(
                re.search(r"must\s+(?:not|never)\s+be\s+copied,\s*vendored", cleaned_text, re.IGNORECASE)
            ),
            "skills.md must prohibit copying/vendoring skills",
        )

    def test_no_skills_vendored_in_repository(self):
        """Ensures that no agent skill implementations are vendored into the repository tree."""
        vendored_agents_skills = self.repo_root / ".agents" / "skills"
        vendored_gemini_skills = self.repo_root / ".gemini" / "skills"

        self.assertFalse(
            vendored_agents_skills.exists(),
            "Skill implementations must not be vendored under .agents/skills/ in repository",
        )
        self.assertFalse(
            vendored_gemini_skills.exists(),
            "Skill implementations must not be vendored under .gemini/skills/ in repository",
        )

    def test_branch_protection_documented_checks_match_ci_workflow(self):
        """Documented branch protection checks in ci-repository-rules.md and CONTEXT.md must match ci.yml jobs."""
        # Find all 2-space indented job keys under 'jobs:' in .github/workflows/ci.yml
        jobs_section = self.ci_workflow_text.split("jobs:\n", 1)[1]
        workflow_jobs = set(re.findall(r"^  ([a-zA-Z0-9_-]+):\s*$", jobs_section, re.MULTILINE))

        required_contexts = {"quality-gates", "test-linux", "test-windows", "secret-scan"}

        # All required contexts must be top-level jobs in .github/workflows/ci.yml
        self.assertTrue(
            required_contexts.issubset(workflow_jobs),
            f"CI workflow jobs {workflow_jobs} must contain all required contexts {required_contexts}",
        )

        # ci-repository-rules.md must list all 4 required status checks
        for context in required_contexts:
            with self.subTest(context=context):
                self.assertIn(f"`{context}`", self.ci_rules_text)
                self.assertIn(f"`{context}`", self.context_text)

        # Server-side protection invariants
        self.assertIn("strict: true", self.ci_rules_text)
        self.assertIn("enforce_admins: true", self.ci_rules_text)
        self.assertIn("required_linear_history: true", self.ci_rules_text)
        self.assertIn("allow_force_pushes: false", self.ci_rules_text)
        self.assertIn("allow_deletions: false", self.ci_rules_text)
        self.assertIn("required_pull_request_reviews: null", self.ci_rules_text)

    def test_developer_docs_relative_links_resolve(self):
        """All Markdown relative links in audited developer documents must resolve to existing targets."""
        link_pattern = re.compile(r"(?<!\!)\[([^\]]+)\]\(([^)]+)\)")
        audited_docs = [
            self.agents_path,
            self.context_path,
            self.skills_path,
            self.ci_rules_path,
        ]

        for doc_path in audited_docs:
            raw_text = doc_path.read_text(encoding="utf-8")
            # Strip fenced code blocks and inline code spans to avoid testing literal syntax examples
            text = re.sub(r"```[\s\S]*?```", "", raw_text)
            text = re.sub(r"`[^`\n]+`", "", text)

            doc_dir = doc_path.parent
            for match in link_pattern.finditer(text):
                link_target = match.group(2).strip()
                # Skip external links, mailto, and anchor-only links
                if link_target.startswith(("http://", "https://", "mailto:", "#")):
                    continue

                # Strip anchor fragment
                file_target = link_target.split("#")[0]
                if not file_target:
                    continue

                # Skip template expressions
                if "{" in file_target:
                    continue

                resolved = (doc_dir / file_target).resolve()
                self.assertTrue(
                    resolved.exists(),
                    f"Broken link in {doc_path.name}: '{link_target}' (resolved to {resolved})",
                )


if __name__ == "__main__":
    unittest.main()
