# V3 early-decision verification

Local engineering checks on 20 October 2026. These are separate from the fixed-window benchmark.

The final protocol is `live_v3_rolling_early_r2`. Frozen source, encoder, model, checkpoints and bundle identities passed validation. Container identities and resource limits passed verification. The lab is healthy, proxy delay is zero, and ownership markers are clear.

| Protocol | Policy | Case | Outcome | Diagnosis seconds | Overall seconds | Agent repair |
| --- | --- | --- | --- | ---: | ---: | --- |
| live_v3_rolling_early_r1 | dqn | catalogue_db_payment_cpu | resource/catalogue-db, resource/payment | 20.7 | 27.6 | True |
| live_v3_rolling_early_r1 | dqn | healthy | DEFER | 121.1 | 125.2 | False |
| live_v3_rolling_early_r1 | dqn | catalogue_db_stop | resource/catalogue-db | 42.8 | 48.3 | True |
| live_v3_rolling_early_r1 | dqn | carts_memory | resource/carts | 18.6 | 25.6 | True |
| live_v3_rolling_early_r2 | voi | catalogue_db_stop | DEFER | 124.4 | 129.1 | False |
| live_v3_rolling_early_r2 | dp | catalogue_db_payment_cpu | resource/catalogue-db, resource/payment | 20.7 | 29.3 | True |
| live_v3_rolling_early_r2 | dqn | catalogue_db_payment_cpu | resource/catalogue-db, resource/payment | 20.7 | 28.0 | True |

Overall time runs from the injection request through verified recovery and graph cleanup. Initial healthy preparation and external narration time are excluded. The 120-second limit applies to observation, not total response time.

The R1 trials are preserved as preliminary checks. R2 adds the V2 graph-confirmation obligation and excludes Docker measurement intervals crossing the fault boundary. It discards identical redraws before interval annotation, while conflicting same-time measurements still fail closed.

Two failed engineering attempts are retained in the JSON report. One exposed a DQN observation-objective mismatch. The other exposed duplicate Docker redraws receiving conflicting interval metadata. Both failures restored the lab and passed recovery checks. Neither failure changed trained weights or thresholds.

Seventy-six focused tests passed. They cover frozen versus live temporal contracts, real sample coverage, readiness, cross-boundary requests, duplicate Docker reports, one ledger with paid refreshes, freshness, confidence, both-cause support, graph confirmation, DQN observation compatibility, narration and existing workload/capture behaviour.

All nine policy options also completed 63 saved-prefix inference checks. These replay a single snapshot using virtual evidence time, not real-time collection. A policy may safely defer. They are compatibility checks, not measured live diagnosis latencies or physical repair trials for every policy.

Terminal 1 and Terminal 2 were exercised against the event journal. The configured external narrator produced timing commentary. Its prompt now distinguishes lab restoration by injector cleanup from agent-directed repair, so a false agent-resolution flag does not imply a continuing outage. Unit tests verify the exact provider facts and exclude private case and incident labels.

Limits: no retraining, no reserved-response reads, no new short-window calibration study and no GCP rehearsal. The full eleven-scenario early-mode physical sweep has not been run. The healthy control deferred rather than reaching a confident healthy classification. VOI on catalogue-db stop also deferred and used injector cleanup. These outcomes are retained, not described as successful agent repairs.

Use `scripts/live_v3.py serve --collection early` for the new mode or `--collection windowed` for the original fixed-window mode. See `live_v3_demo.md` for the three-terminal instructions.
