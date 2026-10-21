"""Small file-queue bridge to the repository's existing V3 controller."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4


class LiveV3Bridge:
    def __init__(self, root: Path, bundle: Path, registry: Path, output: Path):
        self.root = root.resolve()
        self.bundle = bundle.resolve()
        self.registry = registry.resolve()
        self.output = output.resolve()
        self.process: subprocess.Popen | None = None

    def _policy_rows(self, python: Path) -> tuple[list[dict], str | None]:
        names=("dqn","dp","voi","expected-sarsa","two-step-sarsa","expected-sarsa-hybrid","two-step-sarsa-hybrid","random-valid","expanded-rule")
        labels={"dqn":"DQN","dp":"Dynamic programming","voi":"Value of information","expected-sarsa":"Expected SARSA","two-step-sarsa":"Two-step SARSA","expected-sarsa-hybrid":"Expected SARSA + planner","two-step-sarsa-hybrid":"Two-step SARSA + planner","random-valid":"Random valid baseline","expanded-rule":"Expanded rule baseline"}
        if not self.bundle.is_dir():
            reason=f"Frozen inference bundle is missing: {self.bundle}"
            return [{"id":n,"label":labels[n],"available":False,"reason":reason} for n in names],reason
        probe=self.root/"scripts/probe_live_v3.py"
        result=subprocess.run([str(python),str(probe),"--bundle",str(self.bundle)],cwd=self.root,
            env={**os.environ,"PYTHONPATH":str(self.root/"src")},capture_output=True,text=True,timeout=30)
        try:
            data=json.loads(result.stdout)
            if data.get("available"):
                return data["policies"],None
            reason=data.get("error") or result.stderr[-500:] or "Inference bundle check failed"
        except Exception:
            reason=result.stderr[-500:] or result.stdout[-500:] or f"Policy probe exited {result.returncode}"
        return [{"id":n,"label":labels[n],"available":False,"reason":reason} for n in names],reason

    def policy_inventory(self) -> list[dict]:
        python=Path(os.getenv("GRAPH_RCA_CONTROLLER_PYTHON",sys.executable)).expanduser()
        if not python.is_absolute(): python=(self.root/python).resolve()
        if not python.is_file():
            return self._policy_rows(python)[0]
        try:
            version=subprocess.run([str(python),"-c","import sys; print('%d.%d' % sys.version_info[:2])"],capture_output=True,text=True,timeout=5,check=True).stdout.strip()
            if version!="3.12": return self._policy_rows(python)[0]
            return self._policy_rows(python)[0]
        except Exception:
            return self._policy_rows(python)[0]

    def status(self) -> dict:
        script = self.root / "scripts/live_v3.py"
        python = Path(os.getenv("GRAPH_RCA_CONTROLLER_PYTHON", sys.executable)).expanduser()
        if not python.is_absolute(): python=(self.root/python).resolve()
        blockers=[]
        if not script.is_file(): blockers.append(f"V3 controller script missing: {script}")
        policies=[]
        if not python.is_file():
            blockers.append(f"Controller Python not found: {python}")
        else:
            try:
                result=subprocess.run([str(python),"-c","import sys; print('%d.%d' % sys.version_info[:2])"],capture_output=True,text=True,timeout=5,check=True)
                if result.stdout.strip()!="3.12": blockers.append(f"Controller requires Python 3.12; configured interpreter is {result.stdout.strip()}")
                else:
                    for module in ("gymnasium","neo4j","yaml","numpy"):
                        check=subprocess.run([str(python),"-c",f"import {module}"],capture_output=True,timeout=5)
                        if check.returncode: blockers.append(f"Controller dependency missing from {python}: {module}")
                    if not any("dependency missing" in item for item in blockers):
                        policies,probe_error=self._policy_rows(python)
                        if probe_error: blockers.append(f"Inference bundle check failed: {probe_error}")
                        elif not any(row["available"] for row in policies): blockers.append("No inference policy is available in the selected bundle")
            except Exception as exc:
                blockers.append(f"Could not inspect controller Python: {type(exc).__name__}: {exc}")
        if not self.bundle.is_dir(): blockers.append(f"Frozen inference bundle missing: {self.bundle}")
        if not self.registry.is_file(): blockers.append(f"Local rca-lab deployment registry missing: {self.registry}")
        else:
            try:
                value=json.loads(self.registry.read_text())
                if value.get("project")!="rca-lab": blockers.append("Deployment registry is not for the isolated rca-lab project")
            except Exception as exc: blockers.append(f"Deployment registry is invalid: {type(exc).__name__}: {exc}")
        lock=self.output/"server.lock";external_running=False
        if not self.process and lock.is_file():
            try:
                owner=json.loads(lock.read_text()).get("pid");os.kill(int(owner),0);external_running=True
            except ProcessLookupError: blockers.append("Stale V3 controller lock detected; follow scripts/live_v3.py recover guidance after confirming no controller is running")
            except (OSError,TypeError,ValueError,json.JSONDecodeError): blockers.append("V3 controller lock exists but its owner cannot be verified")
        if self.process and self.process.poll() is not None: blockers.append(f"V3 controller exited with status {self.process.returncode}")
        return {"available":not blockers,"running":bool((self.process and self.process.poll() is None) or external_running),"blockers":blockers,
            "bundle":str(self.bundle),"registry":str(self.registry),"controller":str(script),"python":str(python),"policies":policies}

    def start(self) -> dict:
        status = self.status()
        if not status["available"]:
            return status
        if status["running"]:
            return status
        python = status["python"]
        script = self.root / "scripts/live_v3.py"
        self.output.mkdir(parents=True, exist_ok=True)
        (self.output / "controller.log").parent.mkdir(parents=True, exist_ok=True)
        log = (self.output / "controller.log").open("ab", buffering=0)
        self.process = subprocess.Popen(
            [python, str(script), "serve", "--root", str(self.root), "--bundle", str(self.bundle),
             "--registry", str(self.registry), "--output", str(self.output), "--collection", "early"],
            cwd=self.root, stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONPATH": str(self.root / "src")},
            start_new_session=True)
        deadline=time.monotonic()+5
        lock=self.output/"server.lock"
        while time.monotonic()<deadline and self.process.poll() is None and not lock.exists():
            time.sleep(.05)
        status=self.status()
        if self.process.poll() is not None:
            try:
                tail=(self.output/"controller.log").read_text(errors="replace")[-1000:]
            except OSError:
                tail=""
            status["available"]=False
            status["blockers"].append("V3 controller failed during startup"+(": "+tail if tail else ""))
        elif not lock.exists():
            status["available"]=False
            status["blockers"].append("V3 controller did not acquire its single-owner server lock")
        return status

    def enqueue(self, command: dict) -> str:
        self.output.joinpath("pending").mkdir(parents=True, exist_ok=True)
        identifier = str(uuid4())
        payload = {"id": identifier, **command}
        target = self.output / "pending" / f"{identifier}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, allow_nan=False))
        os.replace(temporary, target)
        return identifier

    def events(self) -> list[dict]:
        path = self.output / "events.jsonl"
        if not path.is_file():
            return []
        rows = []
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    break
        return rows
