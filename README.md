# Graph-Based Intelligent Root Cause Analysis

AI-powered RCA system for cloud-native microservices. Combines a live Knowledge Graph (Neo4j) with GraphRAG-based dynamic causal expansion and an LLM chat interface to automatically detect, trace, and explain incidents.

---

## Architecture

```
Sock Shop (17 containers)
  └── Prometheus (metrics) + Loki (logs)
        └── kg_builder.py  — builds & polls live Neo4j Knowledge Graph
              └── retriever.py  — dynamic causal expansion (GraphRAG)
                    └── llm_chat.py  — LLM Q&A session (Gemini / Groq)
```

---

## Quick Start

### 1. Prerequisites
- Docker Desktop
- Python 3.10+
- Gemini or Groq API key

### 2. Setup

```bash
cd rca-project
bash setup.sh
source venv/bin/activate

cp pipeline/.env.example pipeline/.env
# Edit pipeline/.env and add your API key
```

### 3. Start the Stack

```bash
docker compose up -d
```

Wait ~60 seconds for all services to boot.

### 4. Run

```bash
# Terminal 1 — live monitor
python pipeline/monitor.py

# Terminal 2 — LLM chat
python pipeline/llm_chat.py

# Terminal 3 — inject faults
python pipeline/fault_injector.py
```

---

## Fault Scenarios

| # | Type | Target |
|:--|:-----|:-------|
| 1 | DB crash | catalogue-db |
| 2 | CPU burn | payment |
| 3 | Service crash | orders |
| 4 | Bad deploy | payment |
| 5 | Memory leak | carts |
| 6 | Upstream DB crash | orders-db |
| 7 | Auth failure | user |
| 8 | Multi-fault chaos | catalogue-db + payment |
| 9 | Silent downstream failure | shipping |

---

## Key URLs (after stack is up)

| Service | URL |
|:--------|:----|
| Sock Shop UI | http://localhost:80 |
| Prometheus | http://localhost:9090 |
| Neo4j Browser | http://localhost:7474 |
| Loki | http://localhost:3100 |

Neo4j login: `neo4j / password123`

---

## Pipeline Files

| File | Purpose |
|:-----|:--------|
| `kg_builder.py` | Builds Neo4j graph, polls Prometheus, detects anomalies |
| `retriever.py` | GraphRAG — dynamic causal expansion, fetches logs |
| `llm_chat.py` | LLM chat session with live anomaly watcher |
| `monitor.py` | Live health dashboard (replaces collect.py) |
| `fault_injector.py` | Interactive fault injection menu |
| `graphrag.py` | Thin standalone graph inspector |
| `verify_all.py` | Checks all services are reachable |
