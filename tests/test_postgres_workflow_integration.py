import os
import tempfile
import unittest
from unittest.mock import patch

from agent_runtime.game_workflow import GameWorkflowManager, WorkflowError


@unittest.skipUnless(
    os.getenv("DOCMIND_TEST_POSTGRES_DSN"),
    "set DOCMIND_TEST_POSTGRES_DSN to run real Postgres integration tests",
)
class PostgresWorkflowIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.managers = []
        dsn = os.environ["DOCMIND_TEST_POSTGRES_DSN"]
        self.first = GameWorkflowManager(tempfile.mkdtemp(), checkpoint_dsn=dsn)
        self.second = GameWorkflowManager(tempfile.mkdtemp(), checkpoint_dsn=dsn)
        self.managers.extend((self.first, self.second))
        if (self.first.checkpoint_backend != "langgraph-postgres" or
                self.second.checkpoint_backend != "langgraph-postgres"):
            self.skipTest("Postgres checkpoint backend unavailable")

    def tearDown(self):
        for manager in self.managers:
            manager.close()

    def test_schema_health_and_lease_fencing(self):
        health = self.first.checkpoint_health(probe=True)
        self.assertTrue(health["healthy"])
        self.assertEqual(health["schema_version"], 1)
        token = self.first._acquire_workflow_lease("integration-workflow")
        with self.assertRaises(WorkflowError):
            self.second._acquire_workflow_lease("integration-workflow")
        with self.first._checkpoint_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE docmind_workflow_leases "
                    "SET expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' "
                    "WHERE workflow_id = %s",
                    ("integration-workflow",),
                )
        next_token = self.second._acquire_workflow_lease("integration-workflow")
        self.assertGreater(next_token, token)

        # A separately configured one-slot pool must fail fast when saturated.
        with patch.dict(os.environ, {
            "DOCMIND_CHECKPOINT_POOL_SIZE": "1",
            "DOCMIND_CHECKPOINT_POOL_TIMEOUT_S": "0.2",
        }, clear=False):
            manager = GameWorkflowManager(tempfile.mkdtemp(),
                                          checkpoint_dsn=os.environ["DOCMIND_TEST_POSTGRES_DSN"])
        self.managers.append(manager)
        if manager.checkpoint_backend != "langgraph-postgres":
            self.skipTest("Postgres checkpoint backend unavailable")
        pool = manager._checkpoint_pool
        with pool.connection(timeout=1.0):
            try:
                from psycopg_pool import PoolTimeout
            except ImportError:
                PoolTimeout = Exception
            with self.assertRaises(PoolTimeout):
                with pool.connection(timeout=0.1):
                    pass

        # A broken backend connection must not poison subsequent work.
        pool = self.first._checkpoint_pool
        victim = pool.getconn(timeout=5)
        killer = pool.getconn(timeout=5)
        try:
            with victim.cursor() as cur:
                cur.execute("SELECT pg_backend_pid()")
                victim_pid = cur.fetchone()[0]
            with killer.cursor() as cur:
                cur.execute("SELECT pg_terminate_backend(%s)", (victim_pid,))
                self.assertTrue(cur.fetchone()[0])
        finally:
            pool.putconn(killer)
            try:
                victim.close()
            except Exception:
                pass
            pool.putconn(victim)
        with pool.connection(timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                self.assertEqual(cur.fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
