# D2 fixed vs adaptive polling

Medians use observed values only; the counts below show missed detections.
Requests count OpenFlow statistics requests, not bytes or CPU load.

| Metric | Fixed | Adaptive |
|---|---:|---:|
| Quiet-phase requests/s | 3.00 (3/3) | 1.60 (3/3) |
| High-phase requests/s | 2.91 (3/3) | 5.16 (3/3) |
| All measured phases requests/s | 2.93 (3/3) | 3.73 (3/3) |
| High-phase mean utilization (%) | 78.56 (3/3) | 76.42 (3/3) |
| Workload start to high alert (s) | 7.24 (3/3) | 6.18 (3/3) |
| Load reduction to alert clear (s) | 3.84 (3/3) | 3.42 (3/3) |
| Agent stop to stale report alert (s) | 4.71 (3/3) | 4.52 (3/3) |
| Agent restart to recovery alert (s) | 0.55 (3/3) | 0.60 (3/3) |
| Trials passing every check | 3/3 | 3/3 |

Mean utilization uses the byte change between the first and last port samples in the phase.
See [trials.csv](trials.csv) for each trial, its evidence directory, and missing outcomes.
Compare achieved utilization as context before attributing delay differences to polling.
