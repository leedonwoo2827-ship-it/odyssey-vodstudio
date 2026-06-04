// 영상공방 (VOD Studio) frontend — drives the NotebookLM → 슬라이드/대본 → 번들 flow.
const $ = (id) => document.getElementById(id);
const API = "/api/vodstudio";
let currentJobId = null;
let pollTimer = null;

async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  let data = null;
  try { data = await res.json(); } catch (e) {}
  if (!res.ok) throw new Error((data && data.detail) || `HTTP ${res.status}`);
  return data;
}

// ---- NotebookLM auth (non-blocking; only needed for steps 1–2) ----
async function checkAuth() {
  const badge = $("nlmBadge");
  let authed = false;
  try {
    const d = await api("/auth");
    authed = !!d.authenticated;
  } catch (e) {}
  if (authed) {
    badge.textContent = "NotebookLM: 연결됨"; badge.className = "badge ok";
    $("nlmStatusLine").textContent = "연결됨 ✓";
  } else {
    badge.textContent = "NotebookLM: 미연결"; badge.className = "badge no";
    $("nlmStatusLine").innerHTML = "미연결 — <code>nlm login</code> 필요 (슬라이드/대본 생성 시)";
  }
  return authed;
}

// ---- settings panel (⚙): optional Google app-login + NotebookLM recheck ----
function toggleSettings() {
  const p = $("settingsPanel");
  const show = p.classList.contains("hidden");
  p.classList.toggle("hidden");
  if (show) refreshAppLogin();
}

async function refreshAppLogin() {
  const el = $("appLoginStatus");
  const btn = $("googleLoginBtn");
  try {
    const s = await fetch("/api/auth/status", { credentials: "same-origin" }).then(r => r.json());
    if (s.authenticated) {
      el.textContent = `로그인됨: ${s.username}`;
      btn.textContent = "로그아웃";
      btn.onclick = async () => { await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" }); refreshAppLogin(); };
    } else if (s.google_oauth_enabled) {
      el.textContent = "로그인 안 됨 (선택)";
      btn.textContent = "Google로 로그인";
      btn.disabled = false;
      btn.onclick = () => { window.location.href = "/api/auth/google/login"; };
    } else {
      el.innerHTML = "Google OAuth 미설정 — <code>.env</code> 에 GOOGLE_OAUTH_CLIENT_ID/SECRET 입력";
      btn.textContent = "Google로 로그인";
      btn.disabled = true;
    }
  } catch (e) {
    // AUTH_ENABLED=false 면 /api/auth/status 가 없거나 의미 없음 → 로그인 불필요 안내
    el.textContent = "로그인 불필요 (로컬 모드)";
    btn.disabled = true;
  }
}

async function loadNotebooks() {
  const sel = $("notebook");
  sel.innerHTML = '<option value="">불러오는 중…</option>';
  try {
    const d = await api("/notebooks");
    const items = d.notebooks || [];
    if (!items.length) { sel.innerHTML = '<option value="">(노트북 없음)</option>'; return; }
    sel.innerHTML = '<option value="">— 노트북 선택 —</option>';
    for (const nb of items) {
      const id = nb.id || nb.notebook_id || nb.uuid || "";
      const title = nb.title || nb.name || id;
      const opt = document.createElement("option");
      opt.value = id; opt.textContent = `${title}  (${id.slice(0, 8)}…)`;
      sel.appendChild(opt);
    }
  } catch (e) {
    sel.innerHTML = `<option value="">목록 오류: ${e.message}</option>`;
  }
  syncStartEnabled();
}

function syncStartEnabled() {
  $("startBtn").disabled = !$("notebook").value;
}

// ---- start job + poll ----
async function startJob() {
  const body = {
    notebook_id: $("notebook").value,
    total_pages: parseInt($("totalPages").value, 10) || 40,
    chunk_size: parseInt($("chunkSize").value, 10) || 20,
    language: $("language").value.trim() || "ko",
    target_audience: $("targetAudience").value.trim(),
    objective: $("objective").value.trim(),
    fmt: $("fmt").value,
    length: $("length").value,
    design_system: $("designSystem").value.trim() || null,
    add_script_as_source: $("addSource").checked,
  };
  if (!body.notebook_id) return;
  $("startBtn").disabled = true;
  $("progressCard").classList.remove("hidden");
  $("reviewCard").classList.add("hidden");
  $("logs").textContent = "잡 시작 중…";
  try {
    const d = await api("/jobs", { method: "POST", body: JSON.stringify(body) });
    currentJobId = d.job_id;
    startPolling();
  } catch (e) {
    $("logs").textContent = "오류: " + e.message;
    $("startBtn").disabled = false;
  }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollJob, 2000);
  pollJob();
}

async function pollJob() {
  if (!currentJobId) return;
  let job;
  try { job = await api("/jobs/" + currentJobId); }
  catch (e) { return; }
  $("stage").textContent = `${job.stage} · ${Math.round((job.progress || 0) * 100)}%`;
  $("barFill").style.width = Math.round((job.progress || 0) * 100) + "%";
  $("logs").textContent = (job.logs || []).map(l => "• " + l.msg).join("\n");
  $("logs").scrollTop = $("logs").scrollHeight;

  if (job.status === "review") {
    clearInterval(pollTimer); pollTimer = null;
    renderReview(job);
  } else if (job.status === "done") {
    clearInterval(pollTimer); pollTimer = null;
    renderReview(job);
    showBundleResult(job.result && job.result.bundle);
  } else if (job.status === "error") {
    clearInterval(pollTimer); pollTimer = null;
    $("logs").textContent += "\n\n[실패] " + (job.error || "");
    $("startBtn").disabled = false;
  }
}

// ---- review UI ----
function renderReview(job) {
  $("reviewCard").classList.remove("hidden");
  const r = job.result || {};
  const warns = $("warnings");
  warns.innerHTML = "";
  for (const w of (r.warnings || [])) {
    const div = document.createElement("div");
    div.className = "warn"; div.textContent = "⚠ " + w; warns.appendChild(div);
  }
  const wrap = $("slides");
  wrap.innerHTML = "";
  for (const s of (r.slides || [])) {
    const el = document.createElement("div");
    el.className = "slide";
    el.dataset.index = s.index;
    const imgHtml = s.image_index
      ? `<img loading="lazy" src="${API}/jobs/${job.id}/image/${s.image_index}" alt="slide ${s.index}">`
      : `<div class="warn">이미지 없음</div>`;
    el.innerHTML = `
      ${imgHtml}
      <div class="idx">#${s.index} · 원본 슬라이드 ${s.number}</div>
      <label>제목</label>
      <input class="f-title" value="${escapeHtml(s.title || "")}">
      <label>대본 (narration_text)</label>
      <textarea class="f-narration">${escapeHtml(s.narration || "")}</textarea>
      <details>
        <summary>화면 텍스트 / PDF 추출 텍스트 (검수)</summary>
        <label>화면 텍스트</label>
        <textarea class="f-screen">${escapeHtml(s.screen_text || "")}</textarea>
        <label>PDF에서 추출된 텍스트</label>
        <pre>${escapeHtml(s.extracted_text || "(없음)")}</pre>
      </details>
    `;
    el.dataset.imageIndex = s.image_index || "";
    el.dataset.number = s.number;
    wrap.appendChild(el);
  }
}

function collectSlides() {
  const out = [];
  for (const el of document.querySelectorAll(".slide")) {
    out.push({
      index: parseInt(el.dataset.index, 10),
      number: parseInt(el.dataset.number, 10) || null,
      title: el.querySelector(".f-title").value,
      narration: el.querySelector(".f-narration").value,
      screen_text: el.querySelector(".f-screen").value,
      image_index: el.dataset.imageIndex ? parseInt(el.dataset.imageIndex, 10) : null,
    });
  }
  return out;
}

async function buildBundle() {
  if (!currentJobId) return;
  $("buildBtn").disabled = true;
  $("bundleResult").innerHTML = "<span class='hint'>번들 생성 중…</span>";
  try {
    const payload = await api(`/jobs/${currentJobId}/bundle`, {
      method: "POST",
      body: JSON.stringify({
        chapter: parseInt($("chapter").value, 10) || 1,
        title: $("bundleTitle").value.trim() || "VOD Studio Deck",
        slides: collectSlides(),
      }),
    });
    showBundleResult(payload);
  } catch (e) {
    $("bundleResult").innerHTML = `<span class="err">오류: ${escapeHtml(e.message)}</span>`;
  } finally {
    $("buildBtn").disabled = false;
  }
}

function showBundleResult(payload) {
  if (!payload) return;
  const problems = payload.validation_problems || [];
  const issues = payload.build_issues || [];
  let html = `<div><b>번들 생성 완료</b> — 씬 ${payload.scene_count}개, 예상 ${payload.total_duration_seconds}s</div>`;
  html += `<div class="hint">경로: <code>${escapeHtml(payload.bundle_dir || "")}</code></div>`;
  if (issues.length) html += `<div class="warn">빌드 경고 ${issues.length}건: ${issues.map(escapeHtml).join("; ")}</div>`;
  html += problems.length
    ? `<div class="err">⚠ mp4maker 검증 문제 ${problems.length}건: ${problems.map(escapeHtml).join("; ")}</div>`
    : `<div style="color:var(--accent2)">✓ mp4maker load_bundle 검증 통과</div>`;
  $("bundleResult").innerHTML = html;
  const link = $("downloadLink");
  link.href = `${API}/jobs/${currentJobId}/bundle/download`;
  link.classList.remove("hidden");
  // Reveal the mp4maker render step now that a bundle exists.
  $("renderCard").classList.remove("hidden");
  refreshAudioStatus();
}

// ---- mp4maker render ----
let renderTimer = null;

async function refreshAudioStatus() {
  if (!currentJobId) return;
  try {
    const s = await api(`/jobs/${currentJobId}/audio-status`);
    const el = $("audioStatus");
    if (s.missing && s.missing.length) {
      el.innerHTML = `오디오: ${s.with_audio}/${s.total} 씬에 음성 있음 — ${s.missing.length}개 누락. ` +
        `<b>무음 미리보기</b>로 바로 렌더하거나, <code>audio\\</code> 에 WAV를 채운 뒤 '음성 포함'을 쓰세요.`;
      $("renderMode").value = "silent";
    } else {
      el.innerHTML = `오디오: 전체 ${s.total}개 씬 음성 준비됨 ✓ — '음성 포함' 렌더 가능`;
      $("renderMode").value = "voiced";
    }
  } catch (e) {
    $("audioStatus").textContent = "오디오 상태 확인 실패: " + e.message;
  }
}

async function startRender() {
  if (!currentJobId) return;
  $("renderBtn").disabled = true;
  $("renderLogs").classList.remove("hidden");
  $("renderBar").style.display = "block";
  $("renderBarFill").style.width = "0%";
  $("player").classList.add("hidden");
  $("videoLink").classList.add("hidden");
  $("renderLogs").textContent = "렌더 시작 중…";
  try {
    await api(`/jobs/${currentJobId}/render`, {
      method: "POST",
      body: JSON.stringify({ mode: $("renderMode").value, resolution: $("resolution").value }),
    });
    if (renderTimer) clearInterval(renderTimer);
    renderTimer = setInterval(renderPoll, 1500);
  } catch (e) {
    $("renderLogs").textContent = "오류: " + e.message;
    $("renderBtn").disabled = false;
  }
}

async function renderPoll() {
  if (!currentJobId) return;
  let job;
  try { job = await api(`/jobs/${currentJobId}`); } catch (e) { return; }
  const r = job.result || {};
  const logs = r.render_logs || [];
  $("renderLogs").textContent = logs.join("\n");
  $("renderLogs").scrollTop = $("renderLogs").scrollHeight;
  // progress from "...progress=K/N"
  let pct = 0;
  for (let i = logs.length - 1; i >= 0; i--) {
    const m = /progress=(\d+)\/(\d+)/.exec(logs[i]);
    if (m) { pct = Math.round((+m[1] / +m[2]) * 100); break; }
  }
  if (pct) $("renderBarFill").style.width = pct + "%";

  if (!r.rendering) {
    clearInterval(renderTimer); renderTimer = null;
    $("renderBtn").disabled = false;
    if (r.render && r.render.path) {
      $("renderBarFill").style.width = "100%";
      const url = `${API}/jobs/${currentJobId}/video?t=${Date.now()}`;
      const player = $("player");
      player.src = url; player.classList.remove("hidden");
      const vl = $("videoLink"); vl.href = url; vl.classList.remove("hidden");
    } else if (r.render_error) {
      $("renderLogs").textContent += "\n\n[실패] " + r.render_error;
    }
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---- wire up ----
$("loadNotebooks").addEventListener("click", loadNotebooks);
$("gearBtn").addEventListener("click", toggleSettings);
$("nlmRecheck").addEventListener("click", async () => { if (await checkAuth()) loadNotebooks(); });
$("notebook").addEventListener("change", syncStartEnabled);
$("startBtn").addEventListener("click", startJob);
$("buildBtn").addEventListener("click", buildBundle);
$("renderBtn").addEventListener("click", startRender);

(async () => {
  if (await checkAuth()) loadNotebooks();
})();
