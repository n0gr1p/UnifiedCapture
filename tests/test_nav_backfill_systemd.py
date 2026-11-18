"""Verify the NAV-10 timer remains explicit opt-in and keeps finite work budgets."""
import configparser
import pathlib
import shlex
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
UNITS = ROOT / "deploy" / "systemd" / "user"
SERVICE = "unifiedcapture-nav-backfill.service"
TIMER = "unifiedcapture-nav-backfill.timer"


def read_unit(name):
    cfg = configparser.ConfigParser(interpolation=None)
    loaded = cfg.read(UNITS / name, encoding="utf-8")
    if not loaded:
        raise AssertionError(f"Missing systemd unit: {name}")
    return cfg


class NavBackfillSystemdTests(unittest.TestCase):
    def test_finite_unprivileged_candidate_import_only(self):
        cfg = read_unit(SERVICE)
        service = cfg["Service"]
        command = shlex.split(service["ExecStart"])
        self.assertEqual(cfg["Service"]["Type"], "oneshot")
        self.assertEqual(service["WorkingDirectory"], "/home/peter/UnifiedCapture")
        self.assertEqual(command[0], "/usr/bin/python3")
        self.assertEqual(command[1],
                         "/home/peter/UnifiedCapture-NavDev/scripts/nav_backfill_bounded.py")
        self.assertEqual(command[2:], ["--apply", "--max-events", "2000",
                                       "--max-batches", "5", "--pause-seconds", "5"])
        self.assertEqual(service["Nice"], "10")
        self.assertEqual(service["IOSchedulingClass"], "idle")
        self.assertEqual(service["TimeoutStartSec"], "180s")
        self.assertFalse(cfg.has_option("Service", "User"))
        self.assertFalse(cfg.has_option("Service", "ExecStartPre"))
        self.assertFalse(cfg.has_section("Install"),
                         "Service should not be directly enabled at boot")
        self.assertTrue(cfg.has_option("Unit", "ConditionPathExists"))

    def test_timer_is_opt_in_bounded_and_nonpersistent(self):
        cfg = read_unit(TIMER)
        self.assertEqual(cfg["Timer"]["OnActiveSec"], "5min")
        self.assertEqual(cfg["Timer"]["OnUnitInactiveSec"], "15min")
        self.assertEqual(cfg["Timer"]["Unit"], SERVICE)
        self.assertEqual(cfg["Timer"]["Persistent"], "false")
        self.assertEqual(cfg["Install"]["WantedBy"], "timers.target")
        self.assertNotIn("OnCalendar", cfg["Timer"])

if __name__ == "__main__":
    unittest.main()
