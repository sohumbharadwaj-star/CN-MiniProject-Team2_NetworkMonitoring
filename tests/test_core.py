"""Meaningful checks that do not require Linux network namespaces or OS-Ken."""
import json
import ast
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark import report as comparison_report, trial_row
from common import CounterRate, append_event, atomic_json, config, decode_telemetry, read_json
from policy import AgentHealth, LinkAlert, Polling
from results import summarize
from server import Receiver


@contextmanager
def test_directory():
    base = Path(__file__).resolve().parents[1] / ".test-tmp"
    base.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as name:
        if not Path(name).resolve().is_relative_to(base.resolve()):
            raise RuntimeError("Test temporary directory escaped its workspace")
        yield name


def report(sequence=1, run="test-run"):
    return b"T" + json.dumps({
        "version": 1, "type": "telemetry", "host_id": "h1", "run_id": run,
        "sequence": sequence, "sent_at": 100.0,
        "data_packets_sent": 0, "data_bytes_sent": 0,
        "telemetry_reports_sent": max(0, sequence - 1),
    }).encode()


class CoreTests(unittest.TestCase):
    def test_comparison_keeps_missed_detection_and_measures_from_phase_start(self):
        with test_directory() as temporary:
            folder = Path(temporary)
            run = folder / "test-run"
            run.mkdir()
            cfg = config()
            atomic_json(run / "configuration.json", cfg)
            timeline = [{"name": phase["name"], "start_mono": i * 10, "end_mono": (i + 1) * 10}
                        for i, phase in enumerate(cfg["phases"])]
            atomic_json(run / "phase_times.json", timeline)
            append_event(run / "polling.jsonl", {"event": "REQUEST", "mono": 2})
            append_event(run / "polling.jsonl", {"event": "REQUEST", "mono": 10})
            # A high alert in the wrong phase must not count; a real outage event does.
            append_event(run / "alerts.jsonl", {"event": "HIGH_UTILIZATION", "mono": 9.9})
            append_event(run / "alerts.jsonl", {"event": "AGENT_STALE", "mono": 34, "host": "h2"})
            summary = summarize(run, 0, "fixed", "h2")
            self.assertIsNone(summary["delays"]["high_alert_seconds"])
            self.assertEqual(summary["delays"]["agent_stale_seconds"], 4)
            self.assertFalse(summary["all_checks_passed"])
            self.assertEqual(summary["phases"]["NORMAL"]["requests_per_second"], 0.1)
            comparison_report(folder, [trial_row(1, "fixed", folder, run, summary)])
            self.assertIn("Workload start to high alert (s) | — (0/1)",
                          (folder / "comparison.md").read_text(encoding="utf-8"))
            self.assertIn("test-run", (folder / "trials.csv").read_text(encoding="utf-8"))

    def test_host_launcher_avoids_second_detachment(self):
        source = (Path(__file__).resolve().parents[1] / "run_demo.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "popen" and isinstance(node.func.value, ast.Name)
                 and node.func.value.id == "host"]
        self.assertEqual(len(calls), 1)
        mncmd = next((item.value for item in calls[0].keywords if item.arg == "mncmd"), None)
        self.assertIsInstance(mncmd, ast.List)
        self.assertEqual([ast.literal_eval(item) for item in mncmd.elts[:2]], ["mnexec", "-a"])

    def test_counter_rate_and_reset(self):
        counter = CounterRate()
        self.assertIsNone(counter.sample(1000, 1.0))
        self.assertEqual(counter.sample(1001000, 3.0), 4_000_000)
        self.assertEqual(counter.sample(1001000, 4.0), 0)
        self.assertIsNone(counter.sample(100, 0.5))
        self.assertEqual(counter.sample(200, 1.5), 800)
        self.assertIsNone(counter.sample(300, 1.5))

    def test_alert_requires_fresh_persistent_evidence_and_recovers(self):
        alert = LinkAlert(70, 60, 2, 4)
        for t, utilization in [(0, 80), (1, None), (2, 80), (7, 80), (7, 80), (8, 82)]:
            self.assertIsNone(alert.update(t, utilization))
        self.assertEqual(alert.update(9, 85), "HIGH_UTILIZATION")
        self.assertTrue(alert.active)
        for t, utilization in [(10, 65), (11, 50), (12, 60), (13, 55), (14, 54)]:
            self.assertIsNone(alert.update(t, utilization))
        self.assertEqual(alert.update(15, 55), "RECOVERED")
        self.assertFalse(alert.active)

    def test_polling_responds_to_measurements_and_agent_loss(self):
        adaptive = Polling("adaptive", config())
        fixed = Polling("fixed", config())
        trace = [(0, 12, False), (1, 67, False), (2, 18, False), (5, 18, False),
                 (6, 18, False), (7, 18, True), (12, 18, True)]
        for at, util, missing in trace:
            self.assertIsNone(fixed.update(at, util, missing))
        self.assertEqual(fixed.interval, 1)
        observed = []
        for at, util, missing in trace:
            adaptive.update(at, util, missing)
            observed.append(adaptive.interval)
        self.assertEqual(observed, [2, .5, .5, .5, 2, .5, .5])

    def test_agent_status_is_per_host_and_events_are_not_repeated(self):
        health = AgentHealth()
        self.assertIsNone(health.update("h2", "WAITING"))
        self.assertIsNone(health.update("h2", "LIVE"))
        self.assertEqual(health.update("h2", "STALE"), "AGENT_STALE")
        self.assertIsNone(health.update("h1", "LIVE"))
        self.assertIsNone(health.update("h2", "STALE"))
        self.assertEqual(health.update("h2", "LIVE"), "AGENT_RECOVERED")

    def test_protocol_rejects_bad_identity_and_invalid_counters(self):
        hosts = {"h1": "10.0.0.1"}
        self.assertEqual(decode_telemetry(report(), "10.0.0.1", hosts)["sequence"], 1)
        for payload, ip in [(b"T[]", "10.0.0.1"), (b"bad", "10.0.0.1"),
                            (report(), "10.0.0.2"), (b"T\xff", "10.0.0.1")]:
            with self.assertRaises((ValueError, UnicodeError)):
                decode_telemetry(payload, ip, hosts)
        value = json.loads(report()[1:])
        value["data_bytes_sent"] = True
        with self.assertRaises(ValueError):
            decode_telemetry(b"T" + json.dumps(value).encode(), "10.0.0.1", hosts)

    def test_receiver_handles_duplicates_restart_late_run_and_bad_input(self):
        with test_directory() as temporary:
            receiver = Receiver({"h1": "10.0.0.1"}, Path(temporary))
            source = ("10.0.0.1", 54321)
            receiver.accept(report(2), source)
            receiver.accept(report(1), source)
            self.assertEqual(receiver.latest["h1"]["sequence"], 2)
            receiver.accept(report(1, "new-run"), source)
            receiver.accept(report(3), source)
            self.assertEqual(receiver.latest["h1"]["run_id"], "new-run")
            receiver.accept(b"invalid", source)
            receiver.accept(report(2, "new-run"), source)
            receiver.accept(b"D" + bytes(1199), source)
            receiver.publish()
            snapshot = read_json(Path(temporary) / "telemetry.json")
            self.assertEqual(snapshot["invalid_messages"], 1)
            self.assertEqual(snapshot["hosts"]["h1"]["sequence"], 2)
            self.assertEqual(snapshot["data_received"]["10.0.0.1"]["bytes"], 1200)

    def test_actual_udp_delivery_to_receiver_and_snapshot(self):
        with test_directory() as temporary:
            receiver = Receiver({"h1": "127.0.0.1"}, Path(temporary))
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
                server.bind(("127.0.0.1", 0))
                server.settimeout(2)
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                    client.sendto(report(), server.getsockname())
                    data, source = server.recvfrom(4096)
                    receiver.accept(data, source)
                    client.sendto(b"D" + bytes(1199), server.getsockname())
                    data, source = server.recvfrom(4096)
                    receiver.accept(data, source)
            receiver.publish()
            snapshot = read_json(Path(temporary) / "telemetry.json")
            self.assertEqual(snapshot["hosts"]["h1"]["sequence"], 1)
            self.assertEqual(snapshot["data_received"]["127.0.0.1"]["packets"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
