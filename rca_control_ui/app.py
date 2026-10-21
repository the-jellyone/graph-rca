"""
Graph-RCA Unified Control Plane & API Server
FastAPI + WebSockets + Docker Fault Injector + Neo4j Topology + RL Investigator + Gemini 3.5 Flash Lite Chat
"""

import os
import sys
import time
import json
import asyncio
import subprocess
import sqlite3
import re
from uuid import uuid4
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

import requests

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
import uvicorn

# ---------------------------------------------------------------------------
# Configuration & Environment
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent.resolve()
STATIC_DIR = BASE_DIR / "static"
PROJECT_ROOT = BASE_DIR.parent if (BASE_DIR.parent / "src" / "rca_live_v3").is_dir() else BASE_DIR

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
LOKI_URL = os.getenv("LOKI_URL", "http://localhost:3100")
SOCKSHOP_URL = os.getenv("SOCKSHOP_URL", "http://front-end" if Path("/.dockerenv").exists() else "http://localhost")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
POLICY_BUNDLE = Path(os.getenv("GRAPH_RCA_POLICY_BUNDLE", str(PROJECT_ROOT / "runs/live_v3_export")))
LIVE_REGISTRY = Path(os.getenv("GRAPH_RCA_LIVE_REGISTRY", str(PROJECT_ROOT / "runs/live_v3/deployment.json")))
LIVE_OUTPUT = Path(os.getenv("GRAPH_RCA_LIVE_OUTPUT", str(PROJECT_ROOT / "runs/live_v3")))
DOCKER_COMPOSE_PROJECT = os.getenv("GRAPH_RCA_COMPOSE_PROJECT", "rca-project")
DATABASE_PATH = BASE_DIR / "data" / "console.sqlite3"
POLICY_NAMES = ("dqn", "dp", "voi", "expected-sarsa", "two-step-sarsa", "expected-sarsa-hybrid", "two-step-sarsa-hybrid", "random-valid", "expanded-rule")

# Use the V3 controller's actual scenario IDs; the UI has no independent injectors.
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from rca_investigation.schedule_v3 import SCENARIOS as LIVE_SCENARIOS
_SCENARIO_LABELS = {
    "healthy": ("Healthy baseline", "Baseline", "front-end"),
    "catalogue_db_stop": ("Catalogue database unavailable", "Dependency failure", "catalogue-db"),
    "orders_stop": ("Orders API unavailable", "Service failure", "orders"),
    "payment_cpu": ("Payment CPU pressure", "Resource pressure", "payment"),
    "orders_payment_delay": ("Orders to payment delay", "Network degradation", "payment"),
    "carts_memory": ("Carts memory pressure", "Resource pressure", "carts"),
    "payment_stop": ("Payment service unavailable", "Service failure", "payment"),
    "orders_db_stop": ("Orders database unavailable", "Dependency failure", "orders-db"),
    "user_stop": ("User service unavailable", "Service failure", "user"),
    "shipping_stop": ("Shipping service unavailable", "Service failure", "shipping"),
    "catalogue_db_payment_cpu": ("Catalogue database + payment CPU", "Compound failure", "catalogue-db, payment"),
}
FAULT_CATALOGUE = []
for scenario_id in LIVE_SCENARIOS:
    name, group, target = _SCENARIO_LABELS[scenario_id]
    description = ("Stops the verified local container; recover restarts that same container. This run does not execute an RL policy."
                   if scenario_id in ("catalogue_db_stop", "orders_stop") else
                   "Runs the bounded V3 scenario; cleanup and recovery checks stay under controller control.")
    FAULT_CATALOGUE.append({"id": scenario_id, "name": name, "group": group, "target": target,
        "impact_path": [], "description": description,
        "mechanism": "controller", "resource": target})
FAULT_BY_ID = {item["id"]: item for item in FAULT_CATALOGUE}
_BASIC_STOP_TARGETS = {"catalogue_db_stop": "catalogue-db", "orders_stop": "orders"}
HELPER_BINARY = PROJECT_ROOT / "lab/adaptive-helper/rca-helper-lease"
from live_v3_bridge import LiveV3Bridge
live_controller = LiveV3Bridge(PROJECT_ROOT, POLICY_BUNDLE, LIVE_REGISTRY, LIVE_OUTPUT)

# Check sibling or parent .env for keys
for env_path in [
    BASE_DIR / ".env",
    BASE_DIR.parent / ".env",
    PROJECT_ROOT / "pipeline" / ".env",
]:
    if env_path.exists():
        try:
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip("'\"")
                        if k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", GEMINI_API_KEY)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
DOCKER_COMPOSE_PROJECT = os.getenv("GRAPH_RCA_COMPOSE_PROJECT", DOCKER_COMPOSE_PROJECT)

app = FastAPI(title="Graph-RCA Incident Command Control Plane")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ---------------------------------------------------------------------------
# Canonical Sock Shop 13-Node Architecture
# ---------------------------------------------------------------------------
NODES = [
    {"id": "front-end", "label": "front-end", "role": "EDGE_API", "criticality": 1.0, "type": "gateway"},
    {"id": "orders", "label": "orders", "role": "CORE_API", "criticality": 0.9, "type": "service"},
    {"id": "orders-db", "label": "orders-db", "role": "DATABASE", "criticality": 0.9, "type": "db"},
    {"id": "catalogue", "label": "catalogue", "role": "CORE_API", "criticality": 0.8, "type": "service"},
    {"id": "catalogue-db", "label": "catalogue-db", "role": "DATABASE", "criticality": 0.8, "type": "db"},
    {"id": "payment", "label": "payment", "role": "CORE_API", "criticality": 0.85, "type": "service"},
    {"id": "user", "label": "user", "role": "AUTH_API", "criticality": 0.7, "type": "service"},
    {"id": "user-db", "label": "user-db", "role": "DATABASE", "criticality": 0.7, "type": "db"},
    {"id": "carts", "label": "carts", "role": "STATE_API", "criticality": 0.75, "type": "service"},
    {"id": "carts-db", "label": "carts-db", "role": "DATABASE", "criticality": 0.75, "type": "db"},
    {"id": "shipping", "label": "shipping", "role": "ASYNC_WORKER", "criticality": 0.6, "type": "service"},
    {"id": "rabbitmq", "label": "rabbitmq", "role": "MESSAGE_BROKER", "criticality": 0.65, "type": "queue"},
    {"id": "queue-master", "label": "queue-master", "role": "QUEUE_MANAGER", "criticality": 0.5, "type": "service"},
]

EDGES = [
    {"source": "front-end", "target": "orders", "type": "CALLS"},
    {"source": "front-end", "target": "catalogue", "type": "CALLS"},
    {"source": "front-end", "target": "user", "type": "CALLS"},
    {"source": "front-end", "target": "carts", "type": "CALLS"},
    {"source": "orders", "target": "payment", "type": "CALLS"},
    {"source": "orders", "target": "shipping", "type": "CALLS"},
    {"source": "orders", "target": "orders-db", "type": "CONNECTS_TO"},
    {"source": "catalogue", "target": "catalogue-db", "type": "CONNECTS_TO"},
    {"source": "user", "target": "user-db", "type": "CONNECTS_TO"},
    {"source": "carts", "target": "carts-db", "type": "CONNECTS_TO"},
    {"source": "shipping", "target": "rabbitmq", "type": "PUBLISHES"},
    {"source": "queue-master", "target": "rabbitmq", "type": "SUBSCRIBES"},
]
SERVICE_IDS = {node["id"] for node in NODES}

# Fault scenarios are defined in the typed catalogue above; no shell-command strings are used.

# ---------------------------------------------------------------------------
# Global Runtime State
# ---------------------------------------------------------------------------
class SystemState:
    def __init__(self):
        self.active_fault: Optional[Dict[str, Any]] = None
        self.active_faults: List[Dict[str, Any]] = []
        self.fault_start_time: Optional[float] = None
        self.subgraph_isolated: bool = False
        self.current_run_id: Optional[str] = None
        self.current_comparison_id: Optional[str] = None
        self.current_attempt_id: Optional[str] = None
        self.fault_set_revision: int = 0
        self.current_policy_id: Optional[str] = None
        self.current_phase: str = "idle"
        self.rl_investigation: Dict[str, Any] = {
            "status": "idle",
            "policy": None,
            "confidence": None,
            "cost_spent": None,
            "probes_taken": 0,
            "latency_ms": None,
            "trace": []
        }
        self.telemetry = {
            "cpu": None, "memory_gb": None, "error_rate_5xx": None,
            "latency_ms": None, "node_states": {n["id"]: "unknown" for n in NODES},
            "source": "prometheus", "last_updated": None, "availability": "unknown"
        }
        self.logs: List[Dict[str, str]] = []

state = SystemState()

def database():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    class ClosingConnection(sqlite3.Connection):
        def __exit__(self, exc_type, exc, traceback):
            try:
                return super().__exit__(exc_type, exc, traceback)
            finally:
                self.close()
    connection = sqlite3.connect(DATABASE_PATH, timeout=5, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL, faults TEXT NOT NULL DEFAULT '[]', policies TEXT NOT NULL DEFAULT '[]', comparison_id TEXT);
        CREATE TABLE IF NOT EXISTS chats (run_id TEXT PRIMARY KEY, messages TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS comparisons (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL, policies TEXT NOT NULL, shared_capture_id TEXT, result TEXT NOT NULL DEFAULT '{}', error TEXT);
        CREATE TABLE IF NOT EXISTS run_fault_instances (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, fault_id TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, reverted_at TEXT, receipt TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS run_events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, revision INTEGER NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS run_attempts (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL, policies TEXT NOT NULL, capture_id TEXT, results TEXT NOT NULL DEFAULT '{}', error TEXT);
        CREATE TABLE IF NOT EXISTS controller_requests (request_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, attempt_id TEXT NOT NULL, start_event INTEGER NOT NULL, next_event INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS controller_requests (request_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, attempt_id TEXT NOT NULL, next_event INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS lab_lease (singleton INTEGER PRIMARY KEY CHECK(singleton=1), run_id TEXT, owner TEXT, acquired_at TEXT);
        CREATE TABLE IF NOT EXISTS basic_injection (singleton INTEGER PRIMARY KEY CHECK(singleton=1), run_id TEXT NOT NULL, fault_id TEXT NOT NULL, project TEXT NOT NULL, resource TEXT NOT NULL, container_id TEXT NOT NULL, created_at TEXT NOT NULL, receipt TEXT NOT NULL);
        INSERT OR IGNORE INTO lab_lease(singleton,run_id,owner,acquired_at) VALUES(1,NULL,NULL,NULL);
    """)
    return connection

def iso_now():
    return datetime.now(timezone.utc).isoformat()

def json_safe(value):
    """Convert Neo4j temporal/spatial values and nested properties to JSON values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return str(value)

def run_record(row):
    return {"id": row["id"], "created_at": row["created_at"], "status": row["status"], "faults": json.loads(row["faults"]), "policies": json.loads(row["policies"]), "comparison_id": row["comparison_id"]}

def append_run_event(connection, run_id, revision, kind, payload):
    connection.execute("INSERT INTO run_events(run_id,revision,kind,created_at,payload) VALUES(?,?,?,?,?)",
        (run_id, revision, kind, iso_now(), json.dumps(payload)))

def saved_faults_for_run(connection,run_id):
    rows=connection.execute("SELECT id,fault_id,status FROM run_fault_instances WHERE run_id=? ORDER BY created_at",(run_id,)).fetchall()
    return [{"id":r["fault_id"],"name":FAULT_BY_ID.get(r["fault_id"],{}).get("name",r["fault_id"]),
        "target":FAULT_BY_ID.get(r["fault_id"],{}).get("target"),"impact_path":FAULT_BY_ID.get(r["fault_id"],{}).get("impact_path",[]),
        "instance_id":r["id"],"status":r["status"]} for r in rows]

def fault_supported(fault):
    if fault.get("id") in _BASIC_STOP_TARGETS:
        target = _BASIC_STOP_TARGETS[fault["id"]]
        if not _lab_snapshot.get("available"):
            return False, _lab_snapshot.get("error") or f"Docker project {DOCKER_COMPOSE_PROJECT} is unavailable."
        if _lab_snapshot.get("states", {}).get(target) != "running":
            return False, f"{target} is not running in {DOCKER_COMPOSE_PROJECT}."
        return True, None
    if fault.get("mechanism") != "controller" or fault.get("id") not in LIVE_SCENARIOS:
        return False, "Scenario is not implemented by the V3 live controller."
    status=live_controller.status()
    if not status["available"]:
        return False, "; ".join(status["blockers"])
    if fault["id"] != "healthy":
        if not _lab_snapshot.get("available"):
            return False, _lab_snapshot.get("error") or "The isolated rca-lab deployment is unavailable."
        resources=[resource for resource,_ in LIVE_SCENARIOS[fault["id"]] if resource.startswith("resource/")]
        missing=[resource.split("/",1)[1] for resource in resources if _lab_snapshot.get("states",{}).get(resource.split("/",1)[1]) != "running"]
        if missing:
            return False, "V3 scenario target is not running: "+", ".join(missing)
    return True, None

def lab_driver():
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from rca_investigation.topology import sockshop_topology
    from rca_investigation.faults import LabDriver
    from rca_investigation.faults_v2 import AdaptiveLabDriver
    # The basic fault injector targets the operator's existing local stack.
    # Advanced V2/V3 helpers retain their isolated rca-lab assumptions.
    return LabDriver(sockshop_topology(), PROJECT_ROOT, project=DOCKER_COMPOSE_PROJECT), AdaptiveLabDriver(sockshop_topology(), PROJECT_ROOT)

_lab_snapshot={"available":False,"states":{},"error":f"The configured Docker project {DOCKER_COMPOSE_PROJECT} has not been checked yet."}

def refresh_lab_snapshot():
    """Read the approved Compose project and exact resource states; never mutate it."""
    global _lab_snapshot
    try:
        driver,_=lab_driver()
        driver.resolve()
        _lab_snapshot={"available":True,"states":driver.states(),"error":None}
    except Exception as exc:
        _lab_snapshot={"available":False,"states":{},"error":f"Docker project {DOCKER_COMPOSE_PROJECT} unavailable: {str(exc)[:180]}"}
    return _lab_snapshot

# ---------------------------------------------------------------------------
# Helper: Neo4j / Docker live sync
# ---------------------------------------------------------------------------
def check_docker_available() -> bool:
    try:
        res = subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        return res.returncode == 0
    except Exception:
        return False

def check_neo4j_available() -> bool:
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD), connection_timeout=2.0)
        driver.verify_connectivity()
        driver.close()
        return True
    except Exception:
        return False

source_status = {name: {"available": False, "last_success": None, "error": "Not checked yet"} for name in ("docker", "neo4j", "prometheus", "loki", "sockshop")}

def check_http_source(name: str, url: str) -> bool:
    try:
        response = requests.get(url, timeout=1.5)
        response.raise_for_status()
        source_status[name] = {"available": True, "last_success": iso_now(), "error": None}
        return True
    except requests.RequestException as exc:
        source_status[name] = {"available": False, "last_success": source_status[name]["last_success"], "error": type(exc).__name__}
        return False

def poll_prometheus_summary() -> None:
    queries = {
        "cpu": 'sum(rate(process_cpu_seconds_total{job="sockshop"}[1m])) * 100',
        "memory_gb": 'sum(process_resident_memory_bytes{job="sockshop"}) / 1073741824',
        "error_rate_5xx": 'sum(rate(http_requests_total{job="sockshop",status=~"5.."}[1m]))',
        "latency_ms": 'histogram_quantile(0.99, sum by (le) (rate(http_server_requests_seconds_bucket{job="sockshop"}[5m]))) * 1000',
        "services_up": 'sum(up{job="sockshop"})',
    }
    result = {}
    try:
        for key, query in queries.items():
            response = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}, timeout=2)
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") != "success":
                raise ValueError("Prometheus returned an unsuccessful query")
            rows = payload.get("data", {}).get("result", [])
            result[key] = float(rows[0]["value"][1]) if rows else None
        state.telemetry.update(result, last_updated=iso_now(), availability="available" if any(v is not None for v in result.values()) else "no_data")
        source_status["prometheus"] = {"available": any(v is not None for v in result.values()), "last_success": iso_now(), "error": None if any(v is not None for v in result.values()) else "No matching Sock Shop metrics"}
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        state.telemetry["availability"] = "unavailable"
        source_status["prometheus"] = {"available": False, "last_success": source_status["prometheus"]["last_success"], "error": type(exc).__name__}

def source_health() -> Dict[str, bool]:
    source_status["docker"]["available"] = check_docker_available()
    if source_status["docker"]["available"]:
        source_status["docker"]["last_success"] = iso_now()
        refresh_lab_snapshot()
    else:
        global _lab_snapshot
        _lab_snapshot={"available":False,"states":{},"error":f"Docker daemon is unavailable; project {DOCKER_COMPOSE_PROJECT} could not be checked."}
    source_status["neo4j"]["available"] = check_neo4j_available()
    if source_status["neo4j"]["available"]:
        source_status["neo4j"]["last_success"] = iso_now()
    source_status["prometheus"]["available"] = check_http_source("prometheus", f"{PROMETHEUS_URL}/-/ready")
    source_status["loki"]["available"] = check_http_source("loki", f"{LOKI_URL}/ready")
    source_status["sockshop"]["available"] = check_http_source("sockshop", SOCKSHOP_URL)
    return {name: bool(value["available"]) for name, value in source_status.items()}

# ---------------------------------------------------------------------------
# Background Telemetry Generator & WebSocket Broadcaster
# ---------------------------------------------------------------------------
active_websockets: List[WebSocket] = []

def get_current_payload() -> Dict[str, Any]:
    elapsed_str = ""
    if state.active_fault and state.fault_start_time:
        secs = int(time.time() - state.fault_start_time)
        m, s = divmod(secs, 60)
        elapsed_str = f"{m:02d}:{s:02d}"

    # Update dynamic node states based on fault
    node_states = {n["id"]: "healthy" for n in NODES}
    active_faults = state.active_faults or ([state.active_fault] if state.active_fault else [])
    for active_fault in active_faults:
        target = active_fault["target"]
        impact = active_fault["impact_path"]
        node_states[target] = "anomalous"
        for n in impact:
            if n != target and node_states[n] != "anomalous":
                node_states[n] = "degraded"

    return {
        "active_fault": state.active_fault["name"] if state.active_fault else None,
        "fault_id": state.active_fault["id"] if state.active_fault else None,
        "fault_target": state.active_fault["target"] if state.active_fault else None,
        "active_faults": [{"id": f["id"], "name": f["name"], "target": f["target"], "impact_path": f["impact_path"]} for f in active_faults],
        "fault_elapsed": elapsed_str,
        "subgraph_isolated": state.subgraph_isolated,
        "run_id": state.current_run_id,
        "comparison_id": state.current_comparison_id,
        "attempt_id": state.current_attempt_id,
        "revision": state.fault_set_revision,
        "policy_id": state.current_policy_id,
        "phase": state.current_phase,
        "rl_investigation": state.rl_investigation,
        "telemetry": {
            **state.telemetry,
            "node_states": node_states
        },
        "logs": state.logs[-25:],
        "nodes": NODES,
        "edges": EDGES,
        "fault_catalogue": {item["id"]:{"id":item["id"],"name":item["name"],"target":item["target"],"description":item["description"],"impact_path":item["impact_path"],"supported":fault_supported(item)[0],"reason":fault_supported(item)[1]} for item in FAULT_CATALOGUE},
        "health": {**{name: bool(value["available"]) for name, value in source_status.items()}, "ws_latency": "live"},
        "sources": source_status,
    }

async def telemetry_broadcaster():
    """Poll live metric summaries and publish service and fault state."""
    last_poll = 0.0
    last_health = 0.0
    while True:
        await asyncio.sleep(1.0)
        if time.monotonic() - last_poll >= 15:
            await asyncio.to_thread(poll_prometheus_summary)
            last_poll = time.monotonic()
        if time.monotonic() - last_health >= 15:
            await asyncio.to_thread(source_health)
            last_health = time.monotonic()

        payload = get_current_payload()
        disconnected = []
        for ws in active_websockets:
            try:
                await ws.send_json(payload)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            if ws in active_websockets:
                active_websockets.remove(ws)

@app.on_event("startup")
async def startup_event():
    connection = database()
    connection.close()
    # Rehydrate the visible live-run context after a server restart. Recovery remains
    # explicit; the persistent lease prevents a second run from altering the lab.
    try:
        with database() as connection:
            lease=connection.execute("SELECT run_id FROM lab_lease WHERE singleton=1").fetchone()["run_id"]
            basic=connection.execute("SELECT fault_id FROM basic_injection WHERE singleton=1").fetchone()
            rows=connection.execute("SELECT fault_id FROM run_fault_instances WHERE run_id=? AND status='active' ORDER BY created_at",(lease,)).fetchall() if lease else []
        state.current_run_id=lease
        state.active_faults=[FAULT_BY_ID[row["fault_id"]] for row in rows if row["fault_id"] in FAULT_BY_ID]
        if basic and basic["fault_id"] in FAULT_BY_ID:
            state.active_faults=[FAULT_BY_ID[basic["fault_id"]]]
        state.active_fault=state.active_faults[-1] if state.active_faults else None
        state.fault_start_time=time.time() if state.active_faults else None
    except Exception:
        pass
    asyncio.create_task(asyncio.to_thread(source_health))
    asyncio.create_task(telemetry_broadcaster())
    live_controller.start()

# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(index_file)

@app.get("/api/state")
async def get_state():
    return get_current_payload()

@app.get("/api/controller")
async def get_controller_status():
    status=live_controller.status()
    status["local_lab"]=_lab_snapshot
    return status

@app.get("/api/topology")
async def get_live_topology():
    try:
        from neo4j import GraphDatabase
        driver=GraphDatabase.driver(NEO4J_URI,auth=(NEO4J_USER,NEO4J_PASSWORD),connection_timeout=2.0)
        try:
            with driver.session() as session:
                records=session.run("""
                    MATCH (n) WHERE n.name IN $names
                    OPTIONAL MATCH (n)-[r]->(m) WHERE m.name IN $names
                    RETURN properties(n) AS props, labels(n) AS labels,
                           collect(CASE WHEN r IS NULL THEN null ELSE {source:startNode(r).name,target:endNode(r).name,type:type(r),properties:properties(r)} END) AS outgoing
                    ORDER BY props.name
                """,names=list(SERVICE_IDS))
                nodes={};edges={}
                for record in records:
                    props=record["props"] or {};name=props.get("name")
                    if not name: continue
                    nodes[name]={"id":name,"label":props.get("display_name",name),"role":props.get("role",props.get("category","Unknown")),"type":props.get("type",props.get("category","service")),"labels":list(record["labels"] or []),"properties":json_safe(props)}
                    for edge in record["outgoing"] or []:
                        if edge and edge.get("source") and edge.get("target"):
                            key=(edge["source"],edge["target"],edge["type"])
                            edges[key]=json_safe({**edge,"properties":edge.get("properties") or {}})
            return {"available":True,"source":"neo4j","nodes":list(nodes.values()),"edges":list(edges.values()),"updated_at":iso_now()}
        finally: driver.close()
    except Exception as exc:
        return {"available":False,"source":"neo4j","nodes":[],"edges":[],"updated_at":None,"error":type(exc).__name__}

@app.get("/api/sources")
async def get_sources():
    return {"sources": source_status}

@app.get("/api/runs")
async def list_runs():
    with database() as connection:
        rows = connection.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
    return {"runs": [run_record(row) for row in rows]}

class RunRequest(BaseModel):
    id: Optional[str] = None
    status: str = "Active"
    faults: List[Dict[str, Any]] = []
    policies: List[str] = []
    fault_id: Optional[str] = None

@app.post("/api/runs")
async def create_run(req: RunRequest):
    fault = FAULT_BY_ID.get(req.fault_id) if req.fault_id else None
    if not fault:
        raise HTTPException(status_code=400, detail="Choose one scenario from the V3 controller catalogue")
    if len(req.policies) > len(POLICY_NAMES) or len(set(req.policies)) != len(req.policies) or any(policy not in POLICY_NAMES for policy in req.policies):
        raise HTTPException(status_code=400, detail="Choose unique policies from the available V3 policy list")
    await asyncio.to_thread(refresh_lab_snapshot)
    supported, reason = fault_supported(fault)
    if not supported:
        raise HTTPException(status_code=503, detail=reason)
    # Fault injection is useful on its own. If no policy is selected, create a
    # plainly labeled injector-only run for the two existing stop/restart paths.
    if not req.policies and fault["id"] in _BASIC_STOP_TARGETS:
        identifier = req.id or f"RUN-{int(time.time() * 1000)}"
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identifier):
            raise HTTPException(status_code=400, detail="Invalid run identifier")
        with database() as connection:
            if connection.execute("SELECT 1 FROM runs WHERE id=?", (identifier,)).fetchone():
                raise HTTPException(status_code=409, detail="Run identifier already exists")
            connection.execute("INSERT INTO runs(id,created_at,status,faults,policies) VALUES(?,?,?,?,?)",
                (identifier, iso_now(), "Starting", json.dumps([{"id":fault["id"],"name":fault["name"],"target":fault["target"],"status":"queued"}]), "[]"))
            connection.execute("INSERT INTO chats(run_id,messages,updated_at) VALUES(?,?,?)", (identifier, "[]", iso_now()))
            instance_id=f"FI-{uuid4().hex}"
            connection.execute("INSERT INTO run_fault_instances(id,run_id,fault_id,revision,status,created_at,receipt) VALUES(?,?,?,?,?,?,?)",
                (instance_id,identifier,fault["id"],1,"queued",iso_now(),"{}"))
            attempt_id=f"ATT-{uuid4().hex}"
            message="Fault injection only; no RL policy was selected or executed."
            connection.execute("INSERT INTO run_attempts(id,run_id,revision,created_at,status,phase,policies,error) VALUES(?,?,?,?,?,?,?,?)",
                (attempt_id,identifier,1,iso_now(),"not_requested","complete","[]",message))
        try:
            result = await inject_fault(FaultRequest(fault_id=fault["id"], run_id=identifier))
        except HTTPException as exc:
            with database() as connection:
                connection.execute("UPDATE runs SET status='Failed' WHERE id=?", (identifier,))
                connection.execute("UPDATE run_attempts SET status='failed',error=? WHERE id=?", (str(exc.detail),attempt_id))
            raise
        with database() as connection:
            connection.execute("UPDATE runs SET status='Active' WHERE id=?", (identifier,))
            connection.execute("UPDATE run_fault_instances SET status='active',receipt=? WHERE id=?", (json.dumps(result["receipt"]),instance_id))
            connection.execute("UPDATE runs SET faults=? WHERE id=?", (json.dumps([{ "id":fault["id"],"name":fault["name"],"target":fault["target"],"status":"active"}]),identifier))
            append_run_event(connection,identifier,1,"fault_injected",result)
            row=connection.execute("SELECT * FROM runs WHERE id=?",(identifier,)).fetchone()
        state.current_run_id=identifier;state.current_attempt_id=attempt_id;state.fault_set_revision=1;state.current_phase="injecting"
        return {"run":run_record(row),"attempt":{"id":attempt_id,"status":"not_requested","phase":"complete","policies":[],"message":message},"warning":message}
    inventory = await asyncio.to_thread(policy_inventory)
    unavailable = [item for item in inventory if item["id"] in req.policies and not item["available"]]
    if unavailable:
        raise HTTPException(status_code=503, detail="Selected policy unavailable: " + "; ".join(f"{p['label']}: {p['reason']}" for p in unavailable))
    status = live_controller.start()
    if not status["available"] or not status["running"]:
        raise HTTPException(status_code=503, detail="V3 controller did not start: " + "; ".join(status["blockers"]))
    identifier = req.id or f"RUN-{int(time.time() * 1000)}"
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identifier):
        raise HTTPException(status_code=400, detail="Invalid run identifier")
    with database() as connection:
        lease=connection.execute("SELECT run_id FROM lab_lease WHERE singleton=1").fetchone()["run_id"]
        if lease:
            raise HTTPException(status_code=409, detail=f"The live lab is currently leased by {lease}")
        connection.execute("INSERT INTO runs(id,created_at,status,faults,policies) VALUES(?,?,?,?,?)",
            (identifier, iso_now(), "Active", json.dumps([{"id":fault["id"],"name":fault["name"],"target":fault["target"],"status":"queued"}]), json.dumps(req.policies)))
        connection.execute("INSERT INTO chats(run_id,messages,updated_at) VALUES(?,?,?)", (identifier, "[]", iso_now()))
        connection.execute("UPDATE lab_lease SET run_id=?,owner=?,acquired_at=? WHERE singleton=1",(identifier,identifier,iso_now()))
        instance_id=f"FI-{uuid4().hex}"
        connection.execute("INSERT INTO run_fault_instances(id,run_id,fault_id,revision,status,created_at,receipt) VALUES(?,?,?,?,?,?,?)",(instance_id,identifier,fault["id"],1,"queued",iso_now(),"{}"))
        attempt_id=f"ATT-{uuid4().hex}"
        connection.execute("INSERT INTO run_attempts(id,run_id,revision,created_at,status,phase,policies,error) VALUES(?,?,?,?,?,?,?,NULL)",(attempt_id,identifier,1,iso_now(),"queued","capturing",json.dumps(req.policies)))
    try:
        start_event=len(live_controller.events())
        request_id=live_controller.enqueue({"command":"run","case":fault["id"],"policy":req.policies[0]})
    except Exception as exc:
        with database() as connection:
            connection.execute("UPDATE lab_lease SET run_id=NULL,owner=NULL,acquired_at=NULL WHERE singleton=1 AND run_id=?",(identifier,))
            connection.execute("UPDATE runs SET status='Failed' WHERE id=?",(identifier,))
            append_run_event(connection,identifier,1,"controller_enqueue_failed",{"error":str(exc)[:300]})
        raise HTTPException(status_code=503, detail=f"Could not queue the V3 controller request: {exc}")
    with database() as connection:
        connection.execute("INSERT INTO controller_requests(request_id,run_id,attempt_id,start_event,next_event) VALUES(?,?,?,?,?)",(request_id,identifier,attempt_id,start_event,start_event))
        append_run_event(connection,identifier,1,"controller_queued",{"request_id":request_id,"scenario":fault["id"],"primary_policy":req.policies[0],"replay_policies":req.policies[1:]})
        row=connection.execute("SELECT * FROM runs WHERE id=?",(identifier,)).fetchone()
    state.current_run_id=identifier;state.current_attempt_id=attempt_id;state.fault_set_revision=1;state.current_phase="capturing"
    return {"run":run_record(row),"attempt":{"id":attempt_id,"status":"queued","phase":"capturing","policies":req.policies},"controller_request_id":request_id}

@app.get("/api/faults")
async def list_faults():
    await asyncio.to_thread(refresh_lab_snapshot)
    rows=[]
    for item in FAULT_CATALOGUE:
        supported,reason=fault_supported(item)
        rows.append({**item,"supported":supported,"reason":reason,"parameters":{}})
    return {"faults":rows}

def sync_controller_events(run_id: str):
    rows=live_controller.events()
    with database() as connection:
        mappings=connection.execute("SELECT * FROM controller_requests WHERE run_id=? ORDER BY start_event",(run_id,)).fetchall()
        for mapping_index,mapping in enumerate(mappings):
            stop_at=mappings[mapping_index+1]["start_event"] if mapping_index+1<len(mappings) else len(rows)
            begin_at=mapping["next_event"]
            for index in range(begin_at,min(stop_at,len(rows))):
                event=rows[index]
                kind=event.get("kind","event");payload=event.get("payload") or {}
                attempt=connection.execute("SELECT * FROM run_attempts WHERE id=?",(mapping["attempt_id"],)).fetchone()
                if not attempt: continue
                phase=attempt["phase"];status=attempt["status"];error=attempt["error"]
                if kind=="stage":
                    message=str(payload.get("message",""))
                    incident_live="fault active" in message.lower() or "collecting fresh evidence" in message.lower()
                    phase="investigating" if incident_live or "evidence" in message.lower() else "capturing"
                    status="running"
                    if incident_live:
                        connection.execute("UPDATE run_fault_instances SET status='active' WHERE run_id=? AND status='queued'",(run_id,))
                        row=connection.execute("SELECT fault_id FROM run_fault_instances WHERE run_id=? LIMIT 1",(run_id,)).fetchone()
                        state.active_fault=FAULT_BY_ID.get(row["fault_id"]) if row else None
                        state.active_faults=[state.active_fault] if state.active_fault else []
                        state.fault_start_time=state.fault_start_time or time.time()
                elif kind in ("probe","diagnosis","latency"):
                    phase="investigating";status="running"
                elif kind=="error":
                    error=str(payload.get("message","Controller error"))[:500]
                    status="failed" if "RECOVERY" in error.upper() else "running"
                elif kind=="result":
                    receipt=payload
                    status="completed" if receipt.get("cleanup_confirmed") and receipt.get("health",{}).get("confirmed") else "failed"
                    phase="complete"
                    error=receipt.get("error") or receipt.get("recovery_error_type")
                    connection.execute("UPDATE runs SET status=? WHERE id=?",("Resolved" if status=="completed" else "Recovery failed",run_id))
                    connection.execute("UPDATE run_fault_instances SET status='completed',reverted_at=?,receipt=? WHERE run_id=?",(iso_now(),json.dumps(receipt),run_id))
                    connection.execute("UPDATE lab_lease SET run_id=NULL,owner=NULL,acquired_at=NULL WHERE singleton=1 AND run_id=?",(run_id,))
                    fault_rows=json.loads(connection.execute("SELECT faults FROM runs WHERE id=?",(run_id,)).fetchone()["faults"])
                    for fault in fault_rows: fault["status"]="completed"
                    connection.execute("UPDATE runs SET faults=? WHERE id=?",(json.dumps(fault_rows),run_id))
                    state.current_phase=phase
                    state.active_fault=None;state.active_faults=[];state.fault_start_time=None
                results=json.loads(attempt["results"] or "{}")
                if kind=="diagnosis" and payload.get("policy"):
                    decision=payload.get("decision") or {}
                    results[payload["policy"]]={"state":"diagnosed","decision":decision,"diagnosis":", ".join(decision.get("cause_ids",[])) or decision.get("decision_type"),"confidence":decision.get("confidence"),"probes":payload.get("probes"),"credits":payload.get("credits"),"trace":payload.get("trace",[]),"correct":None}
                if kind=="result":
                    policy=payload.get("policy")
                    diagnosis=payload.get("diagnosis") or {}
                    decision=diagnosis.get("decision") or {}
                    fault_row=connection.execute("SELECT fault_id FROM run_fault_instances WHERE run_id=? LIMIT 1",(run_id,)).fetchone()
                    truth=sorted(cause for cause,_ in LIVE_SCENARIOS.get(fault_row["fault_id"] if fault_row else "healthy",()))
                    if policy:
                        causes=sorted(decision.get("cause_ids",[]))
                        results[policy]={"state":"completed" if status=="completed" else "failed","decision":decision,"diagnosis":", ".join(causes) or decision.get("decision_type"),"confidence":decision.get("confidence"),"probes":diagnosis.get("probes"),"credits":diagnosis.get("credits"),"diagnosis_seconds":payload.get("diagnosis_seconds"),"recovery":"Agent repair verified" if payload.get("resolved") else ("Injector cleanup verified" if payload.get("cleanup_confirmed") else "Not verified"),"correct":bool(decision.get("decision_type")=="DIAGNOSE" and causes==truth),"trace":diagnosis.get("trace",[]) }
                connection.execute("UPDATE run_attempts SET status=?,phase=?,results=?,error=?,capture_id=COALESCE(?,capture_id) WHERE id=?",(status,phase,json.dumps(results),error,(payload.get("id") if kind=="result" else None),mapping["attempt_id"]))
                append_run_event(connection,run_id,1,"controller_"+kind,payload)
                connection.execute("UPDATE controller_requests SET next_event=? WHERE request_id=?",(index+1,mapping["request_id"]))
                mapping=dict(mapping);mapping["next_event"]=index+1

async def replay_selected_policies(run_id: str, policies_override=None, force=False):
    with database() as connection:
        run=connection.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone()
        attempt=connection.execute("SELECT * FROM run_attempts WHERE run_id=? ORDER BY created_at DESC LIMIT 1",(run_id,)).fetchone()
        if not run or not attempt or attempt["status"]!="completed": return
        policies=list(policies_override or json.loads(run["policies"]));receipt_event=connection.execute("SELECT payload FROM run_events WHERE run_id=? AND kind='controller_result' ORDER BY id DESC LIMIT 1",(run_id,)).fetchone()
        if not receipt_event: return
        receipt=json.loads(receipt_event["payload"])
        comparison_id=f"CMP-{uuid4().hex}"
        if not force and connection.execute("SELECT 1 FROM comparisons WHERE run_id=? AND status IN ('capturing','running','complete','partial')",(run_id,)).fetchone(): return
        connection.execute("INSERT INTO comparisons(id,run_id,created_at,status,phase,policies,shared_capture_id,result) VALUES(?,?,?,?,?,?,?,?)",(comparison_id,run_id,iso_now(),"running","replaying",json.dumps(policies),receipt.get("id"),"{}"))
        connection.execute("UPDATE runs SET comparison_id=?,policies=? WHERE id=?",(comparison_id,json.dumps(policies),run_id))
    results=json.loads((LIVE_OUTPUT/"comparisons"/(comparison_id+".json")).read_text()) if (LIVE_OUTPUT/"comparisons"/(comparison_id+".json")).exists() else None
    if results is None:
        extra=[p for p in policies if p!=receipt.get("policy")]
        if extra:
            python=live_controller.status()["python"]
            incident=LIVE_OUTPUT/"incidents"/str(receipt.get("id"))
            output=LIVE_OUTPUT/"comparisons"/(comparison_id+".json")
            output.parent.mkdir(parents=True,exist_ok=True)
            proc=await asyncio.create_subprocess_exec(python,str(PROJECT_ROOT/"scripts/replay_live_v3.py"),"--bundle",str(POLICY_BUNDLE),"--incident",str(incident),"--policies",",".join(extra),"--output",str(output),cwd=str(PROJECT_ROOT),env={**os.environ,"PYTHONPATH":str(PROJECT_ROOT/"src")},stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            stdout,stderr=await proc.communicate()
            if proc.returncode:
                with database() as connection: connection.execute("UPDATE comparisons SET status='partial',phase='complete',error=? WHERE id=?",((stderr or stdout).decode(errors="replace")[-1200:],comparison_id))
                return
            results=json.loads(output.read_text())
        else:
            results={"capture_id":receipt.get("id"),"case":receipt.get("case"),"policies":{}}
    rows=results.get("policies",{})
    with database() as connection:
        attempt=connection.execute("SELECT results FROM run_attempts WHERE id=?",(attempt["id"],)).fetchone()
        merged=json.loads(attempt["results"] or "{}")
        for policy,row in rows.items():
            if row.get("status")=="error":
                merged[policy]={"state":"failed","diagnosis":"Policy replay failed","correct":None,"error":row["error"]};continue
            decision=row.get("decision") or {}
            causes=sorted(decision.get("cause_ids",[]))
            merged[policy]={"state":"completed","decision":decision,"diagnosis":", ".join(causes) or decision.get("decision_type"),"confidence":decision.get("confidence"),"probes":row.get("probes"),"credits":row.get("credits"),"correct":row.get("exact_set_correct"),"trace":row.get("trace",[]),"recovery":"Shadow replay · no remediation"}
        status="partial" if any(v.get("state")=="failed" for v in merged.values()) else "complete"
        connection.execute("UPDATE run_attempts SET results=? WHERE id=?",(json.dumps(merged),attempt["id"]))
        connection.execute("UPDATE comparisons SET status=?,phase='complete',result=? WHERE id=?",(status,json.dumps(merged),comparison_id))
        append_run_event(connection,run_id,1,"policy_replay_complete",{"comparison_id":comparison_id,"capture_id":receipt.get("id"),"status":status})

@app.get("/api/runs/{run_id}/workspace")
async def get_run_workspace(run_id: str):
    sync_controller_events(run_id)
    with database() as connection:
        run=connection.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone()
        if not run: raise HTTPException(status_code=404,detail="Run not found")
        faults=connection.execute("SELECT * FROM run_fault_instances WHERE run_id=? ORDER BY created_at",(run_id,)).fetchall()
        events=connection.execute("SELECT * FROM run_events WHERE run_id=? ORDER BY id",(run_id,)).fetchall()
        attempts=connection.execute("SELECT * FROM run_attempts WHERE run_id=? ORDER BY created_at DESC",(run_id,)).fetchall()
        lease=connection.execute("SELECT run_id FROM lab_lease WHERE singleton=1").fetchone()["run_id"]
    response={"run":run_record(run),"revision":max([0]+[row["revision"] for row in events]),"live_lease":lease,
        "faults":[{"instance_id":r["id"],"fault_id":r["fault_id"],"revision":r["revision"],"status":r["status"],"created_at":r["created_at"],"reverted_at":r["reverted_at"],"receipt":json.loads(r["receipt"])} for r in faults],
        "events":[{"id":r["id"],"revision":r["revision"],"kind":r["kind"],"created_at":r["created_at"],"payload":json.loads(r["payload"])} for r in events],
        "attempts":[{"id":r["id"],"revision":r["revision"],"created_at":r["created_at"],"status":r["status"],"phase":r["phase"],"policies":json.loads(r["policies"]),"capture_id":r["capture_id"],"results":json.loads(r["results"]),"error":r["error"]} for r in attempts]}
    if any(item["status"]=="completed" for item in response["attempts"]):
        asyncio.create_task(replay_selected_policies(run_id))
    return response

@app.post("/api/runs/{run_id}/faults")
async def add_run_fault(run_id: str, body: Dict[str,Any]):
    raise HTTPException(status_code=409,detail="The existing V3 controller owns one scenario from injection through cleanup per run. Start another run for a separate scenario.")

@app.delete("/api/runs/{run_id}/faults/{instance_id}")
async def revert_run_fault(run_id: str,instance_id: str):
    with database() as connection:
        row=connection.execute("SELECT * FROM basic_injection WHERE singleton=1").fetchone()
        fault=connection.execute("SELECT * FROM run_fault_instances WHERE id=? AND run_id=?",(instance_id,run_id)).fetchone()
    if not row or row["run_id"]!=run_id or not fault or fault["status"]!="active":
        raise HTTPException(status_code=409,detail="This fault is not an active injector-only fault owned by the selected run.")
    result=await recover_fault()
    with database() as connection:
        attempt=connection.execute("SELECT id FROM run_attempts WHERE run_id=? ORDER BY created_at DESC LIMIT 1",(run_id,)).fetchone()
    return {"ok":True,"recovered":result,"attempt":{"id":attempt["id"] if attempt else None}}

class RunImportRequest(BaseModel):
    runs: List[Dict[str, Any]] = []
    threads: Dict[str, List[Dict[str, Any]]] = {}

@app.post("/api/runs/import")
async def import_legacy_runs(req: RunImportRequest):
    imported=0
    with database() as connection:
        for item in req.runs[:500]:
            identifier=str(item.get("id", ""))
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}",identifier):
                continue
            connection.execute("INSERT OR IGNORE INTO runs(id,created_at,status,faults,policies) VALUES(?,?,?,?,?)",
                (identifier,str(item.get("created_at") or iso_now()),item.get("status") if item.get("status") in ("Active","Resolved") else "Resolved",
                 json.dumps(item.get("faults",[])[:50]),json.dumps((item.get("policies") or ([item["policy"]] if item.get("policy") else []))[:20])))
            messages=req.threads.get(identifier,[])[:500]
            if messages:
                connection.execute("INSERT OR IGNORE INTO chats(run_id,messages,updated_at) VALUES(?,?,?)",(identifier,json.dumps(messages),iso_now()))
            imported+=1
    return {"imported": imported}

class RunUpdate(BaseModel):
    status: Optional[str] = None
    policies: Optional[List[str]] = None

@app.patch("/api/runs/{run_id}")
async def update_run(run_id: str, req: RunUpdate):
    changes=[]; values=[]
    if req.status is not None:
        if req.status not in ("Active","Resolved"):
            raise HTTPException(status_code=400, detail="Invalid run status")
        changes.append("status=?");values.append(req.status)
    if req.policies is not None:
        if len(req.policies) > len(POLICY_NAMES) or len(set(req.policies)) != len(req.policies) or any(policy not in POLICY_NAMES for policy in req.policies):
            raise HTTPException(status_code=400, detail="Choose unique supported policy IDs")
        changes.append("policies=?");values.append(json.dumps(req.policies[:9]))
    if changes:
        with database() as connection:
            values.append(run_id)
            cursor=connection.execute(f"UPDATE runs SET {','.join(changes)} WHERE id=?",values)
            if not cursor.rowcount: raise HTTPException(status_code=404,detail="Run not found")
            row=connection.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone()
        return {"run":run_record(row)}
    raise HTTPException(status_code=400,detail="No run fields provided")

@app.get("/api/runs/{run_id}/chat")
async def get_run_chat(run_id: str):
    with database() as connection:
        row=connection.execute("SELECT messages FROM chats WHERE run_id=?",(run_id,)).fetchone()
    if not row: raise HTTPException(status_code=404,detail="Run not found")
    return {"messages":json.loads(row["messages"])}

def save_chat_message(run_id: Optional[str], role: str, message: str):
    if not run_id: return
    with database() as connection:
        row=connection.execute("SELECT messages FROM chats WHERE run_id=?",(run_id,)).fetchone()
        if not row: return
        messages=json.loads(row["messages"])
        messages.append({"role":role,"text":message,"time":iso_now()})
        connection.execute("UPDATE chats SET messages=?,updated_at=? WHERE run_id=?",(json.dumps(messages[-1000:]),iso_now(),run_id))

def policy_inventory():
    return live_controller.policy_inventory()

@app.get("/api/policies")
async def get_policies():
    policies=await asyncio.to_thread(policy_inventory)
    return {"policies": policies, "default_selection": [p["id"] for p in policies if p["available"]][:2], "controller": live_controller.status()}

class ComparisonRequest(BaseModel):
    run_id: str
    policies: List[str]

@app.post("/api/comparisons")
async def start_comparison(req: ComparisonRequest):
    if not req.policies or len(req.policies)>9 or len(set(req.policies))!=len(req.policies) or any(policy not in POLICY_NAMES for policy in req.policies):
        raise HTTPException(status_code=400,detail="Choose one or more unique V3 policies")
    with database() as connection:
        run=connection.execute("SELECT * FROM runs WHERE id=?",(req.run_id,)).fetchone()
        attempt=connection.execute("SELECT * FROM run_attempts WHERE run_id=? ORDER BY created_at DESC LIMIT 1",(req.run_id,)).fetchone()
    if not run: raise HTTPException(status_code=404,detail="Run not found")
    if not attempt or attempt["status"]!="completed": raise HTTPException(status_code=409,detail="Policy comparison is available after the live controller finishes and verifies cleanup")
    inventory=await asyncio.to_thread(policy_inventory)
    unavailable=[p for p in inventory if p["id"] in req.policies and not p["available"]]
    if unavailable: raise HTTPException(status_code=503,detail="Selected policy unavailable: "+"; ".join(f"{p['label']}: {p['reason']}" for p in unavailable))
    await replay_selected_policies(req.run_id,req.policies,force=True)
    with database() as connection:
        row=connection.execute("SELECT * FROM comparisons WHERE run_id=? ORDER BY created_at DESC LIMIT 1",(req.run_id,)).fetchone()
    return {"comparison":{"id":row["id"],"run_id":row["run_id"],"status":row["status"],"phase":row["phase"],"policies":json.loads(row["policies"]),"shared_capture_id":row["shared_capture_id"],"result":json.loads(row["result"]),"error":row["error"]}}

@app.get("/api/comparisons/{comparison_id}")
async def get_comparison(comparison_id: str):
    with database() as connection:
        row=connection.execute("SELECT * FROM comparisons WHERE id=?",(comparison_id,)).fetchone()
    if not row: raise HTTPException(status_code=404,detail="Comparison not found")
    return {"comparison":{"id":row["id"],"run_id":row["run_id"],"created_at":row["created_at"],"status":row["status"],"phase":row["phase"],"policies":json.loads(row["policies"]),"shared_capture_id":row["shared_capture_id"],"result":json.loads(row["result"]),"error":row["error"]}}

def prometheus_range(query: str, start: float, end: float, step: int):
    response=requests.get(f"{PROMETHEUS_URL}/api/v1/query_range",params={"query":query,"start":start,"end":end,"step":step},timeout=(2,5))
    response.raise_for_status();payload=response.json()
    if payload.get("status")!="success": raise ValueError("Prometheus query failed")
    output=[]
    for series in payload.get("data",{}).get("result",[]):
        output.append({"labels":series.get("metric",{}),"values":[[float(t),float(v)] for t,v in series.get("values",[]) if v not in ("NaN","+Inf","-Inf")]})
    return output

@app.get("/api/telemetry/metrics")
async def telemetry_metrics(seconds: int = 300, services: str = "affected"):
    if seconds not in (300,900,3600): raise HTTPException(status_code=400,detail="Unsupported time window")
    selected=[] if services in ("", "affected", "all") else services.split(",")
    if any(name not in SERVICE_IDS for name in selected): raise HTTPException(status_code=400,detail="Unknown service filter")
    now=time.time();start=now-seconds;step=max(15,seconds//120)
    service_selector="" if not selected or services=="all" else ",instance=~\"("+"|".join(re.escape(name)+r":(80|8079)" for name in selected)+")\""
    queries={"cpu":f'sum by (instance) (rate(process_cpu_seconds_total{{job="sockshop"{service_selector}}}[1m])) * 100',"memory":f'sum by (instance) (process_resident_memory_bytes{{job="sockshop"{service_selector}}}) / 1048576',"errors":f'sum by (instance) (rate(http_requests_total{{job="sockshop",status=~"5.."{service_selector}}}[1m]))',"latency":f'histogram_quantile(0.99, sum by (le,instance) (rate(http_server_requests_seconds_bucket{{job="sockshop"{service_selector}}}[5m]))) * 1000',"up":f'sum(up{{job="sockshop"{service_selector}}})'}
    output={};errors={}
    for name,query in queries.items():
        try: output[name]=await asyncio.to_thread(prometheus_range,query,start,now,step)
        except (requests.RequestException,ValueError,TypeError) as exc: output[name]=[];errors[name]=type(exc).__name__
    return {"source":"prometheus","available":bool(output["cpu"] or output["memory"] or output["errors"] or output["latency"] or output["up"]),"last_updated":iso_now(),"window_seconds":seconds,"series":output,"errors":errors}

@app.get("/api/telemetry/logs")
async def telemetry_logs(seconds: int = 300, services: str = "affected", severity: str = "all", limit: int = 100):
    if seconds not in (300,900,3600) or severity not in ("all","error","warn","info") or not 1<=limit<=200: raise HTTPException(status_code=400,detail="Invalid log query")
    selected=[] if services in ("", "affected", "all") else services.split(",")
    if any(name not in SERVICE_IDS for name in selected): raise HTTPException(status_code=400,detail="Unknown service filter")
    labels="container=~\".*("+"|".join(re.escape(name) for name in selected)+").*\"" if selected and services!="all" else 'container=~".+"'
    query="{"+labels+"}"
    now=time.time();params={"query":query,"start":str(int((now-seconds)*1e9)),"end":str(int(now*1e9)),"limit":limit,"direction":"backward"}
    try:
        response=await asyncio.to_thread(requests.get,f"{LOKI_URL}/loki/api/v1/query_range",params=params,timeout=(2,5));response.raise_for_status();payload=response.json()
        if payload.get("status")!="success": raise ValueError("Loki query failed")
        rows=[]
        for stream in payload.get("data",{}).get("result",[]):
            service=stream.get("stream",{}).get("container","")
            for timestamp_ns,line in stream.get("values",[]):
                level="error" if re.search(r"\b(error|exception|fatal)\b",line,re.I) else "warn" if re.search(r"\b(warn|warning)\b",line,re.I) else "info"
                if severity=="all" or level==severity: rows.append({"time":datetime.fromtimestamp(int(timestamp_ns)/1e9,timezone.utc).isoformat(),"service":service,"severity":level,"message":line[:1500]})
        return {"source":"loki","available":True,"last_updated":iso_now(),"logs":rows[:limit],"error":None}
    except (requests.RequestException,ValueError,KeyError,TypeError) as exc:
        source_status["loki"]={"available":False,"last_success":source_status["loki"]["last_success"],"error":type(exc).__name__}
        return {"source":"loki","available":False,"last_updated":None,"logs":[],"error":type(exc).__name__}

class FaultRequest(BaseModel):
    fault_id: str
    run_id: Optional[str] = None

@app.post("/api/fault/inject")
async def inject_fault(req: FaultRequest):
    # These two bounded stop/restart injectors are implemented by the existing
    # basic lab driver and do not depend on the RL bundle or V3 controller.
    # More advanced mechanisms remain owned by their verified controller path.
    targets = {"catalogue_db_stop": "catalogue-db", "orders_stop": "orders"}
    resource = targets.get(req.fault_id)
    if not resource:
        raise HTTPException(status_code=400, detail="This local injector supports catalogue_db_stop and orders_stop only.")
    run_id = req.run_id or f"LOCAL-{uuid4().hex[:12]}"
    with database() as connection:
        active = connection.execute("SELECT resource FROM basic_injection WHERE singleton=1").fetchone()
        if active:
            raise HTTPException(status_code=409, detail=f"Recover the active basic fault on {active['resource']} before injecting another.")
        acquired = connection.execute("UPDATE lab_lease SET run_id=?,owner=?,acquired_at=? WHERE singleton=1 AND run_id IS NULL",
            (run_id, run_id, iso_now())).rowcount
        if acquired != 1:
            lease = connection.execute("SELECT run_id FROM lab_lease WHERE singleton=1").fetchone()["run_id"]
            raise HTTPException(status_code=409, detail=f"The live lab is currently leased by {lease}")
    driver, _ = lab_driver()
    try:
        driver.resolve()
        if driver.state(resource)["state"] != "running":
            raise HTTPException(status_code=409, detail=f"{resource} is not running; no fault was injected.")
        receipt = driver.inject_stop(resource)
    except HTTPException:
        with database() as connection:
            connection.execute("UPDATE lab_lease SET run_id=NULL,owner=NULL,acquired_at=NULL WHERE singleton=1 AND run_id=?", (run_id,))
        raise
    except Exception as exc:
        with database() as connection:
            connection.execute("UPDATE lab_lease SET run_id=NULL,owner=NULL,acquired_at=NULL WHERE singleton=1 AND run_id=?", (run_id,))
        raise HTTPException(status_code=503, detail=f"Could not inject fault into {DOCKER_COMPOSE_PROJECT}/{resource}: {str(exc)[:240]}")
    try:
        with database() as connection:
            connection.execute("INSERT INTO basic_injection(singleton,run_id,fault_id,project,resource,container_id,created_at,receipt) VALUES(1,?,?,?,?,?,?,?)",
                (run_id, req.fault_id, DOCKER_COMPOSE_PROJECT, resource, receipt["container_id"], iso_now(), json.dumps(receipt)))
    except Exception:
        driver.recover(resource)
        with database() as connection:
            connection.execute("UPDATE lab_lease SET run_id=NULL,owner=NULL,acquired_at=NULL WHERE singleton=1 AND run_id=?", (run_id,))
        raise HTTPException(status_code=503, detail="Injection was recovered because its receipt could not be persisted.")
    state.active_fault = FAULT_BY_ID.get(req.fault_id)
    state.active_faults = [state.active_fault] if state.active_fault else []
    state.current_run_id = run_id
    state.fault_start_time = time.time()
    return {"ok": True, "project": DOCKER_COMPOSE_PROJECT, "fault_id": req.fault_id, "resource": resource, "receipt": receipt}

@app.post("/api/fault/recover")
async def recover_fault():
    with database() as connection:
        active = connection.execute("SELECT * FROM basic_injection WHERE singleton=1").fetchone()
    if not active:
        raise HTTPException(status_code=409, detail="There is no active basic fault to recover.")
    resource = active["resource"]
    try:
        if active["project"] != DOCKER_COMPOSE_PROJECT:
            raise RuntimeError("configured Compose project differs from the injection receipt")
        driver, _ = lab_driver()
        driver.resolve()
        if driver.ids.get(resource) != active["container_id"]:
            raise RuntimeError("container identity changed since injection")
        # recover() re-inspects the exact container identity and Compose labels.
        receipt = driver.recover(resource) if driver.state(resource)["state"] != "running" else {"already_running": True}
        if driver.state(resource)["state"] != "running":
            raise RuntimeError("target did not return to running state")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Recovery failed for {resource}: {str(exc)[:240]}")
    result = {"ok": True, "project": DOCKER_COMPOSE_PROJECT, "resource": resource, "receipt": receipt, "state": "running"}
    with database() as connection:
        connection.execute("DELETE FROM basic_injection WHERE singleton=1")
        connection.execute("UPDATE lab_lease SET run_id=NULL,owner=NULL,acquired_at=NULL WHERE singleton=1 AND run_id=?", (active["run_id"],))
        connection.execute("UPDATE run_fault_instances SET status='reverted',reverted_at=?,receipt=? WHERE run_id=? AND fault_id=? AND status='active'", (iso_now(),json.dumps({"injector":json.loads(active["receipt"]),"recovery":receipt}),active["run_id"],active["fault_id"]))
        connection.execute("UPDATE runs SET status='Resolved' WHERE id=?", (active["run_id"],))
        run_row=connection.execute("SELECT faults FROM runs WHERE id=?", (active["run_id"],)).fetchone()
        if run_row:
            run_faults=json.loads(run_row["faults"] or "[]")
            for fault in run_faults:
                if fault.get("id")==active["fault_id"]: fault["status"]="reverted"
            connection.execute("UPDATE runs SET faults=? WHERE id=?", (json.dumps(run_faults),active["run_id"]))
        append_run_event(connection,active["run_id"],1,"fault_recovered",result)
    state.current_phase="complete"
    state.active_fault = None; state.active_faults = []; state.fault_start_time = None
    return result

@app.post("/api/topology/toggle_subgraph")
async def toggle_subgraph():
    state.subgraph_isolated = not state.subgraph_isolated
    return {"subgraph_isolated": state.subgraph_isolated}

class ChatRequest(BaseModel):
    message: str
    run_id: Optional[str] = None

@app.post("/api/chat")
async def operator_chat(req: ChatRequest):
    user_msg = req.message.strip()
    if not user_msg:
        raise HTTPException(status_code=400, detail="Empty query")
    run_id=req.run_id or state.current_run_id
    run_faults=[];run_attempts=[]
    if run_id:
        with database() as connection:
            if not connection.execute("SELECT 1 FROM runs WHERE id=?",(run_id,)).fetchone():
                raise HTTPException(status_code=404,detail="Run not found")
            run_faults=[dict(row) for row in connection.execute("SELECT fault_id,status,revision FROM run_fault_instances WHERE run_id=? ORDER BY created_at",(run_id,)).fetchall()]
            run_attempts=[dict(row) for row in connection.execute("SELECT id,revision,status,policies,results,error FROM run_attempts WHERE run_id=? ORDER BY created_at DESC LIMIT 3",(run_id,)).fetchall()]
        save_chat_message(run_id,"user",user_msg)

    active=[FAULT_BY_ID[row["fault_id"]] for row in run_faults if row["status"]=="active" and row["fault_id"] in FAULT_BY_ID]
    active_target=", ".join(item["target"] for item in active) or "None (Healthy)"
    active_name="; ".join(item["name"] for item in active) or "Healthy Baseline"
    impact="; ".join(" -> ".join(item["impact_path"]) for item in active) or "No active blast radius"
    attempt_context=json.dumps(run_attempts,default=str,separators=(",",":"))

    # If Gemini API key is configured, invoke Gemini 3.5 Flash Lite
    if GEMINI_API_KEY and not GEMINI_API_KEY.startswith("your_"):
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel(
                GEMINI_MODEL,
                system_instruction=(
                    "You are the Graph-RCA Incident Command AI Operator for the Weaveworks Sock Shop microservices.\n"
                    f"ACTIVE INCIDENT: {active_name} on [{active_target}]\n"
                    f"PROPAGATION PATH: {impact}\n"
                    f"Run-scoped policy attempts: {attempt_context}.\n"
                    "Respond with crisp, precise technical root-cause insights. Cite node references as [node:service_name]. "
                    "Use only recorded run evidence, distinguish unavailable data from diagnosis, and never claim RL ran when its attempt says unavailable. Keep answers concise (2-4 sentences max)."
                )
            )
            res = model.generate_content(user_msg)
            reply=res.text.strip();save_chat_message(run_id,"assistant",reply)
            return {"reply": reply}
        except Exception as e:
            pass

    # Built-in intelligent deterministic response if Gemini API key isn't set
    lower = user_msg.lower()
    if "propagation" in lower or "blast" in lower:
        reply = f"Failure propagation traced across the service graph: {impact}. The active fault is at [node:{active_target}]."
    elif "rollback" in lower or "fix" in lower or "recover" in lower:
        reply = f"Recovery is an explicit operator action. Revert the corresponding active fault from the run’s Fault set. Current target(s): {active_target}."
    elif "cypher" in lower or "graph" in lower:
        reply = f"The active graph path is: {impact}. Open Topology to inspect the connected services."
    elif "rl" in lower or "policy" in lower or "voi" in lower:
        reply = "This run’s attempt record is included in the workspace. If its state is unavailable, no RL policy was run; inspect the recorded reason before drawing a conclusion."
    else:
        if state.active_fault:
            reply = f"The active fault is {active_name} at [node:{active_target}]. Review the live metrics and logs before choosing a recovery action."
        else:
            reply = "I don’t see an active fault in this workspace. Choose a run or inspect the latest service signals."
    save_chat_message(run_id,"assistant",reply)
    return {"reply":reply}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_websockets.append(websocket)
    # Send initial state immediately
    await websocket.send_json(get_current_payload())
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in active_websockets:
            active_websockets.remove(websocket)

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
