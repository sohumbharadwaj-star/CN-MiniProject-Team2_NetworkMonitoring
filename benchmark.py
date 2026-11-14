"""Run paired Mininet trials and generate a CSV and a Markdown comparison."""
import argparse
import csv
from datetime import datetime
import os
import signal
import subprocess
import sys
from statistics import median

from common import ROOT, read_json


def report(folder, rows):
    if not rows:
        return
    with (folder / "trials.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    lines = ["# D2 fixed vs adaptive polling", "",
             "Medians use observed values only; the counts below show missed detections.",
             "Requests count OpenFlow statistics requests, not bytes or CPU load.", "",
             "| Metric | Fixed | Adaptive |", "|---|---:|---:|"]
    modes = [[row for row in rows if row["mode"] == mode] for mode in ("fixed", "adaptive")]
    for label, key in [
        ("Quiet-phase requests/s", "normal_requests_per_second"),
        ("High-phase requests/s", "high_requests_per_second"),
        ("All measured phases requests/s", "overall_requests_per_second"),
        ("High-phase mean utilization (%)", "high_mean_utilization_percent"),
        ("Workload start to high alert (s)", "high_alert_seconds"),
        ("Load reduction to alert clear (s)", "load_recovery_seconds"),
        ("Agent stop to stale report alert (s)", "agent_stale_seconds"),
        ("Agent restart to recovery alert (s)", "agent_recovery_seconds"),
    ]:
        def cell(trials):
            values = [row[key] for row in trials if row[key] is not None]
            return f"{median(values):.2f} ({len(values)}/{len(trials)})" if values else f"— (0/{len(trials)})"
        lines.append(f"| {label} | {cell(modes[0])} | {cell(modes[1])} |")
    passed = [f"{sum(row['passed'] for row in trials)}/{len(trials)}" for trials in modes]
    lines += [f"| Trials passing every check | {passed[0]} | {passed[1]} |", "",
              "Mean utilization uses the byte change between the first and last port samples in the phase.",
              "See [trials.csv](trials.csv) for each trial, its evidence directory, and missing outcomes.",
              "Compare achieved utilization as context before attributing delay differences to polling."]
    (folder / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def trial_row(pair, mode, folder, run, summary):
    phases = summary["phases"]
    overall = sum(p["requests"] for p in phases.values()) / sum(p["seconds"] for p in phases.values())
    return {
        "pair": pair, "mode": mode, "passed": summary["all_checks_passed"],
        "normal_requests_per_second": phases["NORMAL"]["requests_per_second"],
        "high_requests_per_second": phases["HIGH"]["requests_per_second"],
        "overall_requests_per_second": round(overall, 3),
        "high_mean_utilization_percent": phases["HIGH"]["mean_utilization_percent"],
        **summary["delays"], "evidence": run.relative_to(folder).as_posix(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=int, default=3)
    args = parser.parse_args()
    if args.pairs < 1:
        parser.error("--pairs must be at least 1")
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        parser.error("Run inside Ubuntu with sudo.")
    folder = ROOT / "runs" / datetime.now().strftime("comparison-%Y%m%d-%H%M%S-%f")
    folder.mkdir(parents=True)
    rows = []
    for pair in range(1, args.pairs + 1):
        order = ("fixed", "adaptive") if pair % 2 else ("adaptive", "fixed")
        for mode in order:
            trial = folder / f"pair-{pair}-{mode}"
            trial.mkdir()
            print(f"Pair {pair}/{args.pairs}: {mode} (details: {trial / 'terminal.log'})", flush=True)
            with (trial / "terminal.log").open("w", encoding="utf-8") as log:
                with subprocess.Popen([sys.executable, "-u", str(ROOT / "run_demo.py"),
                                       "--mode", mode, "--output-parent", str(trial)],
                                      stdout=log, stderr=subprocess.STDOUT,
                                      start_new_session=True) as process:
                    try:
                        exit_code = process.wait()
                    except KeyboardInterrupt:
                        if process.poll() is None:
                            process.send_signal(signal.SIGINT)
                        process.wait()
                        print(f"Stopped. Completed results remain in {folder}.", flush=True)
                        return 130
            summaries = list(trial.glob("run-*/summary.json"))
            if exit_code not in (0, 2) or len(summaries) != 1:
                print(f"Trial did not finish. See {trial / 'terminal.log'}; prior results remain in {folder}.",
                      file=sys.stderr)
                return 1
            run = summaries[0].parent
            summary = read_json(summaries[0])
            rows.append(trial_row(pair, mode, folder, run, summary))
            report(folder, rows)
            print("  All checks passed." if summary["all_checks_passed"] else
                  "  Some checks were not observed; this trial is included.", flush=True)
    print(f"Comparison: {folder / 'comparison.md'}", flush=True)
    return 0 if all(row["passed"] for row in rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
