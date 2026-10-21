# V3 live policy and repair demo

This adapter runs fresh Docker incidents through the frozen v3 cause-set model
and a selected evidence policy. It supports all eleven declared scenarios.
It does not train, refit the encoder, open reserved incident responses or alter
benchmark reports. Run it from the repository root on the published `main` branch.

## What this mode measures

The default CLI collection mode is now `early`, protocol
`live_v3_rolling_early_r2`. It ports V2's progressive investigation to V3's
cause-set model and operation probes. A rolling stream starts before injection.
Only post-injection measurements and requests that started after the evidence
boundary enter the investigation. Collection continues while the policy buys
ready evidence from Neo4j. There is no fixed 30-second settling delay.

Early decisions require at least 15 seconds of observation, 90% confidence,
fresh purchased evidence and direct support for every predicted cause. A
single-cause verdict cannot ignore another direct fault alert in the current
graph. CPU evidence needs at least ten seconds, memory pressure fifteen, and
normal memory a measured span of ninety seconds. Request tools still need
twenty actual operation samples, independently for auth, checkout and shipping.
Healthy classification waits at least ninety seconds. Missing evidence is not
normal evidence. Logs remain unavailable in this first early protocol because
their ingestion delay is not certified for short windows.

One ledger spans all updates, with ten credits and five total acquisitions.
Refreshing stale evidence costs another probe and its original price. The
model sees the latest purchased symbol for each tool. Evidence readiness masks
which actions the unchanged policy can choose. V2's graph guidance also requires
purchasing confirmation of visible anomalies before accepting a terminal choice.
Stopped resources take priority, followed by CPU or memory alerts. The policy
ranks these candidates. If DP proposes a terminal action internally, the wrapper
selects its highest-valued required confirmation probe. This declared wrapper is
part of the live mode, not part of the frozen benchmark policy.
The live controller permits a supported confident diagnosis or waits;
unsupported terminal proposals cannot
trigger repairs. At the 120-second observation deadline it defers if support
is insufficient. Inference, collector shutdown and recovery can add time beyond
that observation limit. The injector always cleans up its own remaining faults.

Use `--collection windowed` on `serve` or `run` to retain the original protocol
`live_v3_window120_r1`: thirty seconds settling, 120 seconds observation and ten
seconds log ingestion. The original eleven-case verification script explicitly
uses this mode, and its reports remain historical windowed results.

Both modes now record `diagnosis_seconds`, `repair_command_completed_seconds`
when applicable, and `overall_latency_seconds`. Their clock starts immediately
before requesting injection and ends after recovery verification and graph
cleanup. `controller_total_seconds` also includes initial healthy preparation.
Terminal 1 prints an overall latency marker during investigation and at each
milestone. Terminal 2 states elapsed time in plain language, including the
final overall duration. External narration also receives these numeric timings.
Historical receipts retain their original after-injection timing convention.

The early protocol reuses frozen weights and encoder boundaries without
retraining. Short-window inputs and readiness masks differ from the training
distribution. Its diagnostic gate is a safety condition, not proof of calibrated
90% correctness on short windows. Treat these runs as engineering checks,
not a new accuracy benchmark or a guarantee of fifteen-second diagnosis.

The shared lab lease and graph-writer lease prevent overlap with benchmark
collection or the legacy monitor. The adapter keeps its own `LiveV3Resource`
and `LiveV3Evidence` graph namespace and reuses the existing GraphRAG anomaly
retrieval and dependency traversal. It reads the topology back from Neo4j and
checks it against the frozen topology. The policy receives the original public
observation and paid evidence. The windowed mode retains its original action
mask. Early mode uses the declared readiness and graph-confirmation constraints.

## Export the trained artifacts on the owner laptop

Use the pinned neural interpreter. Windows example:

```powershell
Set-Location 'C:\path\to\rca-project'
$py = '.\experiments\v3\neural\.venv\Scripts\python.exe'
& $py scripts/live_v3.py export --bundle runs/live_v3_export
& $py scripts/live_v3.py check --bundle runs/live_v3_export
```

Export refuses an existing destination. Keep old bundles intact and choose a
new directory for a deliberately changed export. The bundle contains resolved
configuration, the training-fitted encoder and model, validation-selected DQN
and SARSA representatives, hybrid checkpoints, frozen provenance, source and
dependency checksums, and the bounded helper binary. DP, VOI, random selection
and the expanded rule baseline need no trained policy weights.

Transfer the whole `runs/live_v3_export/` directory separately from Git. It is
ignored. Copying the repository alone does not transfer the model or weights.
Do not copy a Windows virtual environment. The adapter loads unchanged weights
on CPU, checks the DQN schema and records its own relocation identity. Original
absolute checkpoint paths remain preserved in `frozen.json`.

## GCP setup

Use an x86-64 Linux VM and the same service topology and resource limits. Follow
the VM, SSH tunnels and volume-preservation guidance in `GCP_LIVE_DEMO_HANDOFF.md`.
For this adapter, use the pinned neural environment instead of `.venv-demo`:

```bash
cd /absolute/path/rca-project
uv sync --locked --project experiments/v3/neural
PY="$PWD/experiments/v3/neural/.venv/bin/python"
"$PY" scripts/live_v3.py check --bundle runs/live_v3_export
```

Do not upgrade torch or numpy to get past an inference check. Resolve a failed
locked installation explicitly. Record Linux package versions. The adapter
compares the frozen Python source as text using canonical original path names,
so the move to Linux does not require rewriting benchmark hashes.

The full lab also needs the leased CPU/memory helpers and the payment path
proxy. The older basic compose launch alone does not provide them. Transfer
the owner lab's exact image archive using `scripts/export_live_v3_images.py`
and load it with `docker load -i <archive>`. Use the resolved compose file that
script exports, with its recorded owner bind mounts relocated to the VM.
Review `compose-images.json` and verify image IDs.
Never use `latest` as a silent replacement.

Owner-side image export and VM preparation:

```bash
# Owner host, after the local rehearsal has finished
python scripts/export_live_v3_images.py --save --output runs/live_v3_images
# Transfer runs/live_v3_images/ and runs/live_v3_export/ to the VM.

# On the VM, from its application directory
docker load -i runs/live_v3_images/images.tar
"$PY" scripts/prepare_live_v3_compose.py --input runs/live_v3_images
docker compose -p rca-lab -f runs/live_v3/compose-vm.json config
mkdir -p neo4j/data
chmod 0755 runs/live_v3_export/helper
# Verify Neo4j's container user can write neo4j/data before starting.
docker compose -p rca-lab -f runs/live_v3/compose-vm.json up -d
```

The relocation script preserves the two declared Docker host mounts and refuses
other paths outside the owner's application root. It does not copy laptop
Neo4j data or launch containers. Review port bindings and firewall rules before
running `up`. Use `start` for a previously created matching lab instead.

After the complete lab is running, create a separate deployment registry:

```bash
"$PY" scripts/live_v3.py inventory --bundle runs/live_v3_export
```

This records new container IDs and images and checks CPU/memory limits against
the training registry. It requires the proxy to exist with zero delay. It does
not overwrite `configs/resources_sockshop_v3.yaml`. Registry creation alone
does not establish equivalent classifier accuracy on another host.

## Three terminals

In each remote terminal, set `PY` and change to the same application directory.
Run exactly one controller and no legacy `monitor.py` or `collect.py` writer.

```bash
# Terminal 1: collection, policy decisions, owned repair and recovery checks
"$PY" scripts/live_v3.py serve --collection early

# Terminal 2: readable events and optional narration
"$PY" scripts/live_v3.py chat --env-file pipeline/.env

# Terminal 3: interactive policy and scenario selection
"$PY" scripts/live_v3.py inject
```

The same commands work with `$py` on Windows. Use `--local-narration` in
Terminal 2 to avoid external calls. Groq narration uses the existing local
`.env` settings and receives only diagnosis, confidence and controller outcome
fields and numeric timings. It receives no raw logs, private scenario labels
or executable tools.
Configure Neo4j with `NEO4J_URI`, `NEO4J_USER` and `NEO4J_PASSWORD` in the shell.
The original local defaults are retained for the isolated lab.

Terminal 3 accepts `policy dqn`, `policy dp`, `policy voi`,
`policy expected-sarsa`, `policy two-step-sarsa`, either `-hybrid` variant,
`policy random-valid` or `policy expanded-rule`. The default is `dqn`.
Representatives come from validation, never from selecting the best test seed.

Choose one of these scenarios:

| Input | Condition |
| --- | --- |
| `healthy` | No injected fault |
| `catalogue_db_stop` | Catalogue database stopped |
| `orders_stop` | Orders service stopped |
| `payment_cpu` | Bounded payment CPU contention |
| `orders_payment_delay` | 800 ms delay on the orders-to-payment path |
| `carts_memory` | Bounded retained memory pressure |
| `payment_stop` | Payment service stopped |
| `orders_db_stop` | Orders database stopped |
| `user_stop` | User/authentication service stopped |
| `shipping_stop` | Shipping service stopped |
| `catalogue_db_payment_cpu` | Catalogue database stop plus payment CPU contention |

Wait for confirmed recovery before queueing another incident. `stop` exits
Terminal 1 at a safe incident boundary. Closing Terminal 2 only closes the
viewer. Its API provider cannot execute actions.

In Neo4j Browser, use the v3 namespace so the original graph and earlier v2
demo nodes do not appear in the same view:

```cypher
MATCH (a:LiveV3Resource)-[r:CALLS]->(b:LiveV3Resource) RETURN a,r,b;
MATCH (n:LiveV3Resource) WHERE n.anomaly_flag=true RETURN n;
```

Run `healthy` first to establish a fresh graph and baseline before presenting
a fault. Terminal 2 is a narration viewer; policy and injection commands belong
in Terminal 3. The adapter does not ask the LLM to select shell commands.

To run a single case directly, with no other controller active:

```bash
"$PY" scripts/live_v3.py run --case user_stop --policy dqn
```

## Repair and interruption

Repair requires a diagnosis at 90% model confidence, an exact match to the
injector-owned cause set and purchased evidence supporting each mechanism.
Stopping and CPU contention share a payment root, but require different paid
support before selecting their recorded recovery operation. For the pair,
both roots and both supporting probes are required. Private ownership records
are repair constraints, never policy inputs.

The controller starts exact stopped containers, terminates only leased helper
processes, or resets the owned proxy delay. A wrong, partial or deferred
diagnosis triggers injector cleanup and is not reported as agent repair.
The controller checks five consecutive groups of fresh auth, semantic checkout
and shipping-health operations, running containers, zero proxy delay and memory
release where applicable. Shipping establishes availability, not fulfilment.

An interruption leaves the transaction in `runs/adaptive_v2/lab/blocked-lab.json`.
After confirming the controller has exited, use the same bundle and registry:

```bash
"$PY" scripts/live_v3.py recover
```

Recovery refuses a potentially live lock owner and preserves a failed recovery
gate. Do not delete journals or recreate containers to bypass it.

## Verification records

`scripts/verify_live_v3.py` runs each scenario once with live DQN. After recovery,
it evaluates all nine policy options against that fresh public window. Those
additional evaluations are shadow replay, not nine independent physical
experiments or nine verified live repairs. Each policy can also be selected
individually in the terminal for a fresh physical run.

Receipts, original measurements, purchased responses, model identities, source
snapshots and recovery checks stay under `runs/live_v3*`, outside benchmark
datasets. Accuracy failures remain visible even when cleanup succeeds. GCP
readiness requires a rehearsal on the actual VM; local checks cannot certify it.
