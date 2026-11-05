"""Safety regression tests for battery-change radio-ID reassignment."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pylacrosse_ng.discovery import (
    BatteryReassignmentCandidate,
    SensorRegistry,
)
from pylacrosse_ng.parser import parse_telegram


NOW = datetime(2026, 11, 5, 16, 0, tzinfo=timezone.utc)
SINCE = NOW - timedelta(minutes=5)


def reading(radio_id: int, *, battery: bool = True, external: bool = False):
    flags = 130 if external else 129 if battery else 1
    decoded = parse_telegram(f"OK 9 {radio_id} {flags} 4 150 45")
    assert decoded is not None
    return decoded


class TestBatteryReassignment(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = SensorRegistry(configured_ids={5, 10}, ignored_ids={8})

    def observe(self, radio_id: int, *, at=NOW, battery=True, external=False):
        return self.registry.observe(
            reading(radio_id, battery=battery, external=external),
            received_at=at,
        )

    def candidates(self, **options):
        return self.registry.battery_reassignment_candidates(
            5, since=SINCE, now=NOW, **options
        )

    def confirm(self, new_radio_id: int, **options):
        return self.registry.confirm_battery_reassignment(
            5, new_radio_id, since=SINCE, now=NOW, **options
        )

    def test_only_fresh_unconfigured_unignored_battery_events_are_suggested(self):
        self.observe(7)
        self.observe(8)
        self.observe(10)
        self.observe(11, battery=False)
        self.observe(12, at=SINCE - timedelta(seconds=1))
        self.observe(13, at=NOW - timedelta(minutes=11))
        self.observe(14, at=NOW + timedelta(seconds=1))
        self.observe(15)
        result = self.candidates()
        self.assertEqual([candidate.radio_id for candidate in result], [7, 15])
        self.assertTrue(all(isinstance(c, BatteryReassignmentCandidate) for c in result))
        self.assertEqual(self.registry.configured_ids, frozenset({5, 10}))
        self.assertEqual(self.registry.ignored_ids, frozenset({8}))

    def test_external_probe_does_not_produce_battery_candidate(self):
        self.observe(25, external=True)
        self.assertEqual(self.candidates(), ())
        self.observe(25, battery=True)
        self.assertEqual([c.radio_id for c in self.candidates()], [25])

    def test_confirmation_changes_membership_only_after_explicit_call(self):
        self.observe(7)
        self.assertEqual([c.radio_id for c in self.candidates()], [7])
        self.assertEqual(self.registry.configured_ids, frozenset({5, 10}))
        updated = self.confirm(7)
        self.assertEqual(updated, frozenset({7, 10}))
        self.assertEqual(self.registry.configured_ids, frozenset({7, 10}))
        self.assertEqual(self.registry.classification(7), "configured")
        self.assertEqual(self.registry.classification(5), "unknown")

    def test_ignored_id_is_rechecked_at_confirmation(self):
        self.observe(7)
        self.assertEqual([c.radio_id for c in self.candidates()], [7])
        self.registry.ignore(7)
        with self.assertRaisesRegex(ValueError, "ignored"):
            self.confirm(7)
        self.assertEqual(self.registry.configured_ids, frozenset({5, 10}))
        self.assertEqual(self.registry.ignored_ids, frozenset({7, 8}))

    def test_newly_configured_id_is_rejected_at_confirmation(self):
        self.observe(7)
        self.registry.set_memberships(
            configured_ids={5, 7, 10}, ignored_ids={8}
        )
        with self.assertRaisesRegex(ValueError, "already configured"):
            self.confirm(7)
        self.assertEqual(self.registry.configured_ids, frozenset({5, 7, 10}))

    def test_expired_or_missing_battery_event_is_rejected(self):
        self.observe(7)
        with self.assertRaisesRegex(ValueError, "no fresh battery event"):
            self.registry.confirm_battery_reassignment(
                5, 7, since=SINCE,
                now=NOW + timedelta(minutes=11),
            )
        self.observe(9, battery=False)
        with self.assertRaisesRegex(ValueError, "no fresh battery event"):
            self.confirm(9)
        self.assertEqual(self.registry.configured_ids, frozenset({5, 10}))

    def test_invalid_old_new_ids_and_windows_are_rejected(self):
        self.observe(7)
        with self.assertRaisesRegex(ValueError, "must differ"):
            self.confirm(5)
        with self.assertRaisesRegex(ValueError, "already configured"):
            self.confirm(10)
        with self.assertRaisesRegex(ValueError, "must be configured"):
            self.registry.battery_reassignment_candidates(
                99, since=SINCE, now=NOW
            )
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self.registry.battery_reassignment_candidates(
                5, since=SINCE.replace(tzinfo=None), now=NOW
            )
        with self.assertRaisesRegex(ValueError, "positive timedelta"):
            self.candidates(max_age=timedelta(0))
        with self.assertRaisesRegex(ValueError, "later than now"):
            self.registry.battery_reassignment_candidates(
                5, since=NOW + timedelta(seconds=1), now=NOW
            )

    def test_confirmation_racing_with_ignore_is_atomic(self):
        # Each operation takes the registry lock. Regardless of ordering,
        # an ignored radio ID must never become configured.
        for _ in range(20):
            registry = SensorRegistry(configured_ids={5}, ignored_ids=set())
            registry.observe(reading(7), received_at=NOW)
            barrier = threading.Barrier(2)
            outcomes = []

            def ignore():
                barrier.wait(timeout=2)
                try:
                    registry.ignore(7)
                    outcomes.append("ignored")
                except ValueError:
                    outcomes.append("ignore_rejected")

            def confirm():
                barrier.wait(timeout=2)
                try:
                    registry.confirm_battery_reassignment(
                        5, 7, since=SINCE, now=NOW
                    )
                    outcomes.append("assigned")
                except ValueError:
                    outcomes.append("assignment_rejected")

            threads = [threading.Thread(target=ignore), threading.Thread(target=confirm)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertFalse(registry.configured_ids & registry.ignored_ids)
            self.assertEqual(len(outcomes), 2)
            self.assertTrue(
                ("assigned" in outcomes and "ignore_rejected" in outcomes)
                or ("ignored" in outcomes and "assignment_rejected" in outcomes)
            )


if __name__ == "__main__":
    unittest.main()
