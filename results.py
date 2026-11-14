"""Summarize measured events and requests, using the VM's shared monotonic clock."""
import json
from common import atomic_json, read_json


def events(directory, name):
    path = directory / (name + ".jsonl")
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def summarize(directory, connectivity, mode, target):
    timeline = read_json(directory / "phase_times.json", [])
    alerts = events(directory, "alerts")
    polling = events(directory, "polling")
    measurements = events(directory, "measurements")
    samples = events(directory, "port_stats")
    telemetry = events(directory, "telemetry")
    cfg = read_json(directory / "configuration.json")
    by_name = {phase["name"]: phase for phase in timeline}

    def during(records, phase, field="mono"):
        return [row for row in records if phase["start_mono"] <= row[field] < phase["end_mono"]]

    def delay(event, phase_name, host=None):
        phase = by_name[phase_name]
        matches = [row for row in during(alerts, phase)
                   if row["event"] == event and (host is None or row.get("host") == host)]
        return None if not matches else round(matches[0]["mono"] - phase["start_mono"], 3)

    phases = {}
    for name, phase in by_name.items():
        elapsed = phase["end_mono"] - phase["start_mono"]
        messages = during(polling, phase)
        requests = sum(row["event"] == "REQUEST" for row in messages)
        replies = sum(row["event"] == "REPLY_COMPLETE" for row in messages)
        ports = during(samples, phase, "at")
        percentages = [row["percent"] for row in ports if row["percent"] is not None]
        mean_percent = None
        if len(ports) >= 2 and all(row["bps"] is not None for row in ports[1:]):
            first, last = ports[0], ports[-1]
            seconds = last["counter_age"] - first["counter_age"]
            if seconds > 0:
                mean_percent = round(100 * 8 * (last["bytes"] - first["bytes"]) /
                                     (seconds * cfg["link_capacity_mbps"] * 1e6), 2)
        phases[name] = {
            "seconds": round(elapsed, 3), "requests": requests, "completed_replies": replies,
            "requests_per_second": round(requests / elapsed, 3),
            "utilization_samples": len(percentages),
            "mean_utilization_percent": mean_percent,
            "peak_utilization_percent": round(max(percentages), 2) if percentages else None,
        }
    delays = {
        "high_alert_seconds": delay("HIGH_UTILIZATION", "HIGH"),
        "load_recovery_seconds": delay("RECOVERED", "RECOVERY"),
        "agent_stale_seconds": delay("AGENT_STALE", "AGENT_STOPPED", target),
        "agent_recovery_seconds": delay("AGENT_RECOVERED", "AGENT_RESTARTED", target),
    }
    outage = during(telemetry, by_name["AGENT_STOPPED"], "received_mono")
    changes = [row for row in polling if row["event"] == "INTERVAL_CHANGED"]
    normal = during(measurements, by_name["NORMAL"], "updated_mono")
    checks = {
        "host_connectivity": connectivity == 0,
        "all_agents_observed_with_sdn_stats": any(row["ready"] and row["state"] == "NORMAL" for row in normal),
        "malformed_message_rejected": any(row["invalid_messages"] > 0 for row in measurements),
        "high_alert_observed": delays["high_alert_seconds"] is not None,
        "load_recovery_observed": delays["load_recovery_seconds"] is not None,
        "interrupted_agent_detected": delays["agent_stale_seconds"] is not None,
        "other_agents_kept_reporting": all(
            sum(row["host_id"] == host for row in outage) >= 2 for host in cfg["hosts"] if host != target),
        "restarted_agent_detected": delays["agent_recovery_seconds"] is not None,
    }
    if mode == "adaptive":
        checks["polling_sped_up_under_load"] = any(
            row["to_seconds"] == cfg["fast_interval"] for row in during(changes, by_name["HIGH"]))
        checks["polling_slowed_after_load"] = any(
            row["to_seconds"] == cfg["quiet_interval"] for row in during(changes, by_name["RECOVERY"]))
        checks["polling_sped_up_for_stale_agent"] = any(
            row["reason"] == "stale telemetry" for row in during(changes, by_name["AGENT_STOPPED"]))
    else:
        checks["polling_stayed_fixed"] = not changes and bool(polling)
    result = {
        "mode": mode, "checks": checks, "all_checks_passed": all(checks.values()),
        "phases": phases, "delays": delays, "alert_events": alerts, "polling_changes": changes,
        "note": "Delays start at the scripted stimulus, not at the first threshold crossing. Null means not observed."
    }
    atomic_json(directory / "summary.json", result)
    return result
