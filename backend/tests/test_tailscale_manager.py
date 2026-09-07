import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tailscale_manager import enable_funnel_once, keep_funnel_active, start_funnel_manager


class TailscaleManagerTests(unittest.TestCase):
    def test_enable_funnel_uses_public_background_command(self):
        calls = []

        def runner(executable, arguments):
            calls.append((executable, tuple(arguments)))
            return 0, "ok", ""

        success, _ = enable_funnel_once(
            8010,
            finder=lambda: r"C:\Program Files\Tailscale\tailscale.exe",
            runner=runner,
        )

        self.assertTrue(success)
        self.assertEqual(calls[0][1], ("status",))
        self.assertEqual(calls[1][1], ("funnel", "--bg", "--yes", "8010"))
        self.assertNotIn("reset", calls[1][1])

    def test_missing_tailscale_is_a_retryable_failure(self):
        success, message = enable_funnel_once(8010, finder=lambda: None)
        self.assertFalse(success)
        self.assertIn("belum ditemukan", message)

    def test_retry_continues_after_initial_failure(self):
        attempts = []
        sleeps = []

        def enable_once(port):
            attempts.append(port)
            if len(attempts) < 3:
                return False, "service belum siap"
            return True, "aktif"

        success = keep_funnel_active(
            8010,
            retry_seconds=(2, 5, 10),
            sleep=sleeps.append,
            enable_once=enable_once,
            max_attempts=5,
        )

        self.assertTrue(success)
        self.assertEqual(attempts, [8010, 8010, 8010])
        self.assertEqual(sleeps, [2, 5])

    def test_skip_flag_avoids_starting_thread(self):
        with patch.dict(os.environ, {"FPOS_SKIP_TAILSCALE": "1"}):
            self.assertIsNone(start_funnel_manager(8010))


if __name__ == "__main__":
    unittest.main()
