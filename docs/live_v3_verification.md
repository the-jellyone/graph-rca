# V3 live adapter verification

These are local engineering checks, not a new reserved benchmark. No policy was trained or tuned.

| Scenario | Live DQN correct | Agent repair | Recovery verified at, seconds |
| --- | --- | --- | ---: |
| carts_memory | True | True | 170.1 |
| catalogue_db_payment_cpu | True | True | 168.1 |
| catalogue_db_stop | True | True | 166.8 |
| healthy | True | False | 171.5 |
| orders_db_stop | True | False | 167.3 |
| orders_payment_delay | True | True | 175.9 |
| orders_stop | True | True | 198.8 |
| payment_cpu | True | False | 169.2 |
| payment_stop | True | False | 170.4 |
| shipping_stop | True | True | 186.7 |
| user_stop | True | True | 168.7 |

Every run passed five consecutive auth, checkout and shipping availability checks. Injector cleanup is separate from agent-directed repair. The healthy control needs no repair.

| Shadow policy | Correct / 11 | Average probes | Average credits | Mean choice, ms |
| --- | ---: | ---: | ---: | ---: |
| dqn | 11 / 11 | 4.55 | 7.18 | 3.91 |
| dp | 11 / 11 | 3.27 | 5.36 | 6843.00 |
| voi | 9 / 11 | 4.18 | 4.91 | 87.80 |
| expected-sarsa | 6 / 11 | 5.00 | 5.64 | 3.05 |
| two-step-sarsa | 7 / 11 | 4.45 | 6.36 | 3.27 |
| expected-sarsa-hybrid | 9 / 11 | 4.00 | 5.55 | 1912.01 |
| two-step-sarsa-hybrid | 11 / 11 | 3.73 | 5.45 | 2962.83 |
| random-valid | 4 / 11 | 5.00 | 7.00 | 2.23 |
| expanded-rule | 6 / 11 | 4.73 | 4.91 | 2.17 |

Each shadow policy investigated the same fresh public windows after recovery. This does not establish live repair for every policy/scenario pair. Model confidence is not calibrated accuracy. The GCP VM still needs its own rehearsal.

An earlier healthy engineering attempt failed at the graph packet checksum boundary. The adapter used a nonexistent packet hash attribute. The corrected adapter uses the canonical packet digest. That failed receipt, source snapshot and successful recovery checks remain preserved. No classifier or policy weights changed.
