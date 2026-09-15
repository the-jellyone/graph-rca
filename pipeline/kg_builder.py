"""
Knowledge Graph Builder for Cloud-Native Microservices (Sock Shop)
Builds topological architecture, ingests real-time Prometheus telemetry,
and dynamically tracks anomaly propagation in Neo4j using the LangChain Neo4jGraph wrapper.
"""

import time
import requests
from datetime import datetime

# LangChain Neo4jGraph Connection Layer
try:
    from langchain_community.graphs import Neo4jGraph
except ImportError:
    try:
        from langchain_neo4j import Neo4jGraph
    except ImportError:
        from neo4j import GraphDatabase
        class Neo4jGraph:
            def __init__(self, url, username, password):
                self.driver = GraphDatabase.driver(url, auth=(username, password))
            def query(self, query, params=None):
                with self.driver.session() as session:
                    result = session.run(query, params or {})
                    return [dict(r) for r in result]
            def close(self):
                if hasattr(self, 'driver') and self.driver:
                    self.driver.close()

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASS = "password123"
PROMETHEUS = "http://localhost:9090"
POLL_INTERVAL = 30  # seconds

SERVICES_METADATA = {
    "front-end": {
        "role": "API Gateway & Customer Web UI",
        "category": "Service",
        "criticality": "critical",
        "calls": ["catalogue", "carts", "orders", "user"],
    },
    "catalogue": {
        "role": "Product Catalogue Service",
        "category": "Service",
        "criticality": "high",
        "calls": ["catalogue-db"],
    },
    "catalogue-db": {
        "role": "Product MySQL Database",
        "category": "Database",
        "criticality": "critical",
        "calls": [],
    },
    "carts": {
        "role": "Cart Management Service",
        "category": "Service",
        "criticality": "high",
        "calls": ["carts-db"],
    },
    "carts-db": {
        "role": "Cart Mongo Database",
        "category": "Database",
        "criticality": "high",
        "calls": [],
    },
    "orders": {
        "role": "Order Processing Service",
        "category": "Service",
        "criticality": "critical",
        "calls": ["payment", "shipping", "orders-db"],
    },
    "orders-db": {
        "role": "Order Mongo Database",
        "category": "Database",
        "criticality": "critical",
        "calls": [],
    },
    "payment": {
        "role": "Payment Authorization Service",
        "category": "Service",
        "criticality": "critical",
        "calls": [],
    },
    "user": {
        "role": "User Account & Authentication Service",
        "category": "Service",
        "criticality": "medium",
        "calls": ["user-db"],
    },
    "user-db": {
        "role": "User Profile Mongo Database",
        "category": "Database",
        "criticality": "medium",
        "calls": [],
    },
    "shipping": {
        "role": "Dispatch & Shipping Service",
        "category": "Service",
        "criticality": "medium",
        "calls": ["queue-master"],
    },
    "queue-master": {
        "role": "Async Queue Worker Service",
        "category": "Service",
        "criticality": "low",
        "calls": ["rabbitmq"],
    },
    "rabbitmq": {
        "role": "AMQP Message Broker",
        "category": "Queue",
        "criticality": "medium",
        "calls": [],
    },
}

for svc, meta in SERVICES_METADATA.items():
    callers = [
        caller for caller, c_meta in SERVICES_METADATA.items()
        if svc in c_meta["calls"]
    ]
    meta["who_calls_it"] = callers


class KGBuilder:
    def __init__(self, uri=NEO4J_URI, user=NEO4J_USER, password=NEO4J_PASS, prometheus_url=PROMETHEUS):
        self.uri = uri
        self.user = user
        self.password = password
        self.prometheus_url = prometheus_url
        try:
            # Tell LangChain NOT to query apoc.meta.data()
            self.graph = Neo4jGraph(
                url=self.uri,
                username=self.user,
                password=self.password,
                enhanced_schema=False,
                refresh_schema=False
            )
        except Exception:
            # Pure native fallback wrapper with exact same .query() interface (zero APOC)
            from neo4j import GraphDatabase
            class _PureNeo4j:
                def __init__(self, u, usr, pwd):
                    self._d = GraphDatabase.driver(u, auth=(usr, pwd))
                def query(self, q, params=None):
                    with self._d.session() as s:
                        res = s.run(q, params or {})
                        return [dict(r) for r in res]
                def close(self):
                    self._d.close()
            self.graph = _PureNeo4j(self.uri, self.user, self.password)

        print(f"Connected to Neo4j via LangChain Neo4jGraph (APOC disabled) at {self.uri}")

    def close(self):
        if hasattr(self.graph, "close"):
            self.graph.close()
            print("Neo4j connection closed.")

    def clear_graph(self):
        self.graph.query("MATCH (n) DETACH DELETE n")
        print("Graph database cleared.")

    def create_service_nodes(self):
        for name, meta in SERVICES_METADATA.items():
            label = meta["category"]
            self.graph.query(f"""
                MERGE (n:{label} {{name: $name}})
                SET n.role = $role,
                    n.category = $category,
                    n.criticality = $criticality,
                    n.what_it_calls = $calls,
                    n.who_calls_it = $who_calls_it,
                    n.status = 'healthy',
                    n.anomaly_flag = false,
                    n.anomaly_type = '',
                    n.anomaly_since = '',
                    n.cpu_usage = 0.0,
                    n.memory_usage = 0.0,
                    n.error_rate = 0.0,
                    n.latency_p99 = 0.0,
                    n.last_updated = $ts
            """,
            params={
                "name": name,
                "role": meta["role"],
                "category": meta["category"],
                "criticality": meta["criticality"],
                "calls": meta["calls"],
                "who_calls_it": meta["who_calls_it"],
                "ts": datetime.utcnow().isoformat()
            })
        print(f"Created {len(SERVICES_METADATA)} service nodes with complete metadata.")

    def create_topology_edges(self):
        total_edges = 0
        for source, meta in SERVICES_METADATA.items():
            for target in meta["calls"]:
                self.graph.query("""
                    MATCH (a {name: $source})
                    MATCH (b {name: $target})
                    MERGE (a)-[r:CALLS]->(b)
                    SET r.avg_latency = 0.0,
                        r.error_rate = 0.0
                """, params={"source": source, "target": target})
                total_edges += 1
        print(f"Created {total_edges} architectural [:CALLS] edges.")

    def query_prometheus(self, query):
        try:
            resp = requests.get(
                f"{self.prometheus_url}/api/v1/query",
                params={"query": query},
                timeout=5
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                return data.get("result", [])
        except Exception:
            pass
        return []

    def is_container_running(self, service_name):
        """Direct check if Docker container is currently running with exact service name matching."""
        try:
            import subprocess
            # Check both running AND paused (paused = Docker Desktop was paused, not a crash)
            for status in ("running", "paused"):
                cmd = ["docker", "ps", "--filter", f"status={status}", "--format", "{{.Names}}"]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
                if res.returncode == 0:
                    for name in res.stdout.strip().splitlines():
                        if f"-{service_name}-" in name or name.endswith(f"-{service_name}"):
                            return True
            return False
        except Exception:
            return True  # assume running if check fails

    def _query_cpu(self, service_name):
        """Try Go-style then Spring Boot CPU metric."""
        # Go services
        r = self.query_prometheus(
            f'rate(process_cpu_seconds_total{{instance=~".*{service_name}.*"}}[1m]) * 100'
        )
        if r:
            return float(r[0]["value"][1])
        # Spring Boot
        r = self.query_prometheus(
            f'process_cpu_usage{{instance=~".*{service_name}.*"}} * 100'
        )
        if r:
            return float(r[0]["value"][1])
        return 0.0

    def _query_mem(self, service_name):
        """Try Go RSS → JVM heap → Spring Boot memory."""
        for query in [
            f'process_resident_memory_bytes{{instance=~".*{service_name}.*"}}',
            f'jvm_memory_used_bytes{{instance=~".*{service_name}.*",area="heap"}}',
            f'jvm_memory_committed_bytes{{instance=~".*{service_name}.*",area="heap"}}',
        ]:
            r = self.query_prometheus(query)
            if r:
                return float(r[0]["value"][1]) / 1e6
        return 0.0

    def _query_err(self, service_name):
        """Try standard HTTP error rate metrics."""
        for query in [
            f'rate(http_requests_total{{instance=~".*{service_name}.*",status=~"5.."}}[1m])',
            f'rate(http_server_requests_seconds_count{{instance=~".*{service_name}.*",status=~"5.."}}[1m])',
        ]:
            r = self.query_prometheus(query)
            if r:
                return float(r[0]["value"][1])
        return 0.0

    def update_node_metrics(self, service_name, grace=False):
        """Update Neo4j node with live metrics. grace=True skips anomaly detection."""

        container_running = self.is_container_running(service_name)
        cpu = self._query_cpu(service_name)
        mem = self._query_mem(service_name)
        err = self._query_err(service_name)

        up_res  = self.query_prometheus(f'up{{instance=~".*{service_name}.*"}}')
        prom_up = float(up_res[0]["value"][1]) == 1.0 if up_res else None

        # Crash = container explicitly NOT running.
        # If container is running but metrics are all 0, it's still warming up — NOT a crash.
        is_crash = not container_running

        # If container check is ambiguous (exception → defaulted True) but Prometheus
        # confirms the target is down, trust Prometheus.
        if not is_crash and prom_up is False:
            is_crash = True

        anomaly      = False
        anomaly_type = None

        if not grace:
            if is_crash:
                anomaly      = True
                anomaly_type = "service_crash"
            elif cpu > 85.0:
                anomaly      = True
                anomaly_type = "cpu_exhaustion"
            elif err > 0.05:
                anomaly      = True
                anomaly_type = "high_error_rate"
            elif mem > 1000.0:
                anomaly      = True
                anomaly_type = "memory_exhaustion"

        ts = datetime.utcnow().isoformat()
        self.graph.query("""
            MATCH (n {name: $name})
            SET n.cpu_usage    = $cpu,
                n.memory_usage = $mem,
                n.error_rate   = $err,
                n.anomaly_flag = $anomaly,
                n.anomaly_type = CASE WHEN $anomaly THEN $anomaly_type ELSE null END,
                n.anomaly_since = CASE
                    WHEN $anomaly AND n.anomaly_since IS NULL THEN $ts
                    WHEN $anomaly THEN n.anomaly_since
                    ELSE null END,
                n.status       = CASE WHEN $anomaly THEN 'anomalous' ELSE 'healthy' END,
                n.last_updated = $ts
        """, params={
            "name":         service_name,
            "cpu":          round(cpu, 2),
            "mem":          round(mem, 2),
            "err":          round(err, 4),
            "anomaly":      anomaly,
            "anomaly_type": anomaly_type,
            "ts":           ts,
        })

        if not anomaly:
            self.graph.query(
                "MATCH (n {name: $name})-[r:TRIGGERS]->() DELETE r",
                params={"name": service_name}
            )

        if anomaly:
            print(f"⚠️  ANOMALY: [{service_name}] -> {anomaly_type} "
                  f"(CPU: {cpu:.1f}%, ERR: {err:.4f}, MEM: {mem:.1f}MB)")
        return anomaly

    def create_triggers_edge(self, source, target, fault_type="propagation"):
        """Creates a directed TRIGGERS edge representing fault propagation from source to target."""
        self.graph.query("""
            MATCH (a {name: $source})
            MATCH (b {name: $target})
            MERGE (a)-[r:TRIGGERS]->(b)
            SET r.fault_type = $fault_type,
                r.propagation_time = $ts,
                r.confidence = 0.90
        """, params={"source": source, "target": target, "fault_type": fault_type, "ts": datetime.utcnow().isoformat()})
        print(f"  🔗 PROPAGATION: ({source}) -[:TRIGGERS]-> ({target})")

    def poll_and_propagate(self, grace=False):
        print(f"[{datetime.utcnow().strftime('%H:%M:%S')}] Polling {len(SERVICES_METADATA)} services"
              + (" (grace period — no anomaly detection)" if grace else "") + "...")
        anomalous = []

        for svc in SERVICES_METADATA:
            if self.update_node_metrics(svc, grace=grace):
                anomalous.append(svc)

        if anomalous:
            for svc in anomalous:
                for caller in SERVICES_METADATA[svc].get("who_calls_it", []):
                    self.create_triggers_edge(svc, caller)
            print(f"🔴 Total anomalous services: {anomalous}")
        else:
            self.graph.query("MATCH ()-[r:TRIGGERS]->() DELETE r")
            if not grace:
                print("✅ All services healthy.")

        return anomalous

    def build_initial_graph(self):
        print("=== Initializing Sock Shop Knowledge Graph ===")
        self.clear_graph()
        self.create_service_nodes()
        self.create_topology_edges()
        print("Knowledge Graph Successfully Initialized in Neo4j.")

    # Aliases for compatibility
    def build(self):
        self.build_initial_graph()

    def poll_all_services(self):
        return self.poll_and_propagate()

    def run_polling_loop(self, interval=POLL_INTERVAL):
        print(f"Starting live metric polling loop (Interval: {interval}s). Press Ctrl+C to stop.")
        try:
            while True:
                self.poll_and_propagate()
                time.sleep(interval)
        except KeyboardInterrupt:
            print("Polling stopped by user.")



if __name__ == "__main__":
    kg = KGBuilder()
    kg.build_initial_graph()
    kg.poll_and_propagate()
    kg.close()

