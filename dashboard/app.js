const STAGES = [
  "define_market", "discover", "resolve_entities", "cheap_filter", "business_fit",
  "agency_fit", "digital_gap", "deep_audit", "commercial_opportunity", "dealability",
  "evidence_verification", "red_team", "score_survivors", "rank", "portfolio_selection",
  "human_approval", "redesign_intelligence",
];

const STAGE_LABELS = {
  define_market: "Định nghĩa thị trường",
  discover: "Tìm lead",
  resolve_entities: "Chuẩn hóa entity",
  cheap_filter: "Lọc nhanh",
  business_fit: "Độ mạnh business",
  agency_fit: "Độ hợp agency",
  digital_gap: "Khoảng trống số",
  deep_audit: "Audit sâu",
  commercial_opportunity: "Cơ hội thương mại",
  dealability: "Khả năng tiếp cận",
  evidence_verification: "Xác minh evidence",
  red_team: "Red team",
  score_survivors: "Chấm survivor",
  rank: "Xếp hạng",
  portfolio_selection: "Chọn portfolio",
  human_approval: "Minh / Wien duyệt",
  redesign_intelligence: "Redesign intelligence",
};

const ROLE_NAMES = {
  orchestrator: "An Minh",
  "market-intelligence": "Ngọc Hân",
  "discovery-scout": "Gia Linh",
  "entity-resolver": "Khánh An",
  "business-strength": "Đức Minh",
  "agency-fit": "Thanh Vy",
  "fast-web-screener": "Yến Nhi",
  "technical-auditor": "Hoàng Nam",
  "ux-conversion-auditor": "Mai Anh",
  "commercial-opportunity": "Tuệ Lâm",
  dealability: "Bảo Ngọc",
  "evidence-verifier": "Nhật Minh",
  "red-team": "Hải Yến",
  "lead-ranker": "Minh Châu",
  "portfolio-selector": "Quỳnh Anh",
  "dossier-writer": "Thảo My",
  "redesign-intelligence": "Yến My",
};

const ROLE_ORDER = [
  "orchestrator", "market-intelligence", "discovery-scout", "entity-resolver",
  "business-strength", "agency-fit", "fast-web-screener", "technical-auditor",
  "ux-conversion-auditor", "commercial-opportunity", "dealability", "evidence-verifier",
  "red-team", "lead-ranker", "portfolio-selector", "dossier-writer", "redesign-intelligence",
];

const STAGE_OWNERS = {
  define_market: "market-intelligence",
  discover: "discovery-scout",
  resolve_entities: "entity-resolver",
  cheap_filter: "fast-web-screener",
  business_fit: "business-strength",
  agency_fit: "agency-fit",
  digital_gap: "ux-conversion-auditor",
  deep_audit: "technical-auditor",
  commercial_opportunity: "commercial-opportunity",
  dealability: "dealability",
  evidence_verification: "evidence-verifier",
  red_team: "red-team",
  score_survivors: "lead-ranker",
  rank: "lead-ranker",
  portfolio_selection: "portfolio-selector",
  human_approval: "dossier-writer",
  redesign_intelligence: "redesign-intelligence",
};

const FUNNEL = [
  ["discovered", "Đã phát hiện"],
  ["resolved", "Đã chuẩn hóa"],
  ["cheap-filtered", "Lọc nhanh"],
  ["gate-survivors", "Qua gate"],
  ["deep-audited", "Audit sâu"],
  ["ranked", "Đã xếp hạng"],
  ["portfolio", "Portfolio"],
  ["human-approved", "Đã duyệt"],
];

const SCORE_FIELDS = [
  ["BusinessStrength", "Business"],
  ["AgencyFit", "Agency fit"],
  ["DigitalGap", "Digital gap"],
  ["ConversionGap", "Conversion gap"],
  ["UXGap", "UX gap"],
  ["TrustGap", "Trust gap"],
  ["CommercialOpportunity", "Opportunity"],
  ["Dealability", "Dealability"],
];

const STATUS_LABELS = {
  healthy: "Healthy",
  partial: "Partial",
  blocked: "Blocked",
  failed_terminal: "Failed terminal",
  "failed-terminal": "Failed terminal",
  failed_retryable: "Retryable failure",
  "failed-retryable": "Retryable failure",
  running: "Đang chạy",
  complete: "Hoàn tất",
  completed: "Đã hoàn tất",
  approved: "Đã duyệt",
  rejected: "Đã từ chối",
  review: "Đang review",
};

const ACTION_LABELS = {
  "select-lead": "Chọn lead",
  "watch-lead": "Theo dõi",
  "lead-approve": "Duyệt lead",
  "lead-request-change": "Yêu cầu sửa",
  "lead-reject": "Từ chối",
  "page-status": "Xem status",
  "page-done": "Đánh dấu xong",
  "page-approve": "Duyệt page",
  block: "Block",
  "final-confirm": "Xác nhận cuối",
};

const ACTOR_NAMES = {
  "620891893659598850": "Minh",
  "859783610625556480": "Wien",
};

const REASON_ACTIONS = new Set(["lead-reject", "lead-request-change", "block"]);

const state = {
  snapshot: null,
  token: "",
  pendingAction: null,
  pollDelay: 15000,
  timer: null,
};

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatTime(value) {
  if (!value) return "N/A";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? escapeHtml(value)
    : date.toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" });
}

function labelForStatus(value) {
  return STATUS_LABELS[value] || value || "N/A";
}

function labelForStage(value) {
  const stage = String(value || "").split(":", 1)[0];
  return STAGE_LABELS[stage] || stage || "N/A";
}

function ownerForStage(stage, row) {
  const roleId = row?.producer_role || STAGE_OWNERS[stage];
  return ROLE_NAMES[roleId] || roleId || "N/A";
}

function setBadge(kind, text) {
  const badge = $("#freshness-badge");
  badge.className = `status-badge status-${kind}`;
  badge.textContent = text;
}

function showError(message) {
  const banner = $("#error-banner");
  banner.textContent = message;
  banner.hidden = !message;
}

function showFeedback(message) {
  const banner = $("#action-feedback");
  banner.textContent = message;
  banner.hidden = !message;
}

function metricValue(value) {
  return Number.isFinite(value) ? String(value) : "N/A";
}

function currentStageIndex(snapshot) {
  const rawStage = snapshot?.current_stage;
  const stage = String(rawStage || "").split(":", 1)[0];
  const direct = STAGES.indexOf(stage);
  if (direct >= 0) return direct;
  const stageRows = snapshot?.current_run?.stages ?? [];
  const current = stageRows.find((row) => row.status === "running");
  return current ? STAGES.indexOf(String(current.stage_name || "").split(":", 1)[0]) : -1;
}

function renderMetrics(snapshot) {
  const counts = snapshot?.funnel ?? {};
  const run = snapshot?.current_run;
  $("#metric-run").textContent = labelForStatus(run?.status);
  $("#metric-run-id").textContent = run?.run_id ?? "N/A";
  $("#metric-discovered").textContent = metricValue(counts.discovered);
  $("#metric-survivors").textContent = metricValue(counts["gate-survivors"]);
  $("#metric-approved").textContent = metricValue(counts["human-approved"]);
}

function renderStageRail(snapshot) {
  const current = currentStageIndex(snapshot);
  const stageRows = snapshot?.current_run?.stages ?? [];
  $("#stage-count").textContent = `${current < 0 ? 0 : current + 1} / ${STAGES.length}`;
  $("#stage-rail").innerHTML = STAGES.map((stage, index) => {
    const row = stageRows.find((item) => item.stage_name === stage || item.stage_id?.startsWith(`${stage}:`));
    const done = row?.status === "complete" || (current >= 0 && index < current);
    const active = index === current;
    const className = done ? "done" : active ? "current" : "";
    const owner = ownerForStage(stage, row);
    return `<li class="stage-item"><button class="stage-button ${className}" type="button" disabled aria-current="${active ? "step" : "false"}">
      <span class="stage-index">${String(index + 1).padStart(2, "0")} / ${escapeHtml(stage)}</span>
      <span class="stage-name">${escapeHtml(STAGE_LABELS[stage])}</span>
      <span class="stage-owner">${escapeHtml(owner)}</span>
    </button></li>`;
  }).join("");
}

function renderRun(snapshot) {
  const run = snapshot?.current_run;
  const index = currentStageIndex(snapshot);
  const currentStage = snapshot?.current_stage || run?.current_stage;
  const currentStageKey = String(currentStage || "").split(":", 1)[0];
  const stageRows = run?.stages ?? [];
  const currentRow = stageRows.find((row) => row.stage_name === currentStageKey || row.stage_id?.startsWith(`${currentStageKey}:`));
  const currentAgent = ownerForStage(currentStageKey, currentRow);
  const status = snapshot?.service_status || "unavailable";
  $("#run-status").textContent = labelForStatus(run?.status);
  $("#run-state").textContent = labelForStatus(run?.status);
  $("#run-id").textContent = run?.run_id ?? "N/A";
  $("#current-stage").textContent = labelForStage(currentStage);
  $("#current-agent").textContent = currentAgent;
  $("#evaluated-count").textContent = run ? metricValue(run.evaluated_candidates) : "N/A";
  $("#run-progress").style.width = `${index < 0 ? 0 : ((index + 1) / STAGES.length) * 100}%`;
  $("#run-progress-label").textContent = `${index < 0 ? 0 : index + 1} / ${STAGES.length} stage`;
  $("#service-status").textContent = labelForStatus(status);
  $("#signal-status").textContent = snapshot?.blocked ? "Blocked" : snapshot?.partial ? "Partial" : "Read model";
  $("#signal-copy").textContent = snapshot?.error
    || (snapshot?.partial ? "Run đang partial; không có lead giả được bổ sung." : "Workflow database đang được theo dõi live.");
  $("#last-updated").textContent = snapshot?.generated_at ? `Cập nhật ${formatTime(snapshot.generated_at)}` : "Chưa có snapshot";
  const dot = $(".signal-dot");
  dot.className = `signal-dot ${snapshot?.blocked || snapshot?.error ? "error" : snapshot?.partial ? "" : "ok"}`;
}

function renderFunnel(snapshot) {
  const values = snapshot?.funnel ?? {};
  const numeric = FUNNEL.map(([key]) => values[key]).filter((value) => Number.isFinite(value));
  const max = numeric.length ? Math.max(...numeric, 1) : 1;
  $("#funnel").innerHTML = FUNNEL.map(([key, label]) => {
    const value = values[key];
    const known = Number.isFinite(value);
    const width = known && value > 0 ? Math.max(3, (value / max) * 100) : 0;
    return `<div class="funnel-row"><span class="funnel-label">${label}</span><span class="funnel-bar"><span style="width:${width}%"></span></span><strong class="funnel-value">${known ? value : "N/A"}</strong></div>`;
  }).join("");
}

function verdictClass(verdict) {
  if (verdict === "survive") return "tag-positive";
  if (verdict === "reject") return "tag-danger";
  return "tag-warning";
}

function actionButton(action, label, lead, extraClass = "", options = {}) {
  const targetId = options.targetId ?? lead.entry_id;
  const stateVersion = options.stateVersion ?? lead.state_version;
  const pageSlug = options.pageSlug ?? "";
  return `<button class="button ${extraClass || "button-quiet"}" type="button" data-action="${escapeHtml(action)}" data-entry-id="${escapeHtml(lead.entry_id)}" data-target="${escapeHtml(targetId)}" data-page-slug="${escapeHtml(pageSlug)}" data-version="${stateVersion}">${escapeHtml(label)}</button>`;
}

function renderLead(lead) {
  const scores = lead.score_breakdown ?? {};
  const scoreRows = SCORE_FIELDS.map(([key, label]) => [label, scores[key]]);
  const issues = (lead.top_issues ?? []).slice(0, 3).map((issue) => `<span class="tag">${escapeHtml(issue)}</span>`).join("");
  const url = lead.website_url
    ? `<a class="lead-url" href="${escapeHtml(lead.website_url)}" target="_blank" rel="noreferrer">${escapeHtml(lead.website_url)}</a>`
    : `<span class="lead-url">URL unavailable</span>`;
  const contact = lead.public_contact
    ? `<div class="lead-contact"><span>Public contact</span><strong>${escapeHtml(lead.public_contact)}</strong></div>`
    : "";
  const screenshot = lead.screenshot_url
    ? `<img class="lead-screenshot" src="${escapeHtml(lead.screenshot_url)}" alt="Screenshot ${escapeHtml(lead.company_name)}" loading="lazy" />`
    : "";
  const pageControls = renderPageControls(lead);
  const finalAction = lead.project_id && Number.isInteger(lead.project_state_version)
    ? actionButton("final-confirm", ACTION_LABELS["final-confirm"], lead, "button-accent", { targetId: lead.project_id, stateVersion: lead.project_state_version })
    : "";
  return `<article class="lead-card" data-entry-id="${escapeHtml(lead.entry_id)}">
    <div class="lead-card-top"><span class="lead-rank">#${lead.rank}</span><span class="tag ${verdictClass(lead.red_team_verdict)}">${escapeHtml(lead.red_team_verdict)}</span></div>
    <div class="lead-title-row"><h3 class="lead-title" title="${escapeHtml(lead.company_name)}">${escapeHtml(lead.company_name)}</h3><span class="state-chip">${escapeHtml(lead.state)}</span></div>
    ${url}
    ${contact}
    ${screenshot}
    <div class="lead-score-grid">${scoreRows.map(([name, value]) => `<div class="score-line"><span>${name}</span><span class="score-meter"><span style="width:${Number.isFinite(value) ? value : 0}%"></span></span><strong>${Number.isFinite(value) ? value : "N/A"}</strong></div>`).join("")}
      <div class="score-line"><span>Evidence</span><span class="score-meter"><span style="width:${Number.isFinite(lead.evidence_confidence) ? lead.evidence_confidence * 100 : 0}%"></span></span><strong>${Number.isFinite(lead.evidence_confidence) ? `${Math.round(lead.evidence_confidence * 100)}%` : "N/A"}</strong></div>
    </div>
    <div class="lead-tags">${issues || `<span class="tag">No issue snapshot</span>`}</div>
    <div class="lead-actions">${actionButton("select-lead", ACTION_LABELS["select-lead"], lead)}${actionButton("watch-lead", ACTION_LABELS["watch-lead"], lead)}${actionButton("lead-approve", ACTION_LABELS["lead-approve"], lead, "button-accent")}</div>
    <div class="lead-actions">${actionButton("lead-request-change", ACTION_LABELS["lead-request-change"], lead)}${actionButton("lead-reject", ACTION_LABELS["lead-reject"], lead, "button-danger")}</div>
    ${pageControls}
    ${finalAction ? `<div class="lead-actions final-action">${finalAction}</div>` : ""}
  </article>`;
}

function renderPageControls(lead) {
  if (!lead.project_id || !Number.isInteger(lead.project_state_version)) return "";
  const pages = lead.pages ?? [];
  if (!pages.length) return `<div class="page-controls"><div class="page-heading"><span>Page actions</span><span class="tag">No page snapshot</span></div></div>`;
  return `<div class="page-controls"><div class="page-heading"><span>Page actions</span><code>${escapeHtml(lead.project_id)} · v${lead.project_state_version}</code></div>${pages.map((page) => {
    const options = { targetId: lead.project_id, pageSlug: page.page_slug, stateVersion: lead.project_state_version };
    return `<div class="page-row"><div class="page-info"><strong>${escapeHtml(page.page_slug)}</strong><span>${escapeHtml(page.status)}${page.assigned_actor ? ` · assigned: ${escapeHtml(page.assigned_actor)}` : ""}</span></div><div class="page-actions">${actionButton("page-status", ACTION_LABELS["page-status"], lead, "button-quiet", options)}${actionButton("page-done", ACTION_LABELS["page-done"], lead, "button-quiet", options)}${actionButton("page-approve", ACTION_LABELS["page-approve"], lead, "button-quiet", options)}${actionButton("block", ACTION_LABELS.block, lead, "button-danger", options)}</div></div>`;
  }).join("")}</div>`;
}

function renderPortfolio(snapshot) {
  const leads = snapshot?.portfolio ?? [];
  $("#portfolio-count").textContent = `${leads.length} lead${leads.length === 1 ? "" : "s"}`;
  $("#portfolio-health").textContent = snapshot?.partial ? "Partial" : leads.length ? "Human action" : "Chờ dữ liệu";
  $("#portfolio").innerHTML = leads.length
    ? leads.map(renderLead).join("")
    : `<p class="empty-state">Chưa có portfolio defensible. Khi evidence chưa đủ, hệ thống giữ trạng thái trống thay vì dựng lead giả.</p>`;
}

function renderEvents(snapshot) {
  const events = snapshot?.events ?? [];
  $("#events").innerHTML = events.length
    ? events.slice(0, 12).map((event) => {
      const action = ACTION_LABELS[event.action] || event.action || event.event_type;
      const actor = ACTOR_NAMES[event.actor] || event.actor;
      return `<div class="event-row"><span class="event-dot"></span><div><div class="event-copy"><strong class="event-title">${escapeHtml(action)}</strong><span class="microcopy">${formatTime(event.created_at)}</span></div><p class="event-meta">${escapeHtml(event.target_id)}${actor ? ` · ${escapeHtml(actor)}` : ""}${event.status ? ` · ${escapeHtml(event.status)}` : ""}</p></div></div>`;
    }).join("")
    : `<p class="empty-state">Chưa có event nào trong read model.</p>`;
}

function renderRoster() {
  $("#agent-roster").innerHTML = ROLE_ORDER.map((role, index) => `<div class="agent-row"><span class="agent-index">${String(index + 1).padStart(2, "0")}</span><strong>${escapeHtml(ROLE_NAMES[role])}</strong><code>${escapeHtml(role)}</code></div>`).join("");
}

function render(snapshot) {
  renderMetrics(snapshot);
  renderRun(snapshot);
  renderStageRail(snapshot);
  renderFunnel(snapshot);
  renderPortfolio(snapshot);
  renderEvents(snapshot);
  renderRoster();
  const stale = Boolean(snapshot?.stale);
  $("#stale-banner").hidden = !stale;
  setBadge(stale ? "stale" : snapshot?.error ? "error" : "fresh", stale ? "Stale" : snapshot?.error ? "Lỗi API" : "Live");
}

async function getSnapshot() {
  const response = await fetch("/api/v1/dashboard", { cache: "no-store", headers: { Accept: "application/json" } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.message_vi || "Không đọc được dashboard snapshot.");
  return body;
}

async function refresh() {
  try {
    const snapshot = await getSnapshot();
    state.snapshot = snapshot;
    state.pollDelay = 15000;
    showError("");
    render(snapshot);
  } catch (error) {
    state.pollDelay = Math.min(state.pollDelay * 2, 120000);
    if (state.snapshot) {
      state.snapshot = { ...state.snapshot, stale: true, freshness: "stale" };
      render(state.snapshot);
    } else {
      setBadge("error", "Unavailable");
      showError(error.message || "Dashboard API unavailable.");
    }
  } finally {
    window.clearTimeout(state.timer);
    state.timer = window.setTimeout(refresh, state.pollDelay);
  }
}

function idempotencyKey() {
  return `dashboard-${crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`}`;
}

function findLead(targetId) {
  return (state.snapshot?.portfolio ?? []).find((lead) => lead.entry_id === targetId);
}

function openAction(action, lead, button) {
  state.pendingAction = {
    action,
    targetId: button.dataset.target || lead.entry_id,
    pageSlug: button.dataset.pageSlug || null,
    expectedStateVersion: Number(button.dataset.version ?? lead.state_version),
  };
  $("#dialog-title").textContent = ACTION_LABELS[action] || action;
  const pageText = state.pendingAction.pageSlug ? ` · page ${state.pendingAction.pageSlug}` : "";
  $("#dialog-copy").textContent = `${lead.company_name}${pageText} · target ${state.pendingAction.targetId} · version ${state.pendingAction.expectedStateVersion}. Server sẽ xác định actor canonical từ token và coordinator sẽ kiểm tra quyền.`;
  $("#reason-input").value = "";
  $("#action-dialog").showModal();
}

async function confirmAction() {
  const pending = state.pendingAction;
  if (!pending) return;
  if (!state.token) {
    showError("Nhập dashboard token trong phiên hiện tại trước khi gửi action.");
    $("#action-dialog").close();
    return;
  }
  const reason = $("#reason-input").value.trim();
  if (REASON_ACTIONS.has(pending.action) && !reason) {
    $("#reason-input").focus();
    return;
  }
  const button = $("#confirm-action");
  button.disabled = true;
  try {
    const response = await fetch("/api/v1/actions", {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json", Accept: "application/json", Authorization: `Bearer ${state.token}` },
      body: JSON.stringify({ action: pending.action, target_id: pending.targetId, page_slug: pending.pageSlug, reason: reason || null, expected_state_version: pending.expectedStateVersion, idempotency_key: idempotencyKey() }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.message_vi || "Action thất bại.");
    $("#action-dialog").close();
    showError("");
    showFeedback(`${body.message_vi || "Action đã được tiếp nhận."} · event ${body.event_id} · state ${body.state}`);
    await refresh();
  } catch (error) {
    showError(error.message || "Không gửi được action.");
  } finally {
    button.disabled = false;
  }
}

$("#refresh-button").addEventListener("click", refresh);
$("#clear-token").addEventListener("click", () => {
  state.token = "";
  $("#token-input").value = "";
  $("#permission-copy").textContent = "Đã xoá token khỏi memory của tab.";
  showFeedback("");
});
$("#token-input").addEventListener("input", (event) => {
  state.token = event.target.value;
  $("#permission-copy").textContent = state.token ? "Token đã nhập; actor/quyền sẽ do server xác định." : "Chưa xác thực actor.";
});
$("#portfolio").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const lead = findLead(button.dataset.entryId);
  if (lead) openAction(button.dataset.action, lead, button);
});
$("#confirm-action").addEventListener("click", confirmAction);
$("#action-dialog").addEventListener("close", () => { state.pendingAction = null; });

render({ service_status: "connecting", stage_rail: STAGES, funnel: {}, portfolio: [], events: [], freshness: "unknown" });
refresh();
