# Team 2 — D2 Network Monitoring and Alert System

Problem 6. Three Mininet agents send UDP traffic and telemetry to a monitoring
server. A Python OS-Ken controller learns forwarding rules and combines switch
measurements with UDP telemetry. D2 adds adaptive OpenFlow polling and reporting
of a silent or returning agent, with a fixed polling mode for comparison.

```text
h1, h2, h3 --> s1 -- 7 Mbps link -- s2 --> h4 (10.0.0.4:9999 UDP)
                 s1 and s2 <--> controller (127.0.0.1:6653, OpenFlow 1.3)
                 h4 writes telemetry.json; controller reads it in the same VM
```

Agent IPs: 10.0.0.1 through 10.0.0.3. Port counters measure RX on s2-eth1,
toward the server, after the shaped link. Selected switch-2 flow counters measure
each agent's application traffic. Agents send T-prefixed JSON reports and
D-prefixed generated data using Python UDP sockets. A report includes a host ID,
run ID, sequence, and cumulative sent data counters. The receiver validates
reports and ignores duplicates and old runs. Mininet host processes run
concurrently; the receiver uses a single datagram loop.

## Reproduce

Use the same Ubuntu VM as D1 with Python 3, Mininet, Open vSwitch and OS-Ken
installed. On a clean Ubuntu setup, the required packages are
`mininet`, `openvswitch-switch`, and `python3-os-ken`. Run from a local Linux
directory rather than directly on a VirtualBox shared folder. Exit any older
Mininet session and stop its controller first.

```bash
python3 -m unittest discover -s tests -v
sudo python3 run_demo.py
sudo python3 benchmark.py --pairs 3
```

The first demo command runs adaptive mode in about 90 seconds. Add
`--mode fixed` to see the reference behavior. The comparison command runs six
trials sequentially (roughly 9–10 minutes); `--pairs 1` runs a quick pair.
A run exit status of 2 means an expected outcome was not observed; its result
is still saved and retained in the comparison. A launch error stops the
comparison and points to the terminal log. Ctrl+C stops the current run.

## Policy

- Fixed mode requests statistics about every 1 s throughout. Adaptive mode
  starts at 2 s, switches to 0.5 s at 50% measured utilization or on stale
  telemetry, and returns to 2 s after 4 s of healthy telemetry and utilization
  at or below 40%. The controller uses measurements for these decisions; it
  does not read the demo phase. The status loop runs separately from polling.
- Both modes use the same link alert rule. Qualifying fresh samples must span
  at least 2 s above 70% to raise an alert, or below 60% to clear it. A contrary
  or unusable measurement interrupts confirmation. Missing data does not clear
  an active alert. A sample is a rate averaged over a counter interval, not
  a continuous observation.
- A reporting agent becomes STALE after 5 s without an accepted report, and a
  fresh report marks recovery. Agent events are separate from link alerts.
  Silence does not identify the root cause. The experiment stops only the
  h2 sender, leaving the host and network intact; h1 and h3 should keep reporting.
- D2 changes how intensively the controller observes the network. It does not
  throttle or reroute traffic. The launcher schedules the offered load and
  intentionally restarts the sender. The controller detects these changes.

## What to show

| Phase | Point out |
|---|---|
| Normal, 0.1 Mbps per agent | Ping connectivity, LIVE agents, flow rates, low link utilization, quiet polling. |
| High, 3.2 Mbps target per agent | Measured utilization, polling speeding up, then HIGH if sustained above threshold. |
| Recovery, 0.1 Mbps per agent | Alert clearing after sustained low measurements, polling slowing again. |
| Agent stopped | Only h2 becomes STALE, h1/h3 continue sending, adaptive monitoring speeds up. |
| Agent restarted | h2 returns to LIVE with a new run ID; monitoring recovers. |

The launcher also injects one malformed UDP datagram and checks that it was
rejected. At the end show the checks and the actual evidence path. A target
sending rate is a workload setting, not measured throughput.

The first D2 trial used a 10 Mbps link. The agents achieved about 2.1 Mbps of
payload each in its high phase, and measured utilization never remained above
70% long enough to raise an alert. The D2 experiment now shapes the link to
7 Mbps in both modes to make sustained high utilization easier to exercise on
this VM. The alert policy is unchanged. Use only runs with identical settings
for the fixed-versus-adaptive comparison.

## Results and submission

Each run saves `summary.json`, `configuration.json`, `topology.json`,
`phase_times.json`, `polling.jsonl`, `alerts.jsonl` (if events occurred),
telemetry, raw statistics, combined measurements, and component logs.
The benchmark writes `comparison.md` and `trials.csv` under
`runs/comparison-.../`, with links to individual trial evidence. It alternates
mode order and uses the same topology, VM, workload and alert rules.

Compare quiet and high requests per second, actual utilization, and delays
from the scripted stimulus to each observed event. Counts refer to logical
OpenFlow requests/completed replies, not bytes or CPU consumption. The table
reports medians and observation counts; missing alerts are not zero-delay
successes. Compare achieved utilization before attributing a delay difference
to polling. Adaptive mode may save requests while quiet, use more when busy,
and take longer to first notice a sudden change.

The [D2 results write-up](D2_RESULTS.md) records the completed six-trial Ubuntu
benchmark and links to the recorded table, CSV, and selected evidence for every
trial. Keep bulk logs separately if needed; `runs/` is ignored.
Share this project folder in the team's GitHub repository and verify instructor
access.

| Rubric | Evidence |
|---|---|
| Application functionality (4) | UDP agents, server, validated reports, combined switch and host measurements, alerts. |
| Dynamic SDN (6) | Controller changes OpenFlow request intervals based on observed conditions; polling event log. |
| Challenging scenario (4) | Stop/restart h2, record stale/recovered events and continuing reports from h1/h3. |
| Performance comparison (5) | Same scenario in both modes, repeated trials, CSV and Markdown from actual measurements. |
| Documentation, demo and viva (6) | This README, runnable checks, saved evidence and final GitHub handoff. |

For viva: explain why UDP is suitable for periodic reports, where the two
measurement sources come from, why one high spike does not raise an alert,
what changes between fixed and adaptive mode, and why loss of telemetry does
not prove link failure.
