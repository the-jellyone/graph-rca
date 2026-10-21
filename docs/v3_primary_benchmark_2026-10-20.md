# V3 primary baseline and benchmark

DP retains the best cost-adjusted score. The selected DQN matches its multi-scope diagnosis accuracy and has lower compute latency, but spends more evidence credits.

| Reserved test result | Exact set | Mean return | Mean credits | Median decision ms |
|---|---:|---:|---:|---:|
| Single DP, 100 captures | 100.0% | 0.7455 | 5.090 | 4784.342 |
| Multi DP, 110 captures | 100.0% | 0.7327 | 5.345 | 5821.737 |
| Multi dqn-11, 110 captures | 100.0% | 0.6532 | 6.936 | 0.813 |

Decision latency excludes the 120-second observation window, telemetry collection, network and LLM narration. Full details and five-seed variability follow.

Primary comparisons audited. Full-plan completion is NOT claimed.
Multi-cause robustness is deferred and its partial results remain preserved.
This report reuses sealed reserved outcomes; it performs no policy tuning or reserved reruns.

One fresh cohort of 440 physical incidents, in 40 independent capture sessions.
Twenty sessions were reserved for training, ten for validation and ten for test.
The single-cause scope excludes the 40 declared pair incidents. The scopes share physical incidents.

Shipping measures service availability. It does not establish shipment fulfilment.
The only supported pair is stopped catalogue-db with payment CPU contention.
No unseen fault combination or continuously running live-agent result is claimed.

## Single scope

Validation selected `dp`. The strongest nonlearning baseline was `dp`.

| Method | Exact set | Pair | Healthy | Return | Credits | Probes | Median ms | p95 ms | Promotion |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| defer-immediately | 0.000 | - | 0.000 | 0.250 | 0.000 | 0.000 | 0.002 | 0.002 | not passed |
| dp | 1.000 | - | 1.000 | 0.746 | 5.090 | 3.060 | 4784.342 | 5333.498 | baseline |
| expanded-rule | 0.500 | - | 0.000 | 0.375 | 5.000 | 5.000 | 0.004 | 0.006 | not passed |
| expected-sarsa-11-hybrid | 0.970 | - | 1.000 | 0.719 | 5.030 | 3.630 | 0.126 | 5312.538 | not passed |
| expected-sarsa-11-tabular | 0.600 | - | 1.000 | 0.325 | 5.500 | 4.500 | 0.105 | 0.136 | not passed |
| expected-sarsa-22-hybrid | 0.500 | - | 0.000 | 0.385 | 4.800 | 4.200 | 0.113 | 4690.771 | not passed |
| expected-sarsa-22-tabular | 0.500 | - | 0.000 | 0.345 | 5.600 | 5.000 | 0.085 | 0.103 | not passed |
| expected-sarsa-33-hybrid | 0.400 | - | 0.000 | 0.325 | 4.500 | 4.500 | 0.112 | 65.161 | not passed |
| expected-sarsa-33-tabular | 0.400 | - | 0.000 | 0.300 | 5.000 | 5.000 | 0.096 | 0.128 | not passed |
| expected-sarsa-44-hybrid | 0.500 | - | 0.000 | 0.370 | 5.100 | 4.500 | 0.120 | 66.024 | not passed |
| expected-sarsa-44-tabular | 0.500 | - | 0.000 | 0.345 | 5.600 | 5.000 | 0.086 | 0.118 | not passed |
| expected-sarsa-55-hybrid | 1.000 | - | 1.000 | 0.722 | 5.560 | 4.030 | 0.127 | 5307.946 | not passed |
| expected-sarsa-55-tabular | 0.600 | - | 1.000 | 0.310 | 5.800 | 4.800 | 0.097 | 0.118 | not passed |
| fixed-31-0.6 | 0.700 | - | 1.000 | 0.467 | 4.660 | 3.860 | 0.004 | 0.006 | not passed |
| frozen-old-rule | 0.200 | - | 0.000 | 0.305 | 1.900 | 1.900 | 0.022 | 0.030 | not passed |
| random-valid-11 | 0.360 | - | 0.200 | 0.175 | 5.900 | 3.900 | 0.047 | 0.073 | not passed |
| random-valid-22 | 0.310 | - | 0.400 | 0.144 | 5.560 | 3.650 | 0.051 | 0.075 | not passed |
| random-valid-33 | 0.380 | - | 0.100 | 0.168 | 6.130 | 4.060 | 0.048 | 0.080 | not passed |
| random-valid-44 | 0.210 | - | 0.100 | 0.056 | 5.580 | 3.690 | 0.050 | 0.077 | not passed |
| random-valid-55 | 0.340 | - | 0.200 | 0.171 | 6.040 | 4.040 | 0.048 | 0.079 | not passed |
| stop-immediately | 0.200 | - | 0.000 | 0.200 | 0.000 | 0.000 | 0.001 | 0.002 | not passed |
| two-step-sarsa-11-hybrid | 0.700 | - | 1.000 | 0.425 | 5.500 | 4.000 | 0.116 | 71.267 | not passed |
| two-step-sarsa-11-tabular | 0.400 | - | 0.000 | 0.275 | 5.500 | 4.600 | 0.100 | 0.128 | not passed |
| two-step-sarsa-22-hybrid | 0.800 | - | 1.000 | 0.535 | 5.300 | 4.100 | 0.124 | 4762.302 | not passed |
| two-step-sarsa-22-tabular | 0.500 | - | 0.000 | 0.340 | 5.700 | 5.000 | 0.107 | 0.152 | not passed |
| two-step-sarsa-33-hybrid | 0.700 | - | 1.000 | 0.465 | 4.700 | 4.100 | 0.115 | 4647.413 | not passed |
| two-step-sarsa-33-tabular | 0.400 | - | 0.000 | 0.300 | 5.000 | 5.000 | 0.084 | 0.110 | not passed |
| two-step-sarsa-44-hybrid | 0.500 | - | 1.000 | 0.300 | 6.000 | 4.300 | 0.115 | 89.286 | not passed |
| two-step-sarsa-44-tabular | 0.400 | - | 0.000 | 0.265 | 5.700 | 4.700 | 0.085 | 0.106 | not passed |
| two-step-sarsa-55-hybrid | 0.970 | - | 1.000 | 0.702 | 5.360 | 3.960 | 0.128 | 5313.353 | not passed |
| two-step-sarsa-55-tabular | 0.600 | - | 1.000 | 0.310 | 5.800 | 4.800 | 0.085 | 0.108 | not passed |
| voi | 0.900 | - | 1.000 | 0.637 | 5.260 | 3.930 | 51.056 | 62.871 | not passed |

Training resource figures use active wall time and whole-process peak resident memory, including validation.

| Learner | Active seconds | Peak MiB |
|---|---:|---:|
| expected-sarsa-11-hybrid | 457.91 | 172.79 |
| expected-sarsa-11-tabular | 457.91 | 172.79 |
| expected-sarsa-22-hybrid | 458.31 | 174.54 |
| expected-sarsa-22-tabular | 458.31 | 174.54 |
| expected-sarsa-33-hybrid | 452.56 | 172.12 |
| expected-sarsa-33-tabular | 452.56 | 172.12 |
| expected-sarsa-44-hybrid | 452.06 | 174.02 |
| expected-sarsa-44-tabular | 452.06 | 174.02 |
| expected-sarsa-55-hybrid | 456.22 | 173.42 |
| expected-sarsa-55-tabular | 456.22 | 173.42 |
| two-step-sarsa-11-hybrid | 451.75 | 203.86 |
| two-step-sarsa-11-tabular | 451.75 | 203.86 |
| two-step-sarsa-22-hybrid | 445.30 | 208.04 |
| two-step-sarsa-22-tabular | 445.30 | 208.04 |
| two-step-sarsa-33-hybrid | 440.38 | 205.32 |
| two-step-sarsa-33-tabular | 440.38 | 205.32 |
| two-step-sarsa-44-hybrid | 447.56 | 206.17 |
| two-step-sarsa-44-tabular | 447.56 | 206.17 |
| two-step-sarsa-55-hybrid | 444.00 | 209.07 |
| two-step-sarsa-55-tabular | 444.00 | 209.07 |

| Family | Seeds | Mean return | Return sample SD | Mean accuracy | Accuracy sample SD |
|---|---:|---:|---:|---:|---:|
| expected-sarsa/hybrid | 5 | 0.504 | 0.199 | 0.674 | 0.287 |
| expected-sarsa/tabular | 5 | 0.325 | 0.020 | 0.520 | 0.084 |
| two-step-sarsa/hybrid | 5 | 0.485 | 0.148 | 0.734 | 0.171 |
| two-step-sarsa/tabular | 5 | 0.298 | 0.030 | 0.460 | 0.089 |

Complete decision traces, per-root metrics, calibration and paired session intervals are in `runs/adaptive_v3_single/compare-test.json`.
Robustness status: complete. Saved groups: {'all_telemetry': 32, 'container_stats': 32, 'double-prices': 32, 'half-prices': 32, 'logs': 32, 'request_metrics': 32}. Raw report: `runs/adaptive_v3_single/challenge.json`.

## Multi scope

Validation selected `dp`. The strongest nonlearning baseline was `dp`.

| Method | Exact set | Pair | Healthy | Return | Credits | Probes | Median ms | p95 ms | Promotion |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| defer-immediately | 0.000 | 0.000 | 0.000 | 0.250 | 0.000 | 0.000 | 0.002 | 0.002 | not passed |
| dp | 1.000 | 1.000 | 1.000 | 0.733 | 5.345 | 3.264 | 5821.737 | 8131.145 | baseline |
| dqn-11 | 1.000 | 1.000 | 1.000 | 0.653 | 6.936 | 4.427 | 0.813 | 1.067 | not passed |
| dqn-22 | 1.000 | 1.000 | 1.000 | 0.629 | 7.418 | 4.882 | 0.880 | 1.376 | not passed |
| dqn-33 | 1.000 | 1.000 | 1.000 | 0.623 | 7.545 | 5.000 | 0.902 | 1.329 | not passed |
| dqn-44 | 0.918 | 0.400 | 1.000 | 0.566 | 7.045 | 4.818 | 0.946 | 1.652 | not passed |
| dqn-55 | 0.973 | 1.000 | 1.000 | 0.632 | 6.818 | 4.545 | 0.859 | 1.104 | not passed |
| expanded-rule | 0.545 | 1.000 | 0.000 | 0.414 | 4.909 | 4.727 | 0.008 | 0.017 | not passed |
| expected-sarsa-11-hybrid | 0.455 | 1.000 | 0.000 | 0.309 | 5.182 | 4.455 | 0.156 | 8488.145 | not passed |
| expected-sarsa-11-tabular | 0.455 | 1.000 | 0.000 | 0.282 | 5.727 | 5.000 | 0.144 | 0.251 | not passed |
| expected-sarsa-22-hybrid | 0.545 | 1.000 | 0.000 | 0.382 | 5.091 | 4.455 | 0.193 | 128.098 | not passed |
| expected-sarsa-22-tabular | 0.545 | 1.000 | 0.000 | 0.355 | 5.636 | 5.000 | 0.237 | 0.359 | not passed |
| expected-sarsa-33-hybrid | 0.818 | 1.000 | 1.000 | 0.532 | 5.727 | 4.091 | 0.296 | 12909.791 | not passed |
| expected-sarsa-33-tabular | 0.455 | 1.000 | 0.000 | 0.291 | 6.000 | 5.000 | 0.086 | 0.116 | not passed |
| expected-sarsa-44-hybrid | 0.545 | 0.000 | 1.000 | 0.277 | 5.364 | 4.727 | 0.121 | 0.223 | not passed |
| expected-sarsa-44-tabular | 0.545 | 0.000 | 1.000 | 0.264 | 5.636 | 5.000 | 0.120 | 0.202 | not passed |
| expected-sarsa-55-hybrid | 0.818 | 1.000 | 1.000 | 0.541 | 5.545 | 4.000 | 0.279 | 12218.230 | not passed |
| expected-sarsa-55-tabular | 0.455 | 1.000 | 0.000 | 0.295 | 5.909 | 5.000 | 0.122 | 0.196 | not passed |
| fixed-0-0.6 | 0.673 | 0.400 | 1.000 | 0.437 | 4.718 | 3.718 | 0.008 | 0.014 | not passed |
| frozen-old-rule | 0.182 | 0.000 | 0.000 | 0.273 | 1.818 | 1.818 | 0.039 | 0.069 | not passed |
| random-valid-11 | 0.191 | 0.000 | 0.200 | 0.017 | 5.927 | 3.891 | 0.118 | 0.198 | not passed |
| random-valid-22 | 0.282 | 0.100 | 0.500 | 0.131 | 5.555 | 3.645 | 0.110 | 0.168 | not passed |
| random-valid-33 | 0.309 | 0.200 | 0.300 | 0.116 | 6.000 | 3.964 | 0.104 | 0.154 | not passed |
| random-valid-44 | 0.264 | 0.100 | 0.200 | 0.098 | 5.682 | 3.764 | 0.102 | 0.166 | not passed |
| random-valid-55 | 0.273 | 0.200 | 0.500 | 0.106 | 5.973 | 3.973 | 0.089 | 0.133 | not passed |
| stop-immediately | 0.182 | 0.000 | 0.000 | 0.182 | 0.000 | 0.000 | 0.002 | 0.004 | not passed |
| two-step-sarsa-11-hybrid | 0.818 | 1.000 | 1.000 | 0.545 | 5.455 | 4.273 | 0.152 | 6871.621 | not passed |
| two-step-sarsa-11-tabular | 0.455 | 1.000 | 0.000 | 0.282 | 5.727 | 5.000 | 0.107 | 0.129 | not passed |
| two-step-sarsa-22-hybrid | 1.000 | 1.000 | 1.000 | 0.727 | 5.455 | 3.727 | 87.308 | 7110.139 | not passed |
| two-step-sarsa-22-tabular | 0.455 | 1.000 | 0.000 | 0.295 | 5.909 | 5.000 | 0.103 | 0.128 | not passed |
| two-step-sarsa-33-hybrid | 0.636 | 1.000 | 0.000 | 0.427 | 6.000 | 4.545 | 0.137 | 101.852 | not passed |
| two-step-sarsa-33-tabular | 0.455 | 1.000 | 0.000 | 0.273 | 5.909 | 5.000 | 0.111 | 0.155 | not passed |
| two-step-sarsa-44-hybrid | 1.000 | 1.000 | 1.000 | 0.709 | 5.818 | 3.455 | 98.220 | 12674.626 | not passed |
| two-step-sarsa-44-tabular | 0.636 | 1.000 | 1.000 | 0.318 | 6.364 | 4.455 | 0.120 | 0.187 | not passed |
| two-step-sarsa-55-hybrid | 0.818 | 1.000 | 1.000 | 0.541 | 5.545 | 3.909 | 0.319 | 12849.821 | not passed |
| two-step-sarsa-55-tabular | 0.455 | 1.000 | 0.000 | 0.295 | 5.909 | 4.909 | 0.303 | 0.553 | not passed |
| voi | 0.764 | 0.400 | 1.000 | 0.518 | 4.909 | 4.182 | 92.226 | 150.507 | not passed |

Training resource figures use active wall time and whole-process peak resident memory, including validation.

| Learner | Active seconds | Peak MiB |
|---|---:|---:|
| dqn-11 | 2798.00 | 930.37 |
| dqn-22 | 2568.26 | 901.25 |
| dqn-33 | 3913.12 | 855.55 |
| dqn-44 | 3149.53 | 958.60 |
| dqn-55 | 2811.02 | 940.33 |
| expected-sarsa-11-hybrid | 484.17 | 174.33 |
| expected-sarsa-11-tabular | 484.17 | 174.33 |
| expected-sarsa-22-hybrid | 482.74 | 178.93 |
| expected-sarsa-22-tabular | 482.74 | 178.93 |
| expected-sarsa-33-hybrid | 489.84 | 176.09 |
| expected-sarsa-33-tabular | 489.84 | 176.09 |
| expected-sarsa-44-hybrid | 483.92 | 174.66 |
| expected-sarsa-44-tabular | 483.92 | 174.66 |
| expected-sarsa-55-hybrid | 490.67 | 174.37 |
| expected-sarsa-55-tabular | 490.67 | 174.37 |
| two-step-sarsa-11-hybrid | 497.24 | 212.81 |
| two-step-sarsa-11-tabular | 497.24 | 212.81 |
| two-step-sarsa-22-hybrid | 483.30 | 213.59 |
| two-step-sarsa-22-tabular | 483.30 | 213.59 |
| two-step-sarsa-33-hybrid | 496.80 | 207.30 |
| two-step-sarsa-33-tabular | 496.80 | 207.30 |
| two-step-sarsa-44-hybrid | 504.22 | 210.29 |
| two-step-sarsa-44-tabular | 504.22 | 210.29 |
| two-step-sarsa-55-hybrid | 576.99 | 209.56 |
| two-step-sarsa-55-tabular | 576.99 | 209.56 |

| Family | Seeds | Mean return | Return sample SD | Mean accuracy | Accuracy sample SD |
|---|---:|---:|---:|---:|---:|
| dqn/dqn | 5 | 0.621 | 0.033 | 0.978 | 0.036 |
| expected-sarsa/hybrid | 5 | 0.408 | 0.123 | 0.636 | 0.170 |
| expected-sarsa/tabular | 5 | 0.297 | 0.034 | 0.491 | 0.050 |
| two-step-sarsa/hybrid | 5 | 0.590 | 0.126 | 0.855 | 0.152 |
| two-step-sarsa/tabular | 5 | 0.293 | 0.017 | 0.491 | 0.081 |

Complete decision traces, per-root metrics, calibration and paired session intervals are in `runs/adaptive_v3_multi/compare-test.json`.
Robustness status: INCOMPLETE / deferred. Saved groups: {'all_telemetry': 1, 'container_stats': 37, 'logs': 37, 'request_metrics': 37}. Raw report: `runs/adaptive_v3_multi/challenge.json`.

## Interpretation for the demo

DP was selected on validation in both scopes. This remains the selected overall policy.
The validation-selected multi-cause DQN representative is dqn-11; do not choose another seed from test performance.

DQN representative: 100.0% exact-set accuracy, 100.0% pair accuracy, 6.936 mean credits, 0.6532 mean return. Decision median/p95: 0.813/1.067 ms.

DP: 100.0% exact-set accuracy, 5.345 credits, 0.7327 return; decision median/p95 5821.737/8131.145 ms.

Across five DQN seeds, accuracy mean/sample SD is 97.8%/0.0356; pair accuracy mean is 88.0%. The representative result does not describe every seed.

The DQN representative did not pass the predeclared promotion rule against DP. Its accuracy passes the threshold, but its cost-adjusted return is lower.

DQN minus DP paired mean return: -0.07955; 95% session-bootstrap interval [-0.08318181818181815, -0.07590909090909087]. The interval uses ten independent test sessions and 10,000 resamples, seed 11.

DQN is much faster per decision but buys more evidence than DP; the cost-adjusted objective favors DP. Compute latency is measured separately and is not a term in this experiment objective. The companion JSON retains paired return intervals from the original comparison; no new test search was conducted.

These results concern 100 single-scope and 110 multi-scope test captures across ten shared test sessions. The scopes overlap; they are not 210 independent physical experiments. Only one fault pair and one lab environment were measured. VM deployment, live inference and repair require a separate rehearsal. Memory pressure is an intervention, not proof of customer degradation.

## Reproduction and audit scope

On the original Windows experiment host, from the application directory with preserved local datasets and checkpoints:

```powershell
.\experiments\v3\neural\.venv\Scripts\python.exe scripts/audit_v3_primary_benchmark.py
.\experiments\v3\neural\.venv\Scripts\python.exe scripts/report_v3_primary_benchmark.py --output docs/v3_primary_benchmark_2026-10-20.md
```

The audit verifies the raw cohort, training-only fits, encoder/split provenance, frozen checkpoints, primary report coverage and preserved old artifacts. The report recomputes all saved outcome summaries, checks diagnosis correctness, budget and return arithmetic, and reads the recorded compute measurements. Neither command retrains a policy or runs a new reserved test investigation.

The original full completion audit and report scripts are unchanged. The original phase controller remains paused with multi-cause robustness unfinished. The teammate should use the recorded results and the separate GCP handoff; the frozen host audit is not a VM deployment command.
