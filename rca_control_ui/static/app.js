/**
 * Graph-RCA Incident Command Console Client
 * Interactive Topology Canvas + Eye-Comfort Theme Engine + Real-time Telemetry
 */

let ws = null;
let currentState = null;
let activeFaultId = null;

// Node metadata dictionary
const NODE_META = {
  "front-end":    { label: "front-end", role: "Edge Web API & UI", upstream: [], downstream: ["orders", "catalogue", "user", "carts"] },
  "orders":       { label: "orders", role: "Order Lifecycle & Checkout", upstream: ["front-end"], downstream: ["payment", "shipping", "orders-db"] },
  "catalogue":    { label: "catalogue", role: "Product Catalog Query API", upstream: ["front-end"], downstream: ["catalogue-db"] },
  "user":         { label: "user", role: "Customer Profile & Auth", upstream: ["front-end"], downstream: ["user-db"] },
  "carts":        { label: "carts", role: "Shopping Cart State", upstream: ["front-end"], downstream: ["carts-db"] },
  "payment":      { label: "payment", role: "Payment Gateway / Authorize", upstream: ["orders"], downstream: [] },
  "shipping":     { label: "shipping", role: "Shipping Label & Dispatch", upstream: ["orders"], downstream: ["rabbitmq"] },
  "orders-db":    { label: "orders-db", role: "MongoDB Orders Store", upstream: ["orders"], downstream: [] },
  "catalogue-db": { label: "catalogue-db", role: "MySQL Catalogue Store", upstream: ["catalogue"], downstream: [] },
  "user-db":      { label: "user-db", role: "MongoDB Users Store", upstream: ["user"], downstream: [] },
  "carts-db":     { label: "carts-db", role: "MongoDB Carts Store", upstream: ["carts"], downstream: [] },
  "rabbitmq":     { label: "rabbitmq", role: "AMQP Message Broker", upstream: ["shipping"], downstream: ["queue-master"] },
  "queue-master": { label: "queue-master", role: "Async Queue Consumer", upstream: ["rabbitmq"], downstream: [] },
};

// Default layout coordinates (spaced comfortably across 820x420 canvas)
const NODE_COORDINATES = {
  "front-end":    { x: 380, y: 50 },
  "orders":       { x: 230, y: 140 },
  "catalogue":    { x: 440, y: 140 },
  "user":         { x: 100, y: 220 },
  "carts":        { x: 570, y: 170 },
  "payment":      { x: 200, y: 250 },
  "shipping":     { x: 330, y: 230 },
  "orders-db":    { x: 160, y: 350 },
  "catalogue-db": { x: 440, y: 260 },
  "user-db":      { x: 70, y: 340 },
  "carts-db":     { x: 600, y: 300 },
  "rabbitmq":     { x: 330, y: 340 },
  "queue-master": { x: 450, y: 370 },
};

// Runtime dynamic positions (allows drag and drop rearrangement)
let nodePositions = { ...NODE_COORDINATES };
let selectedNodeId = null;

// Zoom and Pan Engine State
let canvasTransform = {
  scale: 1.0,
  panX: 0,
  panY: 0,
  isPanning: false,
  startX: 0,
  startY: 0
};

// Node Drag State
let draggingNode = null;
let dragOffset = { x: 0, y: 0 };
let investigationRuns = [];
let currentRunId = null;
let chatThreads = {};
let latencySamples = [];
let showActiveSubgraph = false;
let selectedPolicies = [];
let policiesInventory = [];
let activeComparison = null;
let activeTelemetryTab = "metrics";
let logsPaused = false;
let telemetryTimer = null;
let workspaceRefreshTimer = null;
let faultCatalogue = [];
let currentWorkspace = null;
let liveTopology = null;

function saveInvestigationData() {
  try { localStorage.setItem("graph_rca_runs_v1", JSON.stringify({runs:investigationRuns,threads:chatThreads,current:currentRunId})); } catch (_) {}
}
function loadInvestigationData() {
  try { const saved=JSON.parse(localStorage.getItem("graph_rca_runs_v1")||"{}"); investigationRuns=Array.isArray(saved.runs)?saved.runs:[]; chatThreads=saved.threads&&typeof saved.threads==="object"?saved.threads:{}; currentRunId=saved.current||investigationRuns[0]?.id||null; } catch (_) {}
}

function showView(name) {
  const label={dashboard:"Overview",topology:"Topology",runs:"Runs",lab:"Runs",investigations:"Runs",chat:"Runs"}[name]||"Runs";
  const view=name==="dashboard"?"dashboard":name==="topology"?"topology":"runs";
  document.querySelectorAll(".view").forEach(v=>v.classList.toggle("active",v.id===`view-${view}`));
  document.querySelectorAll(".nav-item").forEach(v=>{const active=v.dataset.view===view;v.classList.toggle("active",active);if(active)v.setAttribute("aria-current","page");else v.removeAttribute("aria-current");});
  const crumb=document.getElementById("breadcrumb");if(crumb)crumb.textContent=label;
  if(view==="topology")loadLiveTopology();
  if(view==="runs"){renderRunList();if(currentRunId)loadRunWorkspace(currentRunId);startTelemetryPolling();}
  else stopTelemetryPolling();
}

async function startRun(){
  await Promise.all([loadFaultCatalogue(),loadPolicies()]);
  const dialog=document.getElementById("new-run-dialog");if(!dialog)return;
  renderStartRunOptions();dialog.showModal();
}
function closeNewRun(){document.getElementById("new-run-dialog")?.close();}
function renderStartRunOptions(){
  const select=document.getElementById("initial-fault");if(select){select.innerHTML=faultCatalogue.map(f=>`<option value="${escapeHtml(f.id)}" ${f.supported?"":"disabled"}>${escapeHtml(f.name)}${f.supported?"":" · unavailable"}</option>`).join("");const first=faultCatalogue.find(f=>f.supported);if(first)select.value=first.id;select.onchange=()=>{updateFaultNote("initial-fault","initial-fault-note");updateStartRunAvailability();};updateFaultNote("initial-fault","initial-fault-note");}
  const list=document.getElementById("new-run-policies");if(list)list.innerHTML=policiesInventory.map(p=>`<label class="policy-option ${p.available?"":"disabled"}"><input type="checkbox" value="${escapeHtml(p.id)}" ${p.available&&["dqn","voi"].includes(p.id)?"checked":""} ${p.available?"":"disabled"}><span><b>${escapeHtml(p.label)}</b><small>${p.available?"Available for this run":escapeHtml(p.reason||"Unavailable")}</small></span></label>`).join("");
  const availablePolicies=policiesInventory.filter(p=>p.available), warning=document.getElementById("new-run-warning");if(warning)warning.textContent=availablePolicies.length?"The V3 controller runs one live policy. Other selected policies replay against the same captured evidence after recovery.":"You can still inject and recover the two verified basic faults without RL. Policies are optional for these injector-only runs.";
  updateStartRunAvailability();
}
function updateStartRunAvailability(){const selected=document.getElementById("initial-fault")?.value;const fault=faultCatalogue.find(f=>f.id===selected);const available=policiesInventory.some(p=>p.available);const injectorOnly=fault?.supported&&["catalogue_db_stop","orders_stop"].includes(selected);const create=document.getElementById("create-run-button");if(create)create.disabled=!fault?.supported||(!available&&!injectorOnly);}
function updateFaultNote(selectId,noteId){const f=faultCatalogue.find(x=>x.id===document.getElementById(selectId)?.value);const note=document.getElementById(noteId);if(note)note.textContent=f?`${f.group} · ${f.target} · ${f.supported?f.description:f.reason}`:"No verified fault injector is available.";}
async function submitNewRun(event){
  event.preventDefault();const faultId=document.getElementById("initial-fault")?.value;const policies=[...document.querySelectorAll("#new-run-policies input:checked")].map(x=>x.value);
  if(!faultId){notify("Choose an available initial fault.");return;}
  const button=document.getElementById("create-run-button");button.disabled=true;button.textContent="Starting…";
  try{const response=await fetch("/api/runs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({fault_id:faultId,policies})});const data=await response.json();if(!response.ok)throw new Error(data.detail||"Run could not be started");currentRunId=data.run.id;localStorage.setItem("graph_rca_current_run",currentRunId);closeNewRun();if(!investigationRuns.some(r=>r.id===currentRunId))investigationRuns.unshift(data.run);await loadRuns();await loadRunWorkspace(currentRunId);showView("runs");if(data.warning)notify(data.warning);}
  catch(error){notify(error.message);}finally{button.disabled=false;button.textContent="Start run";}
}
async function concludeRun(){if(!currentRunId)return;try{await recoverFault();const r=await fetch(`/api/runs/${encodeURIComponent(currentRunId)}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({status:"Resolved"})});if(!r.ok)throw new Error("Could not conclude run");await loadRuns();await loadRunWorkspace(currentRunId);}catch(error){notify(error.message);}}
function openRun(id){currentRunId=id;localStorage.setItem("graph_rca_current_run",id);showView("runs");renderRuns();renderRunList();updateRunHeader();loadRunWorkspace(id);loadRunChat();}
function updateRunHeader(){
 const run=investigationRuns.find(r=>r.id===currentRunId),label=run?`${run.id} · ${run.status}`:"No run selected";
 ["current-run-label","global-run-label"].forEach(id=>{const el=document.getElementById(id);if(el)el.textContent=label;});
 const title=document.getElementById("conversation-title");if(title)title.textContent=run?`${run.id} · Run notes`:"Select a run";
 renderChatThread();
}
async function loadFaultCatalogue(){try{const r=await fetch("/api/faults");const data=await r.json();if(r.ok)faultCatalogue=data.faults||[];renderFaultCatalogue();}catch(_){} }
async function loadRuns(){try{const response=await fetch("/api/runs");const data=await response.json();investigationRuns=data.runs||[];const saved=localStorage.getItem("graph_rca_runs_v1");if(!localStorage.getItem("graph_rca_imported_v1")&&saved){try{const old=JSON.parse(saved);if(Array.isArray(old.runs)&&old.runs.length){await fetch("/api/runs/import",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({runs:old.runs,threads:old.threads||{}})});localStorage.setItem("graph_rca_imported_v1","1");const refresh=await fetch("/api/runs");investigationRuns=(await refresh.json()).runs||[];}else localStorage.setItem("graph_rca_imported_v1","1");}catch(_){}}currentRunId=localStorage.getItem("graph_rca_current_run")||currentRunId;if(!investigationRuns.some(r=>r.id===currentRunId))currentRunId=investigationRuns[0]?.id||null;renderRuns();renderRunList();updateRunHeader();if(currentRunId)await loadRunWorkspace(currentRunId);}catch(error){notify(`Run history unavailable: ${error.message}`);}}
async function loadRunWorkspace(id){try{const response=await fetch(`/api/runs/${encodeURIComponent(id)}/workspace`);const data=await response.json();if(!response.ok)throw new Error(data.detail||"Run could not be loaded");if(id!==currentRunId)return;currentWorkspace=data;renderRunWorkspace(data);await loadRunChat();if(document.getElementById("view-runs")?.classList.contains("active")&&data.live_lease===id)refreshTelemetry();}catch(error){notify(`Run details unavailable: ${error.message}`);}}
function renderRunWorkspace(data){
 const run=data.run,faults=data.faults||[],active=faults.filter(f=>f.status==="active"),isLease=data.live_lease===run.id;
 const legacyFaults=(run.faults||[]).filter(f=>!faults.some(saved=>saved.fault_id===f.id));
 const summary=document.getElementById("active-run-summary");if(summary)summary.textContent=`${active.length} active fault${active.length===1?"":"s"} · ${active.map(f=>FAULT_BY_ID_UI(f.fault_id)?.target||f.fault_id).join(", ")||"healthy baseline"}${legacyFaults.length?` · ${legacyFaults.length} legacy record(s)`:""}`;
 document.getElementById("workspace-revision").textContent=`v${data.revision||0}`;document.getElementById("workspace-lease").textContent=isLease?"Live lab lease":"Historical run";
 document.getElementById("policy-selection-summary").textContent=(run.policies||[]).map(id=>policiesInventory.find(p=>p.id===id)?.label||id).join(" · ")||"No policies selected";
 const faultHost=document.getElementById("active-fault-list");faultHost.innerHTML=active.length?active.map(f=>{const scenario=FAULT_BY_ID_UI(f.fault_id);return `<article class="active-fault-row"><span class="status-dot dot-red"></span><div><b>${escapeHtml(scenario?.name||f.fault_id)}</b><small>${escapeHtml(scenario?.target||"")} · managed by this run · ${new Date(f.created_at).toLocaleTimeString()}</small></div></article>`;}).join(""):(legacyFaults.length?`<div class="activity-empty">Legacy records: ${legacyFaults.map(f=>escapeHtml(f.name||f.id)).join(", ")}. Their live state is unknown and cannot be reverted here.</div>`:'<div class="activity-empty">No active fault in this run.</div>');
 const add=document.querySelector(".fault-manager-card .workspace-card-head .button");if(add)add.disabled=Boolean(isLease&&data.attempts?.some(a=>["queued","running"].includes(a.status)))||Boolean(data.live_lease&&data.live_lease!==run.id);
 const revertAll=document.querySelector(".run-actions .button-danger");if(revertAll)revertAll.disabled=!isLease||!active.length;
 const conclude=document.querySelector(".run-actions .button-soft");if(conclude)conclude.disabled=run.status!=="Active";
 const leaseNote=document.getElementById("workspace-lease");if(leaseNote&&data.live_lease&&data.live_lease!==run.id)leaseNote.textContent=`Lab busy · ${data.live_lease}`;
 const timeline=document.getElementById("fault-activity");timeline.innerHTML=(data.events||[]).slice().reverse().map(e=>`<div class="activity-item"><i class="activity-dot ${e.kind.includes("failed")?"error":""}"></i><b>${escapeHtml(e.kind.replaceAll("_"," "))}</b><small>v${e.revision} · ${new Date(e.created_at).toLocaleTimeString()}</small></div>`).join("")||'<div class="activity-empty">No run events yet.</div>';
 document.getElementById("activity-count").textContent=`${data.events?.length||0} events`;
 renderAttemptHistory(data.attempts||[]);
 const note=document.getElementById("run-graph-note");if(note)note.textContent=isLease?"Live graph details are available from Neo4j. Fault shading shows the selected run’s active impact paths.":"This is a historical run; live graph state reflects the current environment, not a saved graph snapshot.";
 renderMiniTopology(liveTopology);
 if(!isLease){renderMetrics(null,"Historical Prometheus windows are not stored for this run. Live telemetry is available only for the current lab lease.");renderLogs(null,"Historical Loki windows are not stored for this run.");}
 updateRunHeader();renderRunList();renderPolicyOptions();
}
function FAULT_BY_ID_UI(id){return faultCatalogue.find(f=>f.id===id);}
function renderAttemptHistory(attempts){
 const attempt=attempts[0];const status=document.getElementById("investigation-run-status"),progress=document.getElementById("attempt-progress"),table=document.getElementById("comparison-table"),timeline=document.getElementById("attempt-timeline");
 if(!attempt){status.textContent="Not started";progress.textContent="No attempt for this fault set yet.";table.innerHTML='<tr><td colspan="8" class="empty-cell">Attempt results will appear here.</td></tr>';timeline.innerHTML="";return;}
 status.textContent=attempt.status;progress.textContent=`Version ${attempt.revision} · ${attempt.phase} · ${attempt.error||"Results pending"}`;
 const rows=attempts.flatMap(a=>(a.policies||[]).map(id=>({attempt:a,policy:id,result:a.results?.[id]||{}})));
 table.innerHTML=rows.length?rows.map(({attempt:a,policy:id,result:r})=>`<tr><td>${escapeHtml(policiesInventory.find(p=>p.id===id)?.label||id)}<small class="table-subline">${escapeHtml(a.id)} · v${a.revision}</small></td><td>${escapeHtml(r.state||a.status)}</td><td>${escapeHtml(r.diagnosis||r.decision?.prediction||a.error||"No verified diagnosis")}${r.correct===true?" · correct":r.correct===false?" · incorrect":r.correct===null?" · not scored":""}</td><td>${r.confidence==null?"—":`${(Number(r.confidence)*100).toFixed(1)}%`}</td><td>${r.probes??"—"}</td><td>${r.credits??"—"}</td><td>${r.diagnosis_seconds==null?"—":`${Number(r.diagnosis_seconds).toFixed(2)} s`}</td><td>${escapeHtml(r.recovery||"—")}</td></tr>`).join(""):`<tr><td colspan="8" class="empty-cell">${escapeHtml(attempt.error||"No policies selected for this run.")}</td></tr>`;
 timeline.innerHTML=attempts.map(a=>`<details class="probe-policy"><summary>Attempt ${escapeHtml(a.id)} · fault-set v${a.revision} · ${escapeHtml(a.status)}</summary><div class="attempt-error">${escapeHtml(a.error||"No controller result has been recorded yet.")}</div>${Object.entries(a.results||{}).map(([policy,result])=>`<pre class="property-list">${escapeHtml(JSON.stringify({policy,...result},null,2))}</pre>`).join("")}</details>`).join("");
}
function openFaultPicker(){if(!currentRunId){notify("Start a run first, then add faults to it.");return;}const select=document.getElementById("add-fault-select");select.innerHTML=faultCatalogue.map(f=>`<option value="${escapeHtml(f.id)}" ${f.supported?"":"disabled"}>${escapeHtml(f.name)}${f.supported?"":" · unavailable"}</option>`).join("");const first=faultCatalogue.find(f=>f.supported);if(first)select.value=first.id;select.onchange=()=>updateFaultNote("add-fault-select","add-fault-note");updateFaultNote("add-fault-select","add-fault-note");document.getElementById("fault-picker-dialog").showModal();}
async function submitAddedFault(event){event.preventDefault();const faultId=document.getElementById("add-fault-select").value;try{const response=await fetch(`/api/runs/${encodeURIComponent(currentRunId)}/faults`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({fault_id:faultId})});const data=await response.json();if(!response.ok)throw new Error(data.detail||"Fault could not be added");document.getElementById("fault-picker-dialog").close();await loadRuns();await loadRunWorkspace(currentRunId);notify(`Fault added · investigation attempt ${data.attempt.id} created`);}catch(error){notify(error.message);}}
async function addFault(faultId){if(!currentRunId){openFaultPicker();return;}const response=await fetch(`/api/runs/${encodeURIComponent(currentRunId)}/faults`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({fault_id:faultId})});const data=await response.json();if(!response.ok){notify(data.detail||"Fault could not be added");return;}await loadRuns();await loadRunWorkspace(currentRunId);notify(`Fault added · investigation attempt ${data.attempt.id} created`);}
async function revertFault(instanceId){try{const response=await fetch(`/api/runs/${encodeURIComponent(currentRunId)}/faults/${encodeURIComponent(instanceId)}`,{method:"DELETE"});const data=await response.json();if(!response.ok)throw new Error(data.detail||"Fault could not be reverted");await loadRuns();await loadRunWorkspace(currentRunId);notify(`Fault reverted · new attempt ${data.attempt.id} created`);}catch(error){notify(error.message);}}
async function recoverFault(){if(!currentWorkspace||!currentRunId){notify("Select a run before reverting its faults.");return;}const active=(currentWorkspace.faults||[]).filter(f=>f.status==="active");if(!active.length){notify("This run has no active faults.");return;}for(const fault of active.slice().reverse()){const response=await fetch(`/api/runs/${encodeURIComponent(currentRunId)}/faults/${encodeURIComponent(fault.instance_id)}`,{method:"DELETE"});const data=await response.json();if(!response.ok){notify(data.detail||"A fault could not be reverted; remaining faults are still active.");await loadRunWorkspace(currentRunId);return;}}await loadRuns();await loadRunWorkspace(currentRunId);notify("All faults in this run were reverted.");}
async function loadRunChat(){if(!currentRunId)return;try{const response=await fetch(`/api/runs/${encodeURIComponent(currentRunId)}/chat`);if(response.ok){const data=await response.json();chatThreads[currentRunId]=data.messages||[];renderChatThread();}}catch(_){}}
function renderRuns(){const body=document.getElementById("runs-table");if(!body)return;if(!investigationRuns.length){body.innerHTML='<tr><td colspan="6" class="empty-cell">No runs yet. Start one when you’re ready.</td></tr>';return;}body.innerHTML=investigationRuns.map(r=>`<tr><td class="run-id">${escapeHtml(r.id)}</td><td>${new Date(r.created_at).toLocaleString()}</td><td>${(r.faults||[]).map(f=>escapeHtml(f.name)).join(", ")||"—"}</td><td>${(r.policies||[]).map(escapeHtml).join(", ")||"—"}</td><td><span class="state-pill ${r.status==="Resolved"?"resolved":""}">${escapeHtml(r.status)}</span></td><td><button class="table-open" onclick="openRun('${escapeHtml(r.id)}')">Open run</button></td></tr>`).join("");}
function renderRunList(){const list=document.getElementById("run-list");if(!list)return;const query=(document.getElementById("run-search")?.value||"").toLowerCase(),filter=document.getElementById("run-status-filter")?.value||"all";const rows=investigationRuns.filter(r=>(filter==="all"||r.status===filter)&&`${r.id} ${(r.faults||[]).map(f=>f.name).join(" ")}`.toLowerCase().includes(query));const count=document.getElementById("run-manager-count");if(count)count.textContent=String(rows.length);if(!rows.length){list.innerHTML='<div class="empty-cell">No matching runs.</div>';return;}list.innerHTML=rows.map(r=>`<button class="run-list-item ${r.id===currentRunId?"active":""}" onclick="openRun('${escapeHtml(r.id)}')"><strong>${escapeHtml(r.id)} · ${escapeHtml((r.faults||[])[0]?.name||"New investigation")}</strong><small>${new Date(r.created_at).toLocaleString()} · ${escapeHtml(r.status)}</small></button>`).join("");}
function renderChatThread() {
  const history=document.getElementById("chat-history");if(!history)return;
  const items=chatThreads[currentRunId]||[];
  if(!items.length){history.innerHTML='<div class="empty-state">Ask about this run, its fault path, or the evidence collected.</div>';return;}
  history.innerHTML=items.map(m=>`<div class="chat-bubble ${m.role}">${formatChat(m.text)}</div>`).join(""); history.scrollTop=history.scrollHeight;
}
function formatChat(text) { return escapeHtml(text).replace(/```([\s\S]*?)```/g,'<pre class="code-wrap"><button class="copy-code" onclick="copyCode(this)">Copy</button><code>$1</code></pre>').replace(/\[node:([a-zA-Z0-9_-]+)\]/g,'<span class="node-citation" role="button" tabindex="0" onclick="selectNode(\'$1\');showView(\'topology\')">$1</span>').replace(/\*\*(.*?)\*\*/g,"<strong>$1</strong>").replace(/`([^`]+)`/g,"<code>$1</code>"); }
async function copyCode(button){try{await navigator.clipboard.writeText(button.nextElementSibling.textContent);button.textContent="Copied";setTimeout(()=>button.textContent="Copy",1200);}catch(_){notify("Clipboard access is unavailable in this browser.");}}
function renderFaultCatalogue() {
 const host=document.getElementById("fault-catalogue");if(!host)return;
 host.innerHTML=faultCatalogue.map(f=>`<article class="fault-card ${f.supported?"":"fault-disabled"}"><div class="fault-card-head"><div><h3>${escapeHtml(f.name)}</h3><p>${escapeHtml(f.description)}</p></div><button class="button button-soft small" ${f.supported?`onclick="selectScenario('${escapeHtml(f.id)}')"`:"disabled"}>${f.supported?"Select":"Unavailable"}</button></div><div class="fault-meta"><span class="fault-group">${escapeHtml(f.group)}</span><span>${escapeHtml(f.target)}</span></div>${f.reason?`<small class="fault-reason">${escapeHtml(f.reason)}</small>`:""}</article>`).join("");
}
async function selectScenario(id){await startRun();const select=document.getElementById("initial-fault");if(select){select.value=id;updateFaultNote("initial-fault","initial-fault-note");}}
// WebSocket Connection & Real-time Dispatch
// ---------------------------------------------------------------------------
function initWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws`;

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    renderEnvironment(currentState?.sources||{});
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      currentState = data;
      renderAll(data);
    } catch (e) {
      console.error("[WS] Parse error:", e);
    }
  };

  ws.onclose = () => {
    const summary=document.getElementById("environment-summary");if(summary)summary.textContent="Live updates reconnecting…";
    setTimeout(initWebSocket, 2000);
  };

  ws.onerror = () => {};
}

function renderAll(state) {
  const graphNodes=liveTopology?.available?liveTopology.nodes:null,graphEdges=liveTopology?.available?liveTopology.edges:null;
  currentState=state;
  if(graphNodes&&document.getElementById("view-topology")?.classList.contains("active")){currentState.nodes=graphNodes;currentState.edges=graphEdges;}
  renderDashboard(state);
  renderFaultCatalogue();
  if(hasBuiltTopology) updateTopologyStates(currentState);
  renderEnvironment(state.sources||{});
}

function renderHUD(state) {
  renderEnvironment(state.sources||{});
}

function renderDashboard(state){
 const tel=state.telemetry||{}, active=(state.active_faults||[]).length;
 const up=document.getElementById("services-up");if(up)up.textContent=tel.services_up==null?"—":`${Math.round(tel.services_up)} / 7`;
 const upLabel=document.getElementById("service-health");if(upLabel){upLabel.textContent=tel.services_up==null?"No data":"Live";upLabel.className=`health-label ${tel.services_up==null?"":"good"}`;}
 const anomaly=document.getElementById("anomaly-count");if(anomaly)anomaly.textContent=String(active);
 const anomalyLabel=document.getElementById("anomaly-status");if(anomalyLabel)anomalyLabel.textContent=active?`${active} injected fault${active===1?"":"s"}`:"No injected faults";
 const anomalyHealth=document.getElementById("anomaly-health");if(anomalyHealth){anomalyHealth.textContent=active?"Active":"Clear";anomalyHealth.className=`health-label ${active?"warn":"good"}`;}
 const latency=document.getElementById("dash-latency");if(latency)latency.textContent=tel.latency_ms==null?"No data":`${Number(tel.latency_ms).toFixed(0)} ms`;
 const freshness=document.getElementById("metric-freshness");if(freshness){freshness.textContent=tel.last_updated?new Date(tel.last_updated).toLocaleTimeString():"No sample";freshness.className=`health-label ${tel.last_updated?"good":""}`;}
}

function renderEnvironment(sources){
 const names={docker:"Docker",neo4j:"Neo4j",prometheus:"Prometheus",loki:"Loki",sockshop:"Sock Shop"};
 const container=document.getElementById("environment-details");if(container)container.innerHTML=Object.entries(names).map(([key,label])=>{const source=sources[key]||{};const state=source.available?"Connected":"Unavailable";const time=source.last_success?` · ${new Date(source.last_success).toLocaleTimeString()}`:" · never checked";return `<div class="env-row"><span>${label}</span><span class="env-state ${source.available?"up":"down"}">${state}${time}</span></div>`;}).join("");
 const summary=document.getElementById("environment-summary");if(summary){const up=Object.keys(names).filter(k=>sources[k]?.available).length;summary.textContent=`${up} of 5 sources connected`;}
 const sourceList=document.getElementById("source-list");if(sourceList)sourceList.innerHTML=Object.entries(names).map(([key,label])=>{const row=sources[key]||{};return `<div class="source-row"><span>${label}</span><small>${row.last_success?new Date(row.last_success).toLocaleTimeString():"No recent check"}</small><i class="status-dot ${row.available?"dot-green":"dot-amber"}" title="${row.available?"Connected":escapeHtml(row.error||"Unavailable")}"></i></div>`;}).join("");
}
function toggleEnvironment(){const menu=document.getElementById("environment-menu"),button=document.querySelector(".environment-toggle");if(!menu)return;menu.hidden=!menu.hidden;button?.setAttribute("aria-expanded",String(!menu.hidden));}
function notify(message){let toast=document.getElementById("toast");if(!toast){toast=document.createElement("div");toast.id="toast";toast.className="toast";document.body.appendChild(toast);}toast.textContent=message;toast.classList.add("visible");clearTimeout(toast._timer);toast._timer=setTimeout(()=>toast.classList.remove("visible"),3800);}

function setTelemetryTab(tab){activeTelemetryTab=tab;const metrics=tab==="metrics";document.getElementById("metrics-tab")?.classList.toggle("selected",metrics);document.getElementById("logs-tab")?.classList.toggle("selected",!metrics);document.getElementById("metrics-tab")?.setAttribute("aria-selected",String(metrics));document.getElementById("logs-tab")?.setAttribute("aria-selected",String(!metrics));document.getElementById("metrics-panel").hidden=!metrics;document.getElementById("logs-panel").hidden=metrics;refreshTelemetry();}
function selectedServiceFilter(selectId){const value=document.getElementById(selectId)?.value||"affected";if(value==="all")return "all";const faults=currentState?.active_faults||[];const services=[...new Set(faults.flatMap(f=>f.impact_path||[f.target]).filter(Boolean))];return services.join(",")||"affected";}
function startTelemetryPolling(){stopTelemetryPolling();telemetryTimer=setInterval(()=>{if(activeTelemetryTab==="metrics"||!logsPaused)refreshTelemetry();},15000);workspaceRefreshTimer=setInterval(()=>{if(currentRunId)loadRunWorkspace(currentRunId);},2500);}
function stopTelemetryPolling(){if(telemetryTimer){clearInterval(telemetryTimer);telemetryTimer=null;}if(workspaceRefreshTimer){clearInterval(workspaceRefreshTimer);workspaceRefreshTimer=null;}}
function toggleLogPause(){logsPaused=!logsPaused;const button=document.getElementById("pause-logs");if(button)button.textContent=logsPaused?"Resume":"Pause";}
async function refreshTelemetry(){
 const badge=document.getElementById("telemetry-source-state");if(badge){badge.textContent="Refreshing…";badge.className="source-state";}
 if(currentRunId&&currentWorkspace?.live_lease!==currentRunId){const message="Historical telemetry was not captured for this run. Live signals are available only while it owns the lab.";if(activeTelemetryTab==="metrics")renderMetrics(null,message);else renderLogs(null,message);if(badge){badge.textContent="No run capture";badge.className="source-state";}return;}
 if(activeTelemetryTab==="metrics"){
  const seconds=Number(document.getElementById("metric-window")?.value||300),services=selectedServiceFilter("metric-service");
  if(services==="affected"){renderMetrics(null,"Inject a fault or choose All services to view baseline metrics.");if(badge){badge.textContent="Waiting for affected services";badge.className="source-state";}return;}
  try{const response=await fetch(`/api/telemetry/metrics?seconds=${seconds}&services=${encodeURIComponent(services)}`);const data=await response.json();if(!response.ok)throw new Error(data.detail||"Prometheus query failed");renderMetrics(data);if(badge){badge.textContent=data.available?"Prometheus · live":"Prometheus · no data";badge.className=`source-state ${data.available?"ok":"error"}`;}}
  catch(error){renderMetrics(null,error.message);if(badge){badge.textContent="Prometheus · unavailable";badge.className="source-state error";}}
 }else if(!logsPaused){
  const seconds=Number(document.getElementById("log-window")?.value||300),services=selectedServiceFilter("log-service"),severity=document.getElementById("log-severity")?.value||"all";
  if(services==="affected"){renderLogs(null,"Inject a fault or choose All services to view baseline logs.");if(badge){badge.textContent="Waiting for affected services";badge.className="source-state";}return;}
  try{const response=await fetch(`/api/telemetry/logs?seconds=${seconds}&services=${encodeURIComponent(services)}&severity=${severity}`);const data=await response.json();renderLogs(data);if(badge){badge.textContent=data.available?"Loki · live":"Loki · unavailable";badge.className=`source-state ${data.available?"ok":"error"}`;}}
  catch(error){renderLogs(null,error.message);if(badge){badge.textContent="Loki · unavailable";badge.className="source-state error";}}
 }
}
function renderMetrics(data,error){
 const charts=document.getElementById("metric-charts"),tiles={cpu:"stat-cpu",memory:"stat-mem",errors:"stat-5xx",up:"stat-up"};
 if(!data){charts.innerHTML=`<div class="empty-state">${escapeHtml(error||"Prometheus is unavailable")}</div>`;Object.values(tiles).forEach(id=>{const node=document.getElementById(id);if(node)node.textContent="Unavailable";});return;}
 charts.innerHTML="";
 const specs=[ ["cpu","CPU · %","alt"],["memory","Memory · MiB",""],["errors","HTTP 5XX · /s","alt"],["latency","Latency P99 · ms",""],["up","Prometheus targets up","alt"] ];
 const latest={};
 for(const [key,title,style] of specs){const series=data.series?.[key]||[];const all=series.flatMap(s=>s.values||[]);latest[key]=all.length?all[all.length-1][1]:null;charts.innerHTML+=`<article class="metric-chart"><h3>${title}</h3>${renderLineChart(series,style)}</article>`;}
 document.getElementById("stat-cpu").textContent=latest.cpu==null?"No data":`${latest.cpu.toFixed(1)}%`;
 document.getElementById("stat-mem").textContent=latest.memory==null?"No data":`${latest.memory.toFixed(1)} MiB`;
 document.getElementById("stat-5xx").textContent=latest.errors==null?"No data":`${latest.errors.toFixed(2)} /s`;
 document.getElementById("stat-up").textContent=latest.up==null?"No data":`${Math.round(latest.up)} / 7`;
 const updated=document.getElementById("metrics-updated");if(updated)updated.textContent=`Updated ${new Date(data.last_updated).toLocaleTimeString()}`;
}
function renderLineChart(series,style){const values=series.flatMap(s=>s.values||[]).map(point=>point[1]);if(!values.length)return '<div class="chart-empty">No samples in this window</div>';const min=Math.min(...values),max=Math.max(...values),range=max-min||1;const points=values.map((v,i)=>`${(i/Math.max(1,values.length-1))*300},${78-((v-min)/range)*62}`).join(" ");return `<svg viewBox="0 0 300 88" preserveAspectRatio="none" aria-label="Metric samples"><path class="chart-grid" d="M0 18H300 M0 48H300 M0 78H300"/><polyline class="chart-line ${style}" points="${points}"/></svg><small class="chart-range">${min.toPrecision(3)} — ${max.toPrecision(3)}</small>`;}
function renderLogs(data,error){const box=document.getElementById("logs-results");if(!data||!data.available){box.innerHTML=`<div class="empty-state">${escapeHtml(error||data?.error||"Loki is unavailable")}</div>`;return;}if(!data.logs.length){box.innerHTML='<div class="empty-state">No log entries in this time window.</div>';return;}box.innerHTML=data.logs.map(row=>`<article class="log-row"><time>${new Date(row.time).toLocaleTimeString()}</time><span class="log-service">${escapeHtml(row.service||"service")}</span><span class="log-level ${row.severity}">${escapeHtml(row.severity)}</span><span class="log-message">${escapeHtml(row.message)}</span></article>`).join("");}

async function loadLiveTopology(){
 const note=document.getElementById("topology-source-note");if(note)note.textContent="Reading the live graph from Neo4j…";
 try{const response=await fetch("/api/topology");const data=await response.json();liveTopology=data;
  if(!data.available){if(note)note.textContent=`Neo4j graph unavailable (${data.error||"connection failed"}). The live graph is not available in this environment.`;renderMiniTopology(null);return;}
  if(note)note.textContent=`Live Neo4j graph · ${data.nodes.length} nodes · ${data.edges.length} directed relationships · updated ${new Date(data.updated_at).toLocaleTimeString()}`;
  const mappedNodes=data.nodes.map(n=>({id:n.id,label:n.label||n.id,role:n.role||"Service",type:n.type,labels:n.labels,properties:n.properties}));
  const mappedEdges=data.edges.map(e=>({source:e.source,target:e.target,type:e.type,properties:e.properties}));
  liveTopology.nodes=mappedNodes;liveTopology.edges=mappedEdges;
  const graphStates=Object.fromEntries(mappedNodes.map(n=>[n.id,n.properties?.anomaly_flag?"anomalous":(["degraded","warning"].includes(String(n.properties?.status||"").toLowerCase())?"degraded":"healthy")]));
  if(currentState){currentState.nodes=mappedNodes;currentState.edges=mappedEdges;currentState.telemetry={...(currentState.telemetry||{}),node_states:graphStates};}else currentState={nodes:mappedNodes,edges:mappedEdges,telemetry:{node_states:graphStates},active_faults:[]};
  hasBuiltTopology=false;initTopology(currentState);updateNodeAndEdgePositions();renderGraphEdges(mappedEdges);renderMiniTopology(liveTopology);
 }catch(error){if(note)note.textContent=`Could not read the live Neo4j graph: ${error.message}`;}
}
function handleGraphPanelToggle(panel){if(panel.open)loadLiveTopology();}
function renderMiniTopology(graph){const host=document.getElementById("run-graph-snapshot");if(!host)return;if(!graph?.available){host.innerHTML='<div class="empty-state mini-graph-empty">Neo4j is unavailable. No graph data is shown.</div>';return;}const edges=graph.edges||[],nodes=graph.nodes||[];const lines=edges.map(e=>{const a=NODE_COORDINATES[e.source],b=NODE_COORDINATES[e.target];if(!a||!b)return"";return `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" class="mini-edge"><title>${escapeHtml(e.source)} → ${escapeHtml(e.target)} · ${escapeHtml(e.type)}</title></line>`;}).join("");const circles=nodes.map(n=>{const p=NODE_COORDINATES[n.id]||{x:410,y:210};return `<g class="mini-node" data-node="${escapeHtml(n.id)}" tabindex="0" role="button" aria-label="Inspect ${escapeHtml(n.label||n.id)}"><circle cx="${p.x}" cy="${p.y}" r="20"></circle><text x="${p.x}" y="${p.y+3}">${escapeHtml(n.label||n.id)}</text></g>`;}).join("");host.innerHTML=`<svg class="mini-graph-svg" viewBox="0 0 820 420" role="img" aria-label="Live Neo4j graph preview"><defs><marker id="mini-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4" markerHeight="4" orient="auto"><path d="M 0 1 L 9 5 L 0 9 z" fill="#c9c3d0"/></marker></defs><g class="mini-edges">${lines}</g><g class="mini-nodes">${circles}</g></svg><small class="mini-graph-summary">${nodes.length} nodes · ${edges.length} directed relationships</small>`;host.querySelectorAll(".mini-node").forEach(el=>{const open=()=>sendQuickPrompt(`Inspect [node:${el.dataset.node}] in the current run`);el.addEventListener("click",open);el.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();open();}});});}
function renderGraphEdges(edges){const host=document.getElementById("graph-edge-list"),count=document.getElementById("graph-edge-count");if(count)count.textContent=`${edges.length} edges`;if(!host)return;host.innerHTML=edges.map(e=>`<div class="graph-edge-row"><b>${escapeHtml(e.source)}</b><span>→ ${escapeHtml(e.target)}</span><small>${escapeHtml(e.type)}</small><code>${escapeHtml(JSON.stringify(e.properties||{}))}</code></div>`).join("")||'<div class="activity-empty">No directed relationships were returned.</div>';}

async function loadPolicies(){
 try{const response=await fetch("/api/policies");const data=await response.json();policiesInventory=data.policies||[];renderPolicyOptions();}
 catch(error){const host=document.getElementById("policy-options");if(host)host.innerHTML=`<div class="empty-state">Could not load policy availability: ${escapeHtml(error.message)}</div>`;}
}
function renderPolicyOptions(){const host=document.getElementById("policy-options");if(!host)return;const selected=new Set(investigationRuns.find(r=>r.id===currentRunId)?.policies||[]);host.innerHTML=policiesInventory.map(p=>`<span class="policy-tag ${p.available?"":"unavailable"}">${escapeHtml(p.label)}${selected.has(p.id)?" · selected":""}${p.available?"":" · unavailable"}</span>`).join("")||'<div class="empty-state">No policies listed.</div>';}

function setTelemetryDefaults(){document.getElementById("metric-service")?.addEventListener("change",refreshTelemetry);document.getElementById("log-service")?.addEventListener("change",refreshTelemetry);document.getElementById("log-severity")?.addEventListener("change",refreshTelemetry);document.getElementById("log-window")?.addEventListener("change",refreshTelemetry);}

function setDot(elementId, isUp) {
  const el = document.getElementById(elementId);
  if (!el) return;
  const dot = el.querySelector(".status-dot");
  if (dot) {
    dot.className = `status-dot ${isUp ? "dot-green" : "dot-red"}`;
  }
}

function renderChaosBar(state) {
  const activeContainer = document.getElementById("active-fault-container");
  const healthyBanner = document.getElementById("healthy-banner");
  const activeText = document.getElementById("active-fault-text");

  if (state.active_fault) {
    activeContainer.style.display = "block";
    healthyBanner.style.display = "none";
    activeText.textContent = `FAULT ACTIVE · ${state.active_fault} · ${state.fault_elapsed || "00:00"}`;
  } else {
    activeContainer.style.display = "none";
    healthyBanner.style.display = "flex";
  }
}

// ---------------------------------------------------------------------------
// Interactive Topology Canvas Engine (Pan, Zoom, Drag, Inspect)
// ---------------------------------------------------------------------------
let hasBuiltTopology = false;

function setupCanvasInteractions() {
  const container = document.getElementById("topology-canvas-container");
  const svg = document.getElementById("topology-svg");
  if (!container || !svg) return;

  // Background drag to pan
  container.addEventListener("mousedown", (e) => {
    if (e.target.closest(".node-group") || e.target.closest(".canvas-controls") || e.target.closest("#node-inspector")) {
      return;
    }
    canvasTransform.isPanning = true;
    canvasTransform.startX = e.clientX - canvasTransform.panX;
    canvasTransform.startY = e.clientY - canvasTransform.panY;
    container.style.cursor = "grabbing";
  });

  window.addEventListener("mousemove", (e) => {
    if (canvasTransform.isPanning) {
      canvasTransform.panX = e.clientX - canvasTransform.startX;
      canvasTransform.panY = e.clientY - canvasTransform.startY;
      applyCanvasTransform();
    } else if (draggingNode) {
      // Dragging a node
      const rect = svg.getBoundingClientRect();
      const mouseX = (e.clientX - rect.left - canvasTransform.panX) / canvasTransform.scale;
      const mouseY = (e.clientY - rect.top - canvasTransform.panY) / canvasTransform.scale;

      nodePositions[draggingNode] = {
        x: Math.round(mouseX - dragOffset.x),
        y: Math.round(mouseY - dragOffset.y)
      };
      updateNodeAndEdgePositions();
    }
  });

  window.addEventListener("mouseup", () => {
    if (canvasTransform.isPanning) {
      canvasTransform.isPanning = false;
      container.style.cursor = "grab";
    }
    if (draggingNode) {
      draggingNode = null;
    }
  });

  // Mouse wheel to zoom
  container.addEventListener("wheel", (e) => {
    e.preventDefault();
    const zoomFactor = e.deltaY < 0 ? 1.1 : 0.9;
    const newScale = Math.min(Math.max(0.4, canvasTransform.scale * zoomFactor), 2.5);

    const rect = svg.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    canvasTransform.panX = mouseX - (mouseX - canvasTransform.panX) * (newScale / canvasTransform.scale);
    canvasTransform.panY = mouseY - (mouseY - canvasTransform.panY) * (newScale / canvasTransform.scale);
    canvasTransform.scale = newScale;

    applyCanvasTransform();
  }, { passive: false });

  // Click on background deselects node
  svg.addEventListener("click", (e) => {
    if (!e.target.closest(".node-group")) {
      deselectNode();
    }
  });
}

function applyCanvasTransform() {
  const viewport = document.getElementById("topology-viewport");
  if (viewport) {
    viewport.setAttribute("transform", `translate(${canvasTransform.panX}, ${canvasTransform.panY}) scale(${canvasTransform.scale})`);
  }
}

function zoomIn() {
  canvasTransform.scale = Math.min(2.5, canvasTransform.scale * 1.2);
  applyCanvasTransform();
}

function zoomOut() {
  canvasTransform.scale = Math.max(0.4, canvasTransform.scale / 1.2);
  applyCanvasTransform();
}

function resetCanvasView() {
  canvasTransform.scale = 1.0;
  canvasTransform.panX = 0;
  canvasTransform.panY = 0;
  applyCanvasTransform();
}

function fitClusterView() {
  canvasTransform.scale = 0.95;
  canvasTransform.panX = 20;
  canvasTransform.panY = 10;
  applyCanvasTransform();
}

// ---------------------------------------------------------------------------
// Topology DOM Builder & Dynamic Updater
// ---------------------------------------------------------------------------
function edgeDomId(edge){const safe=value=>String(value||"").replace(/[^a-zA-Z0-9_-]/g,"_");return `edge-${safe(edge.source)}-${safe(edge.target)}-${safe(edge.type||"REL")}`;}
function initTopology(state) {
  const nodesLayer = document.getElementById("nodes-layer");
  const edgesLayer = document.getElementById("edges-layer");
  if (!nodesLayer || !edgesLayer) return;

  nodesLayer.innerHTML = "";
  edgesLayer.innerHTML = "";

  const nodes = state.nodes || [];
  const edges = state.edges || [];

  // Build Edges
  edges.forEach((edge, idx) => {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "line");
    path.setAttribute("id", edgeDomId(edge));
    path.setAttribute("class", "edge-path");
    path.setAttribute("marker-end", "url(#arrow-default)");
    path.dataset.source = edge.source;
    path.dataset.target = edge.target;
    edgesLayer.appendChild(path);
  });

  // Build Nodes
  nodes.forEach(node => {
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.setAttribute("id", `node-${node.id}`);
    g.setAttribute("class", "node-group");
    g.dataset.nodeId = node.id;
    g.setAttribute("tabindex", "0");
    g.setAttribute("role", "button");
    g.setAttribute("aria-label", `${node.label}, ${node.role || "service"}`);

    // Outer Circle
    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    circle.setAttribute("r", "22");
    circle.setAttribute("class", "node-circle healthy");
    circle.setAttribute("id", `circle-${node.id}`);

    // Node Label
    const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
    label.setAttribute("y", "3");
    label.setAttribute("class", "node-label");
    label.textContent = node.label;

    // Node Role / Type Subtext
    const role = document.createElementNS("http://www.w3.org/2000/svg", "text");
    role.setAttribute("y", "14");
    role.setAttribute("class", "node-role-label");
    role.textContent = node.role || "";

    g.appendChild(circle);
    g.appendChild(label);
    g.appendChild(role);

    // Node Drag & Click handlers
    g.addEventListener("mousedown", (e) => {
      e.stopPropagation();
      draggingNode = node.id;
      const rect = document.getElementById("topology-svg").getBoundingClientRect();
      const mouseX = (e.clientX - rect.left - canvasTransform.panX) / canvasTransform.scale;
      const mouseY = (e.clientY - rect.top - canvasTransform.panY) / canvasTransform.scale;
      const pos = nodePositions[node.id] || { x: 300, y: 200 };
      dragOffset = {
        x: mouseX - pos.x,
        y: mouseY - pos.y
      };
    });

    g.addEventListener("click", (e) => {
      e.stopPropagation();
      selectNode(node.id);
    });
    g.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectNode(node.id); }
    });

    nodesLayer.appendChild(g);
  });

  updateNodeAndEdgePositions();
  hasBuiltTopology = true;
}

function updateNodeAndEdgePositions() {
  // Update Node positions
  Object.keys(nodePositions).forEach(nodeId => {
    const g = document.getElementById(`node-${nodeId}`);
    const pos = nodePositions[nodeId];
    if (g && pos) {
      g.setAttribute("transform", `translate(${pos.x}, ${pos.y})`);
    }
  });

  // Update Edge endpoints
  const edges = currentState?.edges || [];
  edges.forEach(edge => {
    const line = document.getElementById(edgeDomId(edge));
    const sPos = nodePositions[edge.source];
    const tPos = nodePositions[edge.target];
    if (line && sPos && tPos) {
      line.setAttribute("x1", sPos.x);
      line.setAttribute("y1", sPos.y);
      line.setAttribute("x2", tPos.x);
      line.setAttribute("y2", tPos.y);
    }
  });
}

function updateTopologyStates(state) {
  if (!hasBuiltTopology) {
    initTopology(state);
  }

  const nodeStates = state.telemetry?.node_states || {};
  const isIsolated = showActiveSubgraph || state.subgraph_isolated;
  const activeTarget = state.fault_target;

  // Determine isolation visibility set
  let isolatedIds = null;
  if (isIsolated && ((state.active_faults||[]).length||state.active_fault)) {
    isolatedIds = new Set();
    const active=(state.active_faults||[]).length?state.active_faults:[{target:activeTarget,impact_path:[activeTarget]}];
    active.forEach(fault=>(fault.impact_path||[fault.target]).filter(Boolean).forEach(id=>isolatedIds.add(id)));
  }

  // Update Nodes
  (state.nodes || []).forEach(node => {
    const g = document.getElementById(`node-${node.id}`);
    const circle = document.getElementById(`circle-${node.id}`);
    if (!g || !circle) return;

    const status = nodeStates[node.id] || "healthy";
    const isSelected = selectedNodeId === node.id;
    const isVisibleInIsolated = !isolatedIds || isolatedIds.has(node.id);

    // Visibility
    g.style.display = isVisibleInIsolated ? "block" : "none";

    // Circle class
    circle.className.baseVal = `node-circle ${status} ${isSelected ? "selected" : ""}`;

    // Highlighting & Dimming
    if (selectedNodeId) {
      const isConnected = selectedNodeId === node.id || (state.edges||[]).some(edge=>(edge.source===selectedNodeId&&edge.target===node.id)||(edge.source===node.id&&edge.target===selectedNodeId));
      g.classList.toggle("dimmed", !isConnected);
    } else {
      g.classList.remove("dimmed");
    }
  });

  // Update Edges
  (state.edges || []).forEach(edge => {
    const line = document.getElementById(edgeDomId(edge));
    if (!line) return;

    const isVisibleInIsolated = !isolatedIds || (isolatedIds.has(edge.source) && isolatedIds.has(edge.target));
    line.style.display = isVisibleInIsolated ? "block" : "none";

    const isPropagationEdge = (
      (edge.source === "front-end" && (edge.target === "orders" || edge.target === "catalogue" || edge.target === "carts")) ||
      (edge.source === "orders" && edge.target === activeTarget) ||
      (edge.source === "catalogue" && edge.target === activeTarget)
    ) && state.active_fault;

    const isConnectedToSelected = selectedNodeId && (edge.source === selectedNodeId || edge.target === selectedNodeId);

    if (isPropagationEdge) {
      line.setAttribute("class", "edge-path active-path");
      line.setAttribute("marker-end", "url(#arrow-active)");
    } else if (isConnectedToSelected) {
      line.setAttribute("class", "edge-path highlighted");
      line.setAttribute("marker-end", "url(#arrow-highlighted)");
    } else {
      line.setAttribute("class", selectedNodeId ? "edge-path dimmed" : "edge-path");
      line.setAttribute("marker-end", "url(#arrow-default)");
    }
  });

  // Update toggle button text
  const btn = document.getElementById("subgraph-btn");
  if (btn) {
    btn.textContent = isIsolated ? "SUBGRAPH: ISOLATED (ACTIVE PATH)" : "SUBGRAPH: FULL CLUSTER (13)";
    btn.className = isIsolated ? "btn-wire active" : "btn-wire";
  }

  // Update Inspector if open
  if (selectedNodeId) {
    updateInspectorData(selectedNodeId, nodeStates[selectedNodeId] || "healthy");
  }
}

// ---------------------------------------------------------------------------
// Node Selection & Inspector Drawer
// ---------------------------------------------------------------------------
function selectNode(nodeId) {
  selectedNodeId = nodeId;
  const inspector = document.getElementById("node-inspector");
  if (inspector) {
    inspector.style.display = "flex";
  }
  if (currentState) {
    updateTopologyStates(currentState);
  }
}

function deselectNode() {
  selectedNodeId = null;
  const inspector = document.getElementById("node-inspector");
  if (inspector) {
    inspector.style.display = "none";
  }
  if (currentState) {
    updateTopologyStates(currentState);
  }
}

function closeNodeInspector() {
  deselectNode();
}

function updateInspectorData(nodeId, status) {
  const node=liveTopology?.nodes?.find(n=>n.id===nodeId)||currentState?.nodes?.find(n=>n.id===nodeId)||{};
  const outgoing=(liveTopology?.edges||[]).filter(e=>e.source===nodeId),incoming=(liveTopology?.edges||[]).filter(e=>e.target===nodeId);
  document.getElementById("inspector-name").textContent=node.label||nodeId;
  document.getElementById("inspector-role").textContent=(node.labels||[]).join(" · ")||node.role||"—";
  document.getElementById("inspector-upstream").textContent=incoming.map(e=>`${e.source} · ${e.type}`).join(", ")||"None";
  document.getElementById("inspector-downstream").textContent=outgoing.map(e=>`${e.type} · ${e.target}`).join(", ")||"None";
  const props=document.getElementById("inspector-properties");if(props)props.textContent=JSON.stringify(node.properties||{},null,2);

  const badge = document.getElementById("inspector-badge");
  const badgeText = document.getElementById("inspector-status-text");
  if (badge && badgeText) {
    badge.className = `inspector-badge ${status}`;
    badgeText.textContent = status.toUpperCase();
  }
}

function inspectorProbeNode() {
  if (!selectedNodeId) return;
  const traceBox = document.getElementById("rl-trace-container");
  if (traceBox) {
    traceBox.innerHTML += `
      <div class="trace-line">
        <span class="trace-step">[*]</span>
        <span class="trace-action" style="color:var(--accent-cyan);">DIRECT PROBE [${selectedNodeId}.status]</span>
        <span class="trace-outcome ${currentState?.telemetry?.node_states?.[selectedNodeId] || 'ok'}">VERIFIED</span>
      </div>
    `;
    traceBox.scrollTop = traceBox.scrollHeight;
  }
}

function inspectorChatNode() {
  if (!selectedNodeId) return;
  sendQuickPrompt(`Inspect health, dependencies and blast radius for [node:${selectedNodeId}]`);
}

function inspectorFocusNode() {
  if (!selectedNodeId) return;
  const pos = nodePositions[selectedNodeId];
  if (pos) {
    canvasTransform.scale = 1.3;
    canvasTransform.panX = 350 - pos.x * 1.3;
    canvasTransform.panY = 180 - pos.y * 1.3;
    applyCanvasTransform();
  }
}

async function toggleSubgraph() {
  try {
    const res = await fetch("/api/topology/toggle_subgraph", { method: "POST" });
    const data = await res.json();
    if (currentState) {
      currentState.subgraph_isolated = data.subgraph_isolated;
      updateTopologyStates(currentState);
    }
  } catch (e) {
    console.error("Toggle subgraph error:", e);
  }
}

// ---------------------------------------------------------------------------
// Fault Injection & Recovery
// ---------------------------------------------------------------------------
async function injectFault(faultId) {
  return addFault(faultId);
}

async function recoverFault() {
  if(!currentWorkspace||!currentRunId){notify("Select a run before reverting its faults.");return;}
  const active=(currentWorkspace.faults||[]).filter(f=>f.status==="active");if(!active.length){notify("This run has no active faults.");return;}
  for(const fault of active.slice().reverse()){const response=await fetch(`/api/runs/${encodeURIComponent(currentRunId)}/faults/${encodeURIComponent(fault.instance_id)}`,{method:"DELETE"});const data=await response.json();if(!response.ok){notify(data.detail||"A fault could not be reverted; remaining faults are still active.");await loadRunWorkspace(currentRunId);return;}}
  await loadRuns();await loadRunWorkspace(currentRunId);notify("All faults in this run were reverted.");
}

// ---------------------------------------------------------------------------
// Operator Chat (Gemini 3.5 Flash Lite)
// ---------------------------------------------------------------------------
async function handleChatSubmit(event) {
  event.preventDefault();
  const input = document.getElementById("chat-input");
  const msg = input.value.trim();
  if (!msg) return;
  if(!currentRunId) await startRun();
  if(!currentRunId)return;
  const runId=currentRunId;chatThreads[runId] ||= [];
  chatThreads[runId].push({role:"user",text:msg}); renderChatThread();
  input.value = "";

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: msg, run_id: runId })
    });
    const data = await res.json();if(!res.ok)throw new Error(data.detail||"Chat request failed");
    await loadRunChat();
  } catch (e) {
    chatThreads[runId].push({role:"assistant",text:`Could not get a response: ${e.message}`});renderChatThread();
  }
}

function sendQuickPrompt(promptText) {
  const input = document.getElementById("chat-input");
  if (!input) return;
  input.value = promptText;
  handleChatSubmit(new Event("submit"));
}

function appendChat(role, text) {
  const history = document.getElementById("chat-history");
  if (!history) return;

  const bubble = document.createElement("div");
  bubble.className = `chat-bubble ${role}`;

  // Format node citations [node:name]
  const formattedText = text.replace(/\[node:([a-zA-Z0-9_-]+)\]/g, (match, name) => {
    return `<span class="node-citation" onclick="selectNode('${name}')">${name}</span>`;
  });

  bubble.innerHTML = formattedText;
  history.appendChild(bubble);
  history.scrollTop = history.scrollHeight;
}

function setSubgraph(active) {
  showActiveSubgraph=active;
  document.getElementById("full-graph")?.classList.toggle("selected",!active);
  document.getElementById("active-graph")?.classList.toggle("selected",active);
  if(currentState){currentState.subgraph_isolated=active;updateTopologyStates(currentState);}
}

function escapeHtml(text) {
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
  return String(text).replace(/[&<>"']/g, m => map[m]);
}

// ---------------------------------------------------------------------------
// Initialization on Load
// ---------------------------------------------------------------------------
window.addEventListener("DOMContentLoaded", () => {
  loadInvestigationData();
  loadFaultCatalogue();loadPolicies();loadRuns();
  setTelemetryDefaults();
  setupCanvasInteractions();
  initWebSocket();
});
