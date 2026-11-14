# D2 results — fixed vs. adaptive OpenFlow polling

Team 2 · Problem 6: Network Monitoring and Alert System  
Benchmark: 14 November 2026 · Ubuntu VM / Mininet

We compared fixed and adaptive statistics polling over three paired trials.
Each trial used three UDP agents, two OpenFlow switches, one monitoring server,
and the same 7 Mbps inter-switch link. The script applied light traffic, high
traffic, load reduction, then stopped and restarted the h2 sender. The controller
made its decisions from switch counters and UDP telemetry.

Both modes used the same alert rules: above 70% to raise a link alert and below
60% to clear it, with qualifying samples spanning at least two seconds. Fixed
polling used a 1 s interval. Adaptive polling used 2 s while quiet and 0.5 s
when measured utilization rose to 50% or an agent went stale. The benchmark
alternated the order between pairs. See the [recorded configuration and trial artifacts](evidence/comparison-20261114-112425-010628/comparison.md).

| Metric (median of three trials per mode) | Fixed | Adaptive |
|---|---:|---:|
| Quiet-phase statistics requests/s | 3.00 | 1.60 |
| High-phase statistics requests/s | 2.91 | 5.16 |
| Statistics requests/s across all measured phases | 2.93 | 3.73 |
| High-phase mean link utilization | 78.56% | 76.42% |
| Workload increase to high alert | 7.24 s | 6.18 s |
| Load reduction to alert clear | 3.84 s | 3.42 s |
| Agent stop to stale alert | 4.71 s | 4.52 s |
| Agent restart to recovery alert | 0.55 s | 0.60 s |
| Trials passing all applicable checks | 3/3 | 3/3 |

**Adaptive polling concentrated requests where the controller saw a need for
more data.** It made about 47% fewer requests per second during quiet traffic
and about 27% more across the full test. The median high-alert delay was about
one second shorter in adaptive mode, but adaptive was faster in only one of the
three matched pairs. High-alert delays ranged from 5.88–13.21 s for fixed and
6.13–20.16 s for adaptive. Workload start times, sampling cadence and achieved
utilization affect those delays; the results do not establish a consistent
speed advantage. Median link-alert clearance was also shorter in adaptive mode.

**All six trials detected the agent outage and return.** The launcher stopped
the h2 process while leaving its host and links up. The controller reported
h2 stale, and h1 and h3 continued reporting. After restart, a fresh report
marked h2 recovered. Detection times were similar because the status loop checks
telemetry freshness separately from OpenFlow polling. The stale timer starts at
the last valid report, so time from the stop action can be under five seconds.

The counts are logical OpenFlow statistics requests, not measured bandwidth
or CPU overhead. Utilization uses RX bytes on the server-facing side of the
shaped link. Delay is measured from the scripted action, so it includes traffic
build-up and alert confirmation. The controller observes recovery; the test
script reduces the load and restarts the sender.

Evidence: [generated comparison](evidence/comparison-20261114-112425-010628/comparison.md)
and [per-trial CSV](evidence/comparison-20261114-112425-010628/trials.csv).
The CSV's evidence column identifies the folder for each trial's configuration,
phase timestamps, summary, alert events, polling events and terminal output.
The full original benchmark archive retains the additional raw logs.
