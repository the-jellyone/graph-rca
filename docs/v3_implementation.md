# V3 implementation progress

Publication handoff, 20 October 2026: the V3 work developed on `dqn` is being
published to the parent repository's `main`. The GCP guide now uses the current
adapter, pinned neural environment, separately transferred inference/image
exports and explicit early/windowed commands. The older branch descriptions
below are historical. GCP deployment remains unverified.
The pre-publication application regression suite passed all 307 tests. The
unchanged inference bundle passed checks for all nine policy options, and the
unpublished V3 history plus staged files passed a credential-pattern scan.

20 October live update: the CLI now defaults to `live_v3_rolling_early_r2`,
porting V2's rolling collection, one paid ledger, charged freshness refreshes
and public graph confirmation to V3. The existing full-window protocol remains
available with `--collection windowed`. Both terminals show elapsed response
time, and receipts record diagnosis, repair-command and overall recovery times
from the injection request. This changes the live adapter only. Frozen encoder,
model, policies, source package and benchmark results remain unchanged. See
`live_v3_demo.md` and `live_v3_early_verification.md` for the separate engineering
checks and their limits. The earlier fixed-window results below remain valid
for their original protocol.

Current status, 20 October 2026: the primary 440-capture cohort, diagnostic
fitting, training and both reserved comparisons are complete. The historical
progress entries below retain earlier states and are not the current run status.
Read `v3_primary_benchmark_2026-10-20.md` for audited results. Multi-cause
robustness remains incomplete. The separate `rca_live_v3` adapter now connects
frozen policy inference, fresh coherent measurements, Neo4j evidence and owned
repairs. Its commands and deployment requirements are in `live_v3_demo.md`.
Local live verification is recorded separately from benchmark evaluation; GCP
rehearsal remains the deployment owner's responsibility.

The local adapter rehearsal completed all eleven fresh scenarios with DQN.
All eleven diagnoses matched the owned scenario causes. Seven fault cases
passed agent-directed repair; payment CPU, payment stop and orders-db stop
used injector cleanup because confidence or paid mechanism evidence did not
meet the repair gate. Healthy operation needed no repair. Every run passed
five consecutive auth, checkout and shipping-availability operation groups.
All nine policy options also completed 99 shadow investigations on the same
fresh public windows after recovery. Those are engineering comparisons, not
new reserved results or 99 physical repair trials. The actual three-terminal
CLI was separately checked with VOI and catalogue-db stop; its diagnosis was
correct but below the repair-confidence threshold, so it reported injector
cleanup and confirmed recovery. Fifty-eight focused regression tests passed.
See `live_v3_verification.md`, its JSON companion and `live_v3_handoff_audit.json`.

Work is on the `dqn` development branch. Commit
`646a999` contains the initial work from this chat. The nested repository's
branch has not been changed.

## What the project measures

The original pipeline builds a live Neo4j dependency graph and offers an LLM
investigation interface. The separate `rca_investigation` package runs the
controlled evidence-acquisition experiments. It uses a fixed Sock Shop topology,
immutable physical capture windows, separately stored private labels and a
training-fitted diagnostic model. An acquisition policy buys a tool result,
stops or defers. The diagnostic model chooses the root on STOP.

The parent `rca_sim` package is the earlier synthetic PPO simulator. It is not
the implementation target for this plan. PPO and arbitrary unseen fault
combinations remain outside v3.

The revised v2c report records 93.3% main-test accuracy for VOI and depth-two DP.
SARSA did not pass its promotion criteria. The original failed encoder remains
preserved. V3 will use a fresh cohort rather than relabeling those captures.

## Completed software and healthy checks

- Phase 0 has a pre-change checkout archive, an index of 64,308 old artifact
  checksums, live resource/image identities, the helper identity and a final
  experiment declaration. The final declaration supersedes the preliminary
  inventory-pending declaration. Its preserved runtime hash comes from the
  archive, not the subsequently edited checkout.
- Phase 1 has canonical empty/singleton/pair cause sets, separate DEFER
  decisions, private component mechanisms and receipts, exact-set utility,
  descriptive root metrics, explicit record decoding, a joint prototype model,
  replay transitions, JSON round trips and multiple-root explanations.
- Phase 2 has independent auth and shipping operation contracts, fresh login
  sessions, and checkout against a basket prepared while healthy. Twenty healthy
  checks of each operation passed. The v3 capability registry has separate paths.
- Phase 3 has exact-identity stops, a persisted CPU lease, a composed injection
  journal, independent component verification and cleanup of every component.
  All thirty mechanics pilots have been accepted separately from
  evaluation captures. Rejected attempts and confirmed recovery remain recorded.
- The eight decision fixtures have hand-calculated depth-one and depth-two
  references. All 24 fixture runs, eight each for Expected SARSA, two-step SARSA
  and DQN, matched those references under separate synthetic paths.
- Vanilla DQN has a public numeric schema, masked exploration/inference and
  bootstrap, uniform training-only replay, Huber loss, an Adam optimizer, hard
  target copies and episode-boundary checkpoints. Neural checks passed in the
  isolated pinned CPU environment, including deterministic continuation and
  preserved sampling/action counts. Checkpoints record active training time and
  the OS-reported peak resident memory of the whole process, including validation.
- The 440-slot primary controller, fixed session split, raw bundle checksums,
  separate single/multi projections and train-only encoder fitting are implemented.
  The per-stratum validation gate prevents training before the diagnostic model
  passes. These paths have not yet consumed a physical evaluation cohort.
- Balanced training and validation-only selection are wired to the existing
  tabular policies and separate DQN trainer. Comparison manifests pin source,
  inputs, checkpoint bytes and the CPU environment. Both distinct scopes must
  be sealed before either reserved comparison can run. Validation and reserved
  evaluation save resumable partial results.
- The declared robustness runner evaluates frozen policies under four paid
  source outages and two public price changes. Operating-mixture estimates
  reweight completed primary outcomes with fixed priors. Pair analysis uses
  coherent physical pair windows and distinguishes failing to buy relevant
  tools from unavailable evidence. Price challenges preserve DQN weights and
  feature scaling while explicitly updating its public price inputs.
- `scripts/run_v3_phases.py` serializes phases, records failures and resumes
  complete checkpoints. It requires a current-source regression/fixture proof
  before freezing the runtime for collection. Both scopes pass their diagnostic
  gates before training. The audit checks raw/derived checksums, independent
  sessions, fitted model recomputation, all seed checkpoints and frozen reports.
  The benchmark writer reads completed reports without rerunning reserved tests.

The baseline regression passed 197 tests before the v3 changes. The latest
isolated neural regression passed all 232 tests in 178.15 seconds, including
collector failure handling, checkpoint continuation, encoder fitting, schedule
integrity and comparison sealing. All 24 learner reference checks passed again
on the final source. Primary collection has started under its frozen identity.

## Physical scope and recovery

Shipping is service availability only. The upstream shipping POST handler
returns success even when sending to its queue fails. This experiment does not
establish completed fulfilment or lost shipments. The availability workload
checks the actual shipping application entry in `/health` through the existing
front-end container's Node runtime.

The prepared basket survives checkout. Repeated v3 checkout therefore sends
`POST /orders` without performing catalogue-dependent basket preparation during
the fault window. Failed account/basket preparation is a generator error and
cannot become invented checkout evidence.

The first orders-db pilot restarted the database but failed its immediate
application recovery check. That attempt was rejected and its measurements were
retained. A subsequent explicit recovery confirmed auth 200, checkout 201 and
shipping health 200 before clearing the owned block. The pilot controller now
allows a bounded 90-second readiness wait after component cleanup. Engineering
source revisions are recorded explicitly and retain already accepted slots.
This revision mechanism is unavailable to the primary evaluation cohort.

Repeated Docker CLI startup reduced one stopped-resource window to 47 state
samples, below the declared minimum of 48. That attempt was rejected. The
collector now uses one owned Docker stats stream; a bounded healthy check
produced 21 samples per resource across ten resources in twelve seconds.
Stream failure and queue overflow reject collection instead of silently losing
samples. This change still needs the remaining mechanics pilots.

## Work still required

Five mechanics pilots per new scenario and healthy control have passed.
On resuming October 15, the prior owner had exited. A later live-demo
recovery marker remained. Its recorded owner was also dead, and the demo's own
recovery procedure verified identities, restored its transaction and confirmed
healthy workloads before clearing its gate. This chat then resumed the pilots.
All thirty have been accepted and their receipt checksums verified.

Separate retained-mechanism checks also passed through the v3 controller.
Carts memory pressure reached a median limit fraction of 0.8359375 with full
coverage and recovery confirmation. The orders-payment path check verified the
declared 800 ms delay and 60 forwarded requests, followed by recovery.
These engineering receipts are excluded from the primary cohort. Their compact
proof is `experiments/v3/retained-mechanics-proof.json`.

The checksum audit verified all 64,308 preserved v1/v2 artifact files unchanged.
The archived v1 and v2 fixture workflows also passed under their preserved
runtime hash and the unchanged core lock. No old reserved comparison was rerun.

Primary collection is running for the fixed 440-incident cohort. Physical model
fitting, the diagnostic gate and training/comparisons remain pending.
The robustness runner, phase orchestration,
completion audit and benchmark report writer are implemented. Their physical
inputs depend on completing the fresh cohort.
No physical-cohort DQN or v3 SARSA training has started. No v3 accuracy or
promotion claim has been made. The final software is frozen at runtime hash
`b8592543e12d03e0fd7931e3ea21205c1ca44b65c26446047473cb73fad9455f`
in `experiments/v3/final-runtime-source.json`. The current-source verification
proof is `experiments/v3/software-validation.json`. The controller refuses
changed source once accepted primary slots exist.

The durable pilot state is `runs/adaptive_v3_multi/capture_pilot/schedule.json`.
The shared lab ownership and recovery gate remain in `runs/adaptive_v2/lab` so
old and new controllers cannot inject concurrently. Do not remove those files
because their timestamps look old. The phase status is in
`runs/adaptive_v3_multi/phase-status.json`.

## Commands currently available

```powershell
.\.venv\Scripts\python.exe -m rca_investigation.cli validate-profile --config configs/investigation_v3_multi.yaml
.\.venv\Scripts\python.exe -m rca_investigation.cli validate-multicause-fixtures
.\experiments\v3\neural\.venv\Scripts\python.exe -m pytest -q tests/test_dqn_v3.py --basetemp .pytest-v3-neural
.\experiments\v3\neural\.venv\Scripts\python.exe scripts/validate_v3_learners.py
.\.venv\Scripts\python.exe scripts/run_v3_pilots.py --declare-software-revision
.\experiments\v3\neural\.venv\Scripts\python.exe scripts/validate_v3_software.py
.\.venv\Scripts\python.exe scripts/check_v3_legacy_compatibility.py
.\.venv\Scripts\python.exe scripts/run_v3_phases.py --dry-run
.\.venv\Scripts\python.exe scripts/run_v3_phases.py
```

Run the pilot command only when the shared lab lease is available. Engineering
revisions preserve accepted pilot receipts and the preceding manifests. Profile
commands use explicit v3 collection/fitting implementations and reject remaining
unsupported v3 operations without dispatching them to a v2 controller.
