# GCP live demo handoff

Updated 20 October 2026. Clone this repository and run commands from its root.
The V3 adapter is implemented. Do not run
the legacy `pipeline/monitor.py`, chat or injector alongside it.

This guide deploys frozen inference artifacts. It does not resume training or
benchmark collection. Deployment and rehearsal on the team's GCP VM remain
unverified until the team completes them.

## 1. Transfer the artifacts separately from Git

Git contains source, configuration, dependency locks, benchmark reports and
verification records. Transfer these complete directories from the owner
laptop into the matching VM application paths:

| Directory | Required contents |
| --- | --- |
| `runs/live_v3_export/` | Encoder, diagnostic model, all selected policy checkpoints, resolved configuration, frozen identities, checksums and bounded helper binary |
| `runs/live_v3_images/` | `images.tar`, exact-image `compose.json` and `compose-images.json` with the archive checksum |

Both exports already exist locally and are ignored by Git. Do not export again
over them. To check the existing bundle on the owner laptop:

```powershell
Set-Location 'C:\path\to\rca-project'
$py = '.\experiments\v3\neural\.venv\Scripts\python.exe'
& $py scripts/live_v3.py check --bundle runs/live_v3_export
```

If a new export is needed, choose new output directories:

```powershell
& $py scripts/live_v3.py export --bundle runs/live_v3_export_new
& $py scripts/export_live_v3_images.py --save --output runs/live_v3_images_new
```

Use an authenticated transfer channel. Do not copy a Windows virtual
environment, laptop Neo4j data, deployment registry or recovery journals onto
the VM. Provision the team's API key separately on the VM.
The older DQN-only transfer inventory is benchmark provenance. The complete
adapter bundle above also includes the selected SARSA and hybrid policies.

## 2. Prepare the VM and checkout

Use x86-64 Linux with Git, Python 3.12, `uv`, Docker Engine and its Compose
plugin. An engineering starting allocation is 8 vCPUs, 32 GiB RAM and 100 GiB
disk. This is an estimate, not a measured minimum. Keep trained container CPU
and memory limits unchanged even when the VM has extra capacity.

Restrict GCP ingress to the team's SSH access and use forwarding for browser
ports. Review Docker port bindings and the GCP firewall together. This lab
includes default credentials and is not a public-facing deployment.

Clone using your team's GitHub authentication:

```bash
git clone https://github.com/the-jellyone/graph-rca.git rca-project
cd rca-project
export RCA_DEMO_ROOT="$PWD"
mkdir -p runs/live_v3_vm
git rev-parse HEAD > runs/live_v3_vm/source-commit.txt

uv sync --locked --project experiments/v3/neural
export PY="$PWD/experiments/v3/neural/.venv/bin/python"
"$PY" --version
docker version > runs/live_v3_vm/docker-version.txt
```

For an existing checkout, preserve local changes before updating. Do not reset
another agent's work. The development branch was `dqn`; the published handoff
is on `main`. Do not initialize another Git repository inside the application.

Use the pinned neural interpreter for all adapter commands. Do not install
arbitrary newer Torch or NumPy versions to bypass checks. Linux installation
and inference still need verification on the VM.

After transferring both export directories into `runs/`:

```bash
cd "$RCA_DEMO_ROOT"
"$PY" scripts/live_v3.py check --bundle runs/live_v3_export
```

Require `passed=true`. This validates the source, bundle, models and selected
policies without training or opening reserved incident responses.

## 3. Verify images and create the isolated lab

Check the archive against its transfer metadata before loading:

```bash
"$PY" - <<'PY'
import hashlib, json
from pathlib import Path
root = Path('runs/live_v3_images')
metadata = json.loads((root / 'compose-images.json').read_text())
with (root / 'images.tar').open('rb') as stream:
    actual = hashlib.file_digest(stream, 'sha256').hexdigest()
assert actual == metadata['archive_sha256'], 'image archive checksum mismatch'
print('Image archive checksum verified.')
PY

docker load -i runs/live_v3_images/images.tar
"$PY" scripts/prepare_live_v3_compose.py --input runs/live_v3_images
mkdir -p neo4j/data
chmod 0755 runs/live_v3_export/helper
docker compose -p rca-lab -f runs/live_v3/compose-vm.json config
```

The relocation script maps owner application binds to VM paths, preserves the
declared Docker socket and log mounts, and rejects other external paths.
Review the output, image IDs, bind mounts and ports. Ensure Neo4j's container
user can write `neo4j/data`. Do not delete a database to fix permissions.

The exported compose includes the payment path proxy and helper-enabled
images. The basic legacy compose alone cannot support all eleven scenarios.
Do not silently rebuild images, replace them with `latest`, or treat owner
container IDs as VM repair targets.

On a new isolated VM lab:

```bash
docker compose -p rca-lab -f runs/live_v3/compose-vm.json up -d
docker compose -p rca-lab -f runs/live_v3/compose-vm.json ps -a
```

For an existing matching lab, inspect pending recovery and identities first,
then prefer `start` for stopped containers. Do not recreate containers while
a registry or transaction refers to the old IDs. Never use `down -v` or volume
pruning to recover this demo.

After warm-up, check endpoints and actual shop operations:

```bash
curl -fsS http://localhost:80/ > /dev/null
curl -fsS http://localhost:9090/-/ready
curl -fsS http://localhost:3100/ready
curl -fsS http://localhost:7474/ > /dev/null
```

Browse products, log in, use a cart and complete checkout. A healthy homepage
is insufficient. The controller also checks fresh auth, semantic checkout and
shipping availability before injection and after recovery. Shipping health
establishes availability, not fulfilment.

Create the VM's own registry once the full lab is running:

```bash
"$PY" scripts/live_v3.py inventory --bundle runs/live_v3_export
```

This creates `runs/live_v3/deployment.json` with VM container/image IDs, verifies
CPU and memory limits and requires zero proxy delay. It refuses to overwrite
an existing registry and does not modify the frozen training registry.

## 4. Open the laptop browser tunnel

Open one SSH session with forwarding:

```bash
gcloud compute ssh <INSTANCE> --project <PROJECT> --zone <ZONE> -- \
  -L 8080:127.0.0.1:80 \
  -L 7474:127.0.0.1:7474 \
  -L 7687:127.0.0.1:7687 \
  -L 9090:127.0.0.1:9090
```

Use two more SSH sessions without duplicate forwarding. Open the shop at
`http://localhost:8080`, Neo4j at `http://localhost:7474` and Prometheus at
`http://localhost:9090`. Neo4j Browser connects to `bolt://localhost:7687`.
The isolated defaults are `neo4j` and `password123`. Keep access private.
Configure any credential changes consistently in the database and adapter.
The adapter reads `NEO4J_URI`, `NEO4J_USER` and `NEO4J_PASSWORD` from its shell.

## 5. Run the three terminals

In each remote terminal, replace the application path:

```bash
cd /absolute/path/rca-project
export PY="$PWD/experiments/v3/neural/.venv/bin/python"
export DEMO_OUTPUT="runs/live_v3_vm"
```

Terminal 1 owns collection, graph updates, policy decisions and recovery:

```bash
"$PY" scripts/live_v3.py serve --collection early --output "$DEMO_OUTPUT"
```

Terminal 2 displays events and timing in natural language. For external
narration, provision untracked `pipeline/.env` with `LLM_PROVIDER=groq`,
`GROQ_API_KEY`, `GROQ_MODEL` and `LIVE_NARRATION_MODE=external`. Use a model
available to the team's account. The LLM receives diagnosis, confidence,
repair/cleanup/health outcomes and numeric timings. It cannot execute tools.

```bash
"$PY" scripts/live_v3.py chat --env-file pipeline/.env --output "$DEMO_OUTPUT"
# Or local narration, with no provider call:
# "$PY" scripts/live_v3.py chat --local-narration --output "$DEMO_OUTPUT"
```

Terminal 3 selects a policy and queues one owned incident:

```bash
"$PY" scripts/live_v3.py inject --output "$DEMO_OUTPUT"
```

Start with `healthy` and wait for recovery to complete. Early mode may defer
the healthy classification; functional health checks are reported separately.
Then try the locally rehearsed simultaneous case:

```text
policy dqn
catalogue_db_payment_cpu
```

Wait for verified recovery before another incident. Other policy names are
`dp`, `voi`, `expected-sarsa`, `two-step-sarsa`, `expected-sarsa-hybrid`,
`two-step-sarsa-hybrid`, `random-valid` and `expanded-rule`.

| Scenario input | Injected condition |
| --- | --- |
| `healthy` | No fault |
| `catalogue_db_stop` | Catalogue database stopped |
| `orders_stop` | Orders service stopped |
| `payment_cpu` | Bounded payment CPU contention |
| `orders_payment_delay` | 800 ms orders-to-payment delay |
| `carts_memory` | Bounded carts memory pressure |
| `payment_stop` | Payment service stopped |
| `orders_db_stop` | Orders database stopped |
| `user_stop` | User/auth service stopped |
| `shipping_stop` | Shipping service stopped |
| `catalogue_db_payment_cpu` | Catalogue database stopped and payment CPU contention |

Inspect the incident-scoped graph in Neo4j Browser:

```cypher
MATCH (a:LiveV3Resource)-[r:CALLS]->(b:LiveV3Resource) RETURN a,r,b;
MATCH (n:LiveV3Resource) WHERE n.anomaly_flag=true RETURN n;
```

The graph updates during collection. Terminal 2 is a narration viewer, not an
interactive repair shell. Policy and scenario commands belong in Terminal 3.
Run exactly one controller and no legacy monitor or benchmark collector.

## 6. Collection modes and latency

Early mode uses `live_v3_rolling_early_r2`. It starts a rolling stream before
injection and has no fixed settling delay. Only post-boundary observations
and requests enter diagnosis. It waits for real sample support and buys graph
evidence under one ten-credit, five-probe budget, including paid refreshes.

Early diagnosis requires at least fifteen seconds of observation, 90% model
confidence, fresh paid support for every cause, and no ignored direct fault
alert. The wrapper requires confirmation of visible graph anomalies before
accepting a terminal proposal. This is a declared live constraint on the
unchanged policy, not part of the original benchmark action mask.

Fifteen seconds is not a response-time guarantee. Request probes need twenty
actual samples per operation. Normal memory needs a measured ninety-second
span. Uncertain cases can defer at the 120-second observation deadline;
inference, collector shutdown and recovery add time afterward. Short-window
log ingestion is not certified, so early-mode log probes remain unavailable.

Terminal 1 prints overall latency markers. Terminal 2 states elapsed time in
plain language. `overall_latency_seconds` runs from injection request through
verified recovery and graph cleanup. Diagnosis and repair-command timings use
the same origin. Initial healthy preparation and external narration time are
excluded. `controller_total_seconds` also includes initial preparation.
Injector cleanup restoring health is distinct from an agent-directed repair.

To retain the original protocol, stop at a safe boundary and restart Terminal 1:

```bash
"$PY" scripts/live_v3.py serve --collection windowed --output "$DEMO_OUTPUT"
```

Windowed mode settles for thirty seconds, observes for 120 seconds and allows
ten seconds for logs. The historical eleven-case verification describes that
mode. Early mode changes evidence timing and readiness masks, so its engineering
results are not a new calibrated accuracy benchmark.

## 7. Recovery and shutdown

Repair requires confidence, purchased mechanism evidence and an exact match
to the injector-owned cause set. The controller starts exact stopped
containers, releases only its leased helpers or resets its owned proxy delay.
Private ownership records constrain repair and never enter policy observations.
Deferred, incorrect or unsupported diagnoses trigger injector cleanup.

Recovery requires five consecutive successful auth, semantic checkout and
shipping-health groups, running resources, zero proxy delay and memory release
where relevant. A DEFER followed by successful cleanup is not an agent repair.

Enter `stop` in Terminal 3. Terminal 1 exits after a safe incident boundary.
Close Terminal 2 with Ctrl+C. If a controller was interrupted, first verify its
PID exited, then use the same bundle, registry and output directory:

```bash
"$PY" scripts/live_v3.py recover --output "$DEMO_OUTPUT"
```

Do not delete live locks or recovery journals. Failed recovery stays blocked.
Preserve `runs/live_v3_vm/`, source commit, registry and compose metadata.
After recovery and controller exit, stop the existing lab if desired:

```bash
docker compose -p rca-lab -f runs/live_v3/compose-vm.json stop
```

Then stop the VM through the team's normal process without deleting data.

## 8. Verification and presentation limits

Local windowed verification covered all eleven scenarios with DQN and 99
all-policy shadow investigations. Final early-mode checks include DQN and DP
diagnosing both simultaneous causes in 20.7 seconds, with verified recovery
in 28.0 and 29.3 seconds respectively. VOI deferred catalogue DB stop. The
preliminary healthy early check also deferred and applied no repair.
Seventy-six focused tests passed, with 63 saved-prefix checks across all nine
policies. Those replays use virtual snapshot time and are not live latency
measurements or physical repair trials for every policy.

Read `docs/live_v3_verification.md`, `docs/live_v3_early_verification.md` and
their JSON companions. The complete eleven-scenario physical sweep has not
been repeated in early mode. GCP readiness needs an actual VM rehearsal with
its commit, locked environment, exact images, new registry and verified
recovery. This handoff does not certify a remote deployment.

The separate primary benchmark's 440 physical captures, training and reserved
comparisons are recorded in `docs/v3_primary_benchmark_2026-10-20.md`.
Multi-cause robustness remains incomplete. Do not run `run_v3_phases.py`,
refit an encoder, retrain, reopen reserved responses or loosen thresholds for
the presentation. Resolve deployment failures explicitly rather than bypassing
identity checks or claiming equivalent classifier accuracy on an untested VM.
