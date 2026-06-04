// 영상공방 (VOD Studio) — 4탭: 대본(Gemini) → 이미지(NotebookLM PDF) → 음성/자막 → 영상(mp4maker)
const $ = (id) => document.getElementById(id);
const API = "/api/vodstudio";
let JOB = null;           // 현재 작업(job) id — 이미지 생성 시 만들어지고 저장/음성/영상에서 재사용
let renderTimer = null;

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
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---- 탭 전환 ----
function showTab(name) {
  document.querySelectorAll(".panel").forEach(p => p.classList.toggle("hidden", p.dataset.panel !== name));
  document.querySelectorAll("#stepper .step").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// ---- Gemini 설치/로그인 상태 ----
async function checkGemini() {
  const b = $("geminiBadge");
  try {
    const s = await api("/gemini/status");
    if (s.installed) { b.textContent = "gemini 설치됨"; b.className = "badge ok"; }
    else { b.textContent = "gemini 미설치"; b.className = "badge no"; }
  } catch (e) { b.textContent = "확인 불가"; b.className = "badge"; }
}

// ================= ① 대본 =================
async function genScript() {
  const f = $("srcFile").files[0];
  if (!f) { alert("소스 파일을 첨부하세요 (또는 대본을 직접 붙여넣어도 됩니다)."); return; }
  $("genBtn").disabled = true;
  $("genStatus").textContent = "Gemini 생성 중… (처음엔 구글 로그인 창이 뜰 수 있어요)";
  try {
    const fd = new FormData();
    fd.append("file", f);
    fd.append("total_pages", $("gTotal").value);
    fd.append("target_audience", $("gAudience").value);
    fd.append("objective", $("gObjective").value);
    const res = await fetch(API + "/gemini/from-file", { method: "POST", credentials: "same-origin", body: fd });
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || "생성 실패");
    $("manualScript").value = d.script || "";
    $("rcTotal").value = $("gTotal").value;   // 렌더코드 슬라이드 수 동기화
    $("genStatus").textContent = `완료 (소스 ${d.source_chars || 0}자) — 대본 확인/수정 후 ② 이미지로`;
  } catch (e) {
    $("genStatus").textContent = "실패: " + e.message;
  } finally {
    $("genBtn").disabled = false;
  }
}

// NotebookLM 렌더 코드 (클라이언트 생성 — 영상 레시피의 3단계 커널 코드)
function buildRenderCode(total, chunk) {
  const n = Math.ceil(total / chunk);
  let fns = "";
  for (let i = 0; i < n; i++) {
    const s = i * chunk + 1, e = Math.min((i + 1) * chunk, total);
    const first = i === 0, last = i === n - 1;
    let k = 3;
    let rules = "    1. Apply [Global Design System] exactly.\n    2. Match Source content 1:1.\n";
    if (!first) { rules += `    ${k}. RULE: DO NOT generate a cover/title slide. Start immediately with slide ${s} body content.\n`; k++; }
    rules += last
      ? `    ${k}. RULE: Place the ONLY ending/closing slide at slide ${e}.`
      : `    ${k}. RULE: DO NOT generate any ending/thank-you slide at slide ${e}. End with body content.`;
    fns += `FUNCTION_${String(i + 1).padStart(2, "0")}_CALL_STUDIO() {\n  target_data: "Source Script Slides ${s} to ${e}"\n  deck_type: "presentation"\n  length: "dynamic"\n  user_steering_prompt: "\n${rules}\n  "\n}\n\n`;
  }
  return `[SYSTEM KERNEL OVERRIDE]\nRole: API Execution Terminal\nTask: Execute the following algorithmic sequence STRICTLY. Do not summarize, do not combine, do not output conversational text.\n\n## [Global Design System]\n<<<여기에 영문 디자인 프롬프트(있으면) 붙여넣기 — 없으면 이 줄 삭제>>>\n\n## EXECUTION_SCRIPT_RUN()\nWARNING: Merging ${total} slides into a single API call causes a FATAL_MEMORY_CRASH. You MUST execute the ${n} functions below sequentially and independently.\n\n${fns}`;
}
function genRenderCode() {
  const total = parseInt($("rcTotal").value, 10) || 40;
  const chunk = parseInt($("rcChunk").value, 10) || 20;
  $("rcOut").value = buildRenderCode(total, chunk);
  $("rcOut").style.display = "block";
  $("rcCopyBtn").classList.remove("hidden");
}
async function copyScript() {
  const t = $("manualScript").value;
  if (!t.trim()) { $("copyScriptStatus").textContent = "대본이 비어 있어요."; return; }
  try { await navigator.clipboard.writeText(t); }
  catch (e) { $("manualScript").select(); document.execCommand("copy"); }
  $("copyScriptStatus").textContent = "✓ 복사됨 — NotebookLM [+ 소스 추가 → 복사된 텍스트]에 붙여넣기";
  setTimeout(() => { $("copyScriptStatus").textContent = ""; }, 4000);
}
async function copyRenderCode() {
  try { await navigator.clipboard.writeText($("rcOut").value); $("rcCopyBtn").textContent = "✓ 복사됨"; }
  catch (e) { $("rcOut").select(); document.execCommand("copy"); $("rcCopyBtn").textContent = "✓ 복사됨"; }
  setTimeout(() => { $("rcCopyBtn").textContent = "📋 복사"; }, 1500);
}

// ================= ② 이미지 =================
const PDF_SLOTS = 3;
const slotInputs = [];
function buildSlots() {
  const wrap = $("pdfSlots");
  wrap.innerHTML = "";
  slotInputs.length = 0;
  for (let i = 0; i < PDF_SLOTS; i++) {
    const label = document.createElement("label");
    label.className = "filebox";
    label.style.cssText = "min-height:64px; margin-bottom:.5rem";
    const dft = `📎 ${i + 1}번째 슬라이드 PDF`;
    label.innerHTML = `<input type="file" accept="application/pdf" hidden><span>${dft}</span>`;
    const inp = label.querySelector("input"), span = label.querySelector("span");
    function refresh() {
      const f = inp.files[0];
      if (f) { span.textContent = `📄 ${i + 1}번째: ${f.name}`; label.classList.add("has-file"); }
      else { span.textContent = dft; label.classList.remove("has-file"); }
    }
    inp.addEventListener("change", () => { refresh(); makeImages(); });
    ["dragenter", "dragover"].forEach(ev => label.addEventListener(ev, e => { e.preventDefault(); label.classList.add("drag"); }));
    ["dragleave", "drop"].forEach(ev => label.addEventListener(ev, e => { e.preventDefault(); label.classList.remove("drag"); }));
    label.addEventListener("drop", e => {
      const f = e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) { const dt = new DataTransfer(); dt.items.add(f); inp.files = dt.files; refresh(); makeImages(); }
    });
    wrap.appendChild(label);
    slotInputs.push(inp);
  }
}
async function makeImages() {
  const files = slotInputs.map(i => i.files[0]).filter(Boolean);
  if (!files.length) { $("thumbs").innerHTML = ""; $("imgStatus").textContent = ""; return; }
  $("imgStatus").textContent = "이미지 만드는 중…";
  try {
    const fd = new FormData();
    files.forEach(f => fd.append("pdfs", f));
    const res = await fetch(API + "/preview-images", { method: "POST", credentials: "same-origin", body: fd });
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || "이미지 생성 실패");
    JOB = d.job_id;
    $("imgStatus").textContent = `총 ${d.page_count}개 씬(이미지) 생성됨`;
    const wrap = $("thumbs");
    wrap.innerHTML = "";
    (d.images || []).forEach(idx => {
      const el = document.createElement("div");
      el.className = "slide";
      el.innerHTML = `<img loading="lazy" src="${API}/jobs/${JOB}/image/${idx}"><div class="idx">씬 ${idx}</div>`;
      wrap.appendChild(el);
    });
  } catch (e) {
    $("imgStatus").textContent = "오류: " + e.message;
  }
}

// ================= ③ 저장 + 음성 =================
async function ensureJob() {
  // 이미지가 없어도 저장할 수 있도록, 빈 job을 만들어 둔다.
  if (JOB) return JOB;
  const res = await fetch(API + "/preview-images", { method: "POST", credentials: "same-origin", body: new FormData() });
  const d = await res.json();
  JOB = d.job_id;
  return JOB;
}
async function saveBundle() {
  const text = $("manualScript").value.trim();
  if (!text) { alert("① 대본을 먼저 만들거나 붙여넣으세요."); showTab("script"); return; }
  $("saveBtn").disabled = true;
  $("saveStatus").textContent = "저장 중… (대본 파싱 + 이미지 매칭)";
  try {
    await ensureJob();
    const fd = new FormData();
    fd.append("script_text", text);
    fd.append("chapter", $("chapter").value);
    fd.append("title", $("bundleTitle").value.trim() || "VOD Studio Deck");
    fd.append("output_dir", $("outputDir").value.trim());
    const res = await fetch(`${API}/jobs/${JOB}/save`, { method: "POST", credentials: "same-origin", body: fd });
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || "저장 실패");
    showSave(d);
    refreshAudio();
  } catch (e) {
    $("saveStatus").textContent = "실패: " + e.message;
  } finally {
    $("saveBtn").disabled = false;
  }
}
function showSave(d) {
  $("saveStatus").textContent = "";
  const probs = d.validation_problems || [], issues = d.build_issues || [];
  let html = `<div style="color:var(--accent2)"><b>저장 완료</b> — 씬 ${d.scene_count}개 (대본 ${d.slide_count ?? "?"} · 이미지 ${d.page_count ?? 0})</div>`;
  html += `<div class="hint">경로: <code>${esc(d.bundle_dir)}</code></div>`;
  if (issues.length) html += `<div class="warn">이미지 부족 ${issues.length}건 — 슬라이드 수와 PDF 페이지 수를 맞추세요.</div>`;
  html += probs.length
    ? `<div class="err">검증 문제 ${probs.length}건</div>`
    : `<div style="color:var(--accent2)">✓ mediaforge가 바로 읽는 형식</div>`;
  $("saveResult").innerHTML = html;
  $("jsonPreview").textContent = d.script_json || "";
  const link = $("downloadLink");
  if (link) { link.href = `${API}/jobs/${JOB}/bundle/download`; link.classList.remove("hidden"); }
}
async function refreshAudio() {
  if (!JOB) return;
  try {
    const s = await api(`/jobs/${JOB}/audio-status`);
    $("audioStatus").innerHTML = (s.missing && s.missing.length)
      ? `오디오: ${s.with_audio}/${s.total} 씬 음성 있음 — ${s.missing.length}개 누락. [무음 오디오 생성]으로 미리보기 가능.`
      : `오디오: 전체 ${s.total}개 음성 준비됨 ✓ — ④ 영상에서 '음성 포함' 렌더 가능`;
  } catch (e) { $("audioStatus").textContent = "번들 저장 후 확인됩니다."; }
}
async function genAudio() {
  if (!JOB) { alert("먼저 ③에서 번들을 저장하세요."); return; }
  $("genAudioBtn").disabled = true;
  try {
    const s = await api(`/jobs/${JOB}/gen-audio`, { method: "POST" });
    $("audioStatus").textContent = `무음 오디오 ${s.generated}개 생성 — 전체 ${s.total}개 준비됨. ④ 영상에서 렌더하세요.`;
  } catch (e) {
    $("audioStatus").textContent = "오류: " + e.message;
  } finally {
    $("genAudioBtn").disabled = false;
  }
}

// ================= ④ 영상 =================
async function startRender() {
  if (!JOB) { alert("먼저 ③에서 번들을 저장하세요."); showTab("audio"); return; }
  $("renderBtn").disabled = true;
  $("renderLogs").classList.remove("hidden");
  $("renderBar").style.display = "block";
  $("renderBarFill").style.width = "0%";
  $("player").classList.add("hidden");
  $("videoLink").classList.add("hidden");
  $("renderLogs").textContent = "렌더 시작…";
  try {
    await api(`/jobs/${JOB}/render`, {
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
  if (!JOB) return;
  let job;
  try { job = await api(`/jobs/${JOB}`); } catch (e) { return; }
  const r = job.result || {};
  const logs = r.render_logs || [];
  $("renderLogs").textContent = logs.join("\n");
  $("renderLogs").scrollTop = $("renderLogs").scrollHeight;
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
      const url = `${API}/jobs/${JOB}/video?t=${Date.now()}`;
      $("player").src = url; $("player").classList.remove("hidden");
      $("videoLink").href = url; $("videoLink").classList.remove("hidden");
    } else if (r.render_error) {
      $("renderLogs").textContent += "\n\n[실패] " + r.render_error;
    }
  }
}

// ---- ⚙ 설정 (선택적 구글 로그인) ----
function toggleSettings() {
  const p = $("settingsPanel");
  p.classList.toggle("hidden");
  if (!p.classList.contains("hidden")) refreshAppLogin();
}
async function refreshAppLogin() {
  const el = $("appLoginStatus"), btn = $("googleLoginBtn");
  if (!el || !btn) return;
  try {
    const s = await fetch("/api/auth/status", { credentials: "same-origin" }).then(r => r.json());
    if (s.authenticated) { el.textContent = `로그인됨: ${s.username}`; btn.textContent = "로그아웃"; btn.onclick = async () => { await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" }); refreshAppLogin(); }; }
    else if (s.google_oauth_enabled) { el.textContent = "로그인 안 됨 (선택)"; btn.disabled = false; btn.onclick = () => { window.location.href = "/api/auth/google/login"; }; }
    else { el.innerHTML = "Google OAuth 미설정 (.env)"; btn.disabled = true; }
  } catch (e) { el.textContent = "로그인 불필요 (로컬)"; btn.disabled = true; }
}

// ---- 파일 첨부 박스(소스) drag&drop ----
function wireDrop(boxId, inputId, textId, dft) {
  const box = $(boxId), inp = $(inputId), txt = $(textId);
  if (!box || !inp) return;
  function show() { const f = inp.files[0]; if (f) { txt.textContent = "📄 " + f.name; box.classList.add("has-file"); } else { txt.textContent = dft; box.classList.remove("has-file"); } }
  inp.addEventListener("change", show);
  ["dragenter", "dragover"].forEach(ev => box.addEventListener(ev, e => { e.preventDefault(); box.classList.add("drag"); }));
  ["dragleave", "drop"].forEach(ev => box.addEventListener(ev, e => { e.preventDefault(); box.classList.remove("drag"); }));
  box.addEventListener("drop", e => { const f = e.dataTransfer.files && e.dataTransfer.files[0]; if (f) { const dt = new DataTransfer(); dt.items.add(f); inp.files = dt.files; show(); } });
}

// ---- 와이어링 ----
function on(id, ev, fn) { const el = $(id); if (el) el.addEventListener(ev, fn); }
document.querySelectorAll("#stepper .step").forEach(b => b.addEventListener("click", () => showTab(b.dataset.tab)));
on("nextImages", "click", () => showTab("images"));
on("nextAudio", "click", () => showTab("audio"));
on("nextVideo", "click", () => showTab("video"));
on("genBtn", "click", genScript);
on("copyScriptBtn", "click", copyScript);
on("rcGenBtn", "click", genRenderCode);
on("rcCopyBtn", "click", copyRenderCode);
on("saveBtn", "click", saveBundle);
on("genAudioBtn", "click", genAudio);
on("renderBtn", "click", startRender);
on("gearBtn", "click", toggleSettings);
on("nlmRecheck", "click", () => {});

wireDrop("srcDrop", "srcFile", "srcBoxText", "📎 소스 파일 첨부 (PDF·Word·PPT·Excel) — 이 내용으로 대본 생성");
buildSlots();
checkGemini();
