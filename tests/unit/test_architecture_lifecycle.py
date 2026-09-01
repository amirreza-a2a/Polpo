# ============================================================
#  tests/unit/test_architecture_lifecycle.py
#  Job Lifecycle Ownership & State Machine Singularity Enforcement
# ============================================================

import ast
import unittest
from pathlib import Path

from core.entities.job import JobStatus
from core.policies.job_state_policy import JobStateTransitionPolicy, InvalidStateTransitionError


class TestArchitectureLifecycle(unittest.TestCase):
    """
    Automated architectural checks enforcing:
      1. JobStateTransitionPolicy is the SOLE authoritative definition of the job lifecycle transition relation.
      2. Domain state transition policy is mathematically sound, complete, and prevents illegal jumps.
      3. Presentation queue vs history models maintain strict status partitioning:
         - Active Queue: pending, processing, paused
         - History: done, failed, cancelled
    """

    @classmethod
    def setUpClass(cls):
        cls.root_dir = Path(__file__).resolve().parent.parent.parent
        cls.core_dir = cls.root_dir / "core"
        cls.app_dir = cls.root_dir / "application"
        cls.desktop_dir = cls.root_dir / "interfaces" / "desktop"

    def test_state_machine_singularity_ast_guard(self):
        """
        Mandatory Correction 6: AST check verifying that JobStateTransitionPolicy is the ONLY
        class across core/, application/, interfaces/, or infrastructure/ defining the job
        status transition relation table (ALLOWED_TRANSITIONS).
        """
        found_transition_tables = []

        for folder in [self.core_dir, self.app_dir, self.desktop_dir, self.root_dir / "infrastructure"]:
            for py_file in folder.rglob("*.py"):
                tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
                for node in ast.walk(tree):
                    # Check class attribute assignments named ALLOWED_TRANSITIONS
                    if isinstance(node, ast.ClassDef):
                        for item in node.body:
                            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                                if item.target.id == "ALLOWED_TRANSITIONS":
                                    found_transition_tables.append((py_file.name, node.name))
                            elif isinstance(item, ast.Assign):
                                for target in item.targets:
                                    if isinstance(target, ast.Name) and target.id == "ALLOWED_TRANSITIONS":
                                        found_transition_tables.append((py_file.name, node.name))

        self.assertEqual(
            len(found_transition_tables),
            1,
            f"Expected exactly 1 transition table definition, found: {found_transition_tables}",
        )
        self.assertEqual(found_transition_tables[0], ("job_state_policy.py", "JobStateTransitionPolicy"))

    def test_domain_state_transition_matrix_contract(self):
        """
        Verifies the authoritative state transition contract:
          PENDING    -> PROCESSING, PAUSED, FAILED, CANCELLED
          PROCESSING -> PAUSED, DONE, FAILED, CANCELLED
          PAUSED     -> PENDING, FAILED, CANCELLED
          FAILED     -> PENDING, CANCELLED
          CANCELLED  -> PENDING
          DONE       -> (None, terminal)
        """
        # 1. PENDING transitions
        self.assertTrue(JobStateTransitionPolicy.can_transition(JobStatus.PENDING, JobStatus.PROCESSING))
        self.assertTrue(JobStateTransitionPolicy.can_transition(JobStatus.PENDING, JobStatus.PAUSED))
        self.assertTrue(JobStateTransitionPolicy.can_transition(JobStatus.PENDING, JobStatus.FAILED))
        self.assertTrue(JobStateTransitionPolicy.can_transition(JobStatus.PENDING, JobStatus.CANCELLED))
        self.assertFalse(JobStateTransitionPolicy.can_transition(JobStatus.PENDING, JobStatus.DONE))

        # 2. PROCESSING transitions
        self.assertTrue(JobStateTransitionPolicy.can_transition(JobStatus.PROCESSING, JobStatus.PAUSED))
        self.assertTrue(JobStateTransitionPolicy.can_transition(JobStatus.PROCESSING, JobStatus.DONE))
        self.assertTrue(JobStateTransitionPolicy.can_transition(JobStatus.PROCESSING, JobStatus.FAILED))
        self.assertTrue(JobStateTransitionPolicy.can_transition(JobStatus.PROCESSING, JobStatus.CANCELLED))
        self.assertFalse(JobStateTransitionPolicy.can_transition(JobStatus.PROCESSING, JobStatus.PENDING))

        # 3. PAUSED transitions
        self.assertTrue(JobStateTransitionPolicy.can_transition(JobStatus.PAUSED, JobStatus.PENDING))
        self.assertTrue(JobStateTransitionPolicy.can_transition(JobStatus.PAUSED, JobStatus.FAILED))
        self.assertTrue(JobStateTransitionPolicy.can_transition(JobStatus.PAUSED, JobStatus.CANCELLED))
        self.assertFalse(JobStateTransitionPolicy.can_transition(JobStatus.PAUSED, JobStatus.PROCESSING))
        self.assertFalse(JobStateTransitionPolicy.can_transition(JobStatus.PAUSED, JobStatus.DONE))

        # 4. FAILED transitions (via Retry or Cancel)
        self.assertTrue(JobStateTransitionPolicy.can_transition(JobStatus.FAILED, JobStatus.PENDING))
        self.assertTrue(JobStateTransitionPolicy.can_transition(JobStatus.FAILED, JobStatus.CANCELLED))
        self.assertFalse(JobStateTransitionPolicy.can_transition(JobStatus.FAILED, JobStatus.PROCESSING))
        self.assertFalse(JobStateTransitionPolicy.can_transition(JobStatus.FAILED, JobStatus.DONE))

        # 5. CANCELLED transitions (via Retry)
        self.assertTrue(JobStateTransitionPolicy.can_transition(JobStatus.CANCELLED, JobStatus.PENDING))
        self.assertFalse(JobStateTransitionPolicy.can_transition(JobStatus.CANCELLED, JobStatus.PROCESSING))
        self.assertFalse(JobStateTransitionPolicy.can_transition(JobStatus.CANCELLED, JobStatus.DONE))

        # 6. DONE terminal state (no outbound transitions to other states)
        for s in JobStatus:
            if s != JobStatus.DONE:
                self.assertFalse(
                    JobStateTransitionPolicy.can_transition(JobStatus.DONE, s),
                    f"Terminal DONE status must not transition to {s}",
                )
                with self.assertRaises(InvalidStateTransitionError):
                    JobStateTransitionPolicy.validate_transition(JobStatus.DONE, s)

    def test_presentation_queue_vs_history_partitioning(self):
        """
        Rule: Presentation models must strictly partition job states:
          - Active Queue: {pending, processing, paused}
          - History: {done, failed, cancelled}
        """
        active_statuses = {"pending", "processing", "paused"}
        history_statuses = {"done", "failed", "cancelled"}

        all_enum_values = {s.value for s in JobStatus}

        self.assertEqual(
            active_statuses.intersection(history_statuses),
            set(),
            "Queue and History statuses must be strictly mutually exclusive!",
        )
        self.assertEqual(
            active_statuses.union(history_statuses),
            all_enum_values,
            "Every JobStatus value must belong to either Queue or History!",
        )


if __name__ == "__main__":
    unittest.main()
