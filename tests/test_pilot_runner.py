"""NAV-03 pilot script must not stat historical root-only DB as a regular user."""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
import derived_navigation_db as nav


class PilotPreflightTests(unittest.TestCase):
    def test_raw_hdd_path_checked_only_by_elevated_extractor(self):
        # Run the actual Bash script with shimmed host-only utilities: we want
        # to exercise its real preflight against an inaccessible raw path.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            nav_path = tmp / "nvme" / "navigation.sqlite3"
            nav.initialize(nav_path)
            commands = tmp / "bin"
            commands.mkdir()
            (commands / "findmnt").write_text(
                "#!/bin/sh\nprintf '/dev/nvme0n1p2\\n'\n"
            )
            (commands / "sudo").write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' '{\"format\":1,\"algorithm\":\"walk-candidate-v1\","
                "\"zone\":247,\"session\":\"protected_raw_test\","
                "\"positions_scanned\":0,\"source_last_sequence\":null,"
                "\"source_after_sequence\":0,\"rejected\":{},\"edges\":[]}'\n"
            )
            for path in commands.iterdir():
                path.chmod(0o755)

            # This path is deliberately absent/unstatable by the current user.
            raw = tmp / "root_only" / "capture.sqlite3"
            env = os.environ.copy()
            env["PATH"] = str(commands) + os.pathsep + env["PATH"]
            env["UCAP_RAW_DB"] = str(raw)
            env["UCAP_NAV_DB"] = str(nav_path)
            done = subprocess.run(
                ["bash", str(ROOT / "scripts/mine_zone247_pilot.sh")],
                env=env, capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn('"documents": 1', done.stdout)
            self.assertIn('"new_edges": 0', done.stdout)
            self.assertIn('"release_status": "not_built"', done.stdout)
            self.assertFalse(raw.exists())
            self.assertEqual(nav.inspect(nav_path)["release_status"], "not_built")


if __name__ == "__main__":
    unittest.main()
