"use strict";

// ---------- 通用 ----------
const $ = (sel) => document.querySelector(sel);
const api = async (path, opts = {}) => {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (res.status === 204) return null;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = body.detail;
    let msg = typeof detail === "string" ? detail : JSON.stringify(detail);
    if (Array.isArray(detail)) {
      msg = detail.map((d) => `${d.loc.slice(1).join(".")}: ${d.msg}`).join("；");
    }
    throw new Error(msg || `请求失败 (${res.status})`);
  }
  return body;
};
const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function setMsg(el, text, kind) {
  el.textContent = text || "";
  el.className = "msg" + (kind ? " " + kind : "");
}

// ---------- 状态 ----------
let lights = [];
let settings = { w: 20, k: 0 };
let currentRecord = null; // 当前正在查看的分析记录（含快照）
let viewingSnapshot = false;
let analysesCache = [];   // 历史分析列表（同时供反演快照下拉）
let currentInversion = null; // 当前正在查看的反演记录
let viewingInversionSnapshot = false;

// ---------- 标签页 ----------
function switchTab(name) {
  const isInv = name === "invert";
  $("#tab-precheck").classList.toggle("active", !isInv);
  $("#tab-invert").classList.toggle("active", isInv);
  $("#pane-precheck").hidden = isInv;
  $("#pane-invert").hidden = !isInv;
}

// ---------- 灯串编辑 ----------
function renderLightRows() {
  const box = $("#light-rows");
  box.innerHTML = "";
  lights.forEach((item, idx) => {
    const row = document.createElement("div");
    row.className = "light-row";
    row.innerHTML = `
      <div class="idx">${idx + 1}</div>
      <input class="name" maxlength="20" value="${esc(item.name)}" data-k="name" aria-label="灯名" />
      <input class="pattern" maxlength="80" value="${esc(item.pattern)}" data-k="pattern" aria-label="灯串（0/1）" />
      <button class="btn small danger" type="button" data-act="del" ${lights.length <= 2 ? "disabled" : ""}>删</button>
      <div class="row-err"></div>`;
    row.querySelector('[data-k="name"]').addEventListener("input", (e) => {
      lights[idx].name = e.target.value;
    });
    row.querySelector('[data-k="pattern"]').addEventListener("input", (e) => {
      lights[idx].pattern = e.target.value.trim();
      const err = validateRow(lights[idx]);
      row.classList.toggle("invalid", !!err);
      row.querySelector(".row-err").textContent = err || "";
    });
    row.querySelector('[data-act="del"]').addEventListener("click", () => {
      if (lights.length <= 2) return;
      lights.splice(idx, 1);
      renderLightRows();
    });
    box.appendChild(row);
  });
}

function validateRow(item) {
  const p = item.pattern || "";
  if (p.length < 2 || p.length > 80) return "灯串长度须为 2~80 位";
  if (!/^[01]+$/.test(p)) return "只能包含 0 和 1";
  if (!p.includes("0") || !p.includes("1")) return "必须同时含 0 和 1";
  if (!(item.name || "").trim()) return "灯名不能为空";
  return null;
}

function collectAndValidate() {
  const errs = [];
  const names = new Set();
  lights.forEach((it, i) => {
    const e = validateRow(it);
    if (e) errs.push(`灯 ${i + 1}：${e}`);
    if (names.has(it.name.trim())) errs.push(`灯 ${i + 1}：灯名重复`);
    names.add(it.name.trim());
  });
  if (lights.length < 2 || lights.length > 20) errs.push("灯的数量须为 2~20 盏");
  const w = Number($("#inp-w").value);
  const k = Number($("#inp-k").value);
  if (!Number.isInteger(w) || w < 1 || w > 400) errs.push("W 须为 1~400 的整数");
  if (!Number.isInteger(k) || k < 0 || k > 3) errs.push("k 须为 0~3 的整数");
  return { errs, w, k };
}

$("#btn-add").addEventListener("click", () => {
  if (lights.length >= 20) return;
  lights.push({ name: `灯 ${String.fromCharCode(65 + lights.length)}`, pattern: "10000000" });
  renderLightRows();
});

$("#btn-save-lights").addEventListener("click", async () => {
  const msg = $("#edit-msg");
  const { errs, w, k } = collectAndValidate();
  if (errs.length) {
    setMsg(msg, errs[0] + (errs.length > 1 ? ` 等 ${errs.length} 处错误` : ""), "err");
    return;
  }
  try {
    await api("/api/lights", { method: "PUT", body: JSON.stringify({ lights }) });
    await api("/api/settings", { method: "PUT", body: JSON.stringify({ w, k }) });
    settings = { w, k };
    setMsg(msg, "已保存。仅影响后续新分析，历史结果保持冻结不变。", "ok");
  } catch (e) {
    setMsg(msg, e.message, "err");
  }
});

$("#btn-analyze").addEventListener("click", async () => {
  const msg = $("#analyze-msg");
  const { errs, w, k } = collectAndValidate();
  if (errs.length) {
    setMsg(msg, errs[0] + (errs.length > 1 ? ` 等 ${errs.length} 处错误` : ""), "err");
    return;
  }
  const btn = $("#btn-analyze");
  btn.disabled = true;
  setMsg(msg, "正在穷举全部独立起始相位，最坏规模约需数秒……", "info");
  try {
    // 先持久化当前灯串与参数，再以其快照创建分析
    await api("/api/lights", { method: "PUT", body: JSON.stringify({ lights }) });
    await api("/api/settings", { method: "PUT", body: JSON.stringify({ w, k }) });
    settings = { w, k };
    const note = $("#inp-note").value;
    const rec = await api("/api/analyses", { method: "POST", body: JSON.stringify({ note }) });
    viewingSnapshot = false;
    currentRecord = rec;
    setMsg($("#edit-msg"), "已按当前输入更新设计；此结果为本次新分析的冻结快照。", "ok");
    setMsg(msg, "分析完成并已冻结保存。", "ok");
    renderResult(rec);
    await loadHistory(rec.id);
  } catch (e) {
    setMsg(msg, e.message, "err");
  } finally {
    btn.disabled = false;
  }
});

// ---------- 波形 ----------
function waveformSVG(pattern, windowN) {
  // 展开到至少 windowN 位（若窗口超过单串长度则循环续展）
  const n = Math.max(pattern.length, windowN);
  const bits = [];
  for (let t = 0; t < n; t++) bits.push(Number(pattern[t % pattern.length]));
  const cw = 11, ch = 30, pad = 2;
  const yHi = pad, yLo = ch - pad;
  const yy = (b) => (b === 1 ? yHi : yLo);
  let d = `M${pad},${yy(bits[0])}`;
  for (let t = 1; t < n; t++) {
    d += `H${pad + t * cw}`;             // 水平线到本位左边界
    if (bits[t] !== bits[t - 1]) d += `V${yy(bits[t])}`; // 边界处跳变
  }
  d += `H${pad + n * cw}`;
  const periodX = pad + pattern.length * cw;
  const bars = bits.map((b, t) =>
    `<rect x="${pad + t * cw + 1}" y="${ch - 3}" width="${cw - 2}" height="2"
      fill="${b === 1 ? "var(--bit-on)" : "var(--bit-off)"}" />`).join("");
  return `<svg class="wave-svg" width="${pad * 2 + n * cw}" height="${ch}" viewBox="0 0 ${pad * 2 + n * cw} ${ch}">
    <line x1="${periodX}" y1="0" x2="${periodX}" y2="${ch}" stroke="#b25e09" stroke-dasharray="3 2" />
    <path d="${d}" fill="none" stroke="#1f66b0" stroke-width="1.5" />
    ${bars}
  </svg>`;
}

// ---------- 距离曲线 ----------
function pairChartSVG(pair, names, w, threshold, shortest, solvable) {
  const W = 420, H = 150, L = 38, R = 12, T = 14, B = 26;
  const dmin = pair.dmin;
  const maxD = Math.max(...dmin, threshold + 1, 1);
  const span = Math.max(w - 1, 1);
  const x = (n) => L + ((n - 1) / span) * (W - L - R);
  const y = (d) => T + (1 - d / maxD) * (H - T - B);
  const pts = dmin.map((d, i) => `${x(i + 1).toFixed(1)},${y(d).toFixed(1)}`).join(" ");
  const markN = pair.witness_window;
  const mk = pair.witness.distance;
  const confuse = pair.confused_at_w;
  // 刻度
  const ticks = [1, Math.round(w / 2), w];
  const tickStr = [...new Set(ticks)].map((n) =>
    `<g><line x1="${x(n)}" y1="${H - B}" x2="${x(n)}" y2="${H - B + 4}" stroke="#9aa4af"/>
     <text x="${x(n)}" y="${H - 8}" font-size="10" fill="#9aa4af" text-anchor="middle">${n}</text></g>`).join("");
  const dTicks = [0, threshold, maxD];
  const dTickStr = [...new Set(dTicks)].map((d) =>
    `<text x="${L - 5}" y="${y(d) + 3}" font-size="10" fill="#9aa4af" text-anchor="end">${d}</text>`).join("");
  const shadeEnd = shortest && solvable ? shortest : w;
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" preserveAspectRatio="xMidYMid meet">
    ${solvable && shortest ? `<rect x="${x(shortest)}" y="${T}" width="${x(w) - x(shortest)}" height="${H - T - B}" fill="#e6f6ee" opacity=".6"/>` : ""}
    <line x1="${L}" y1="${y(threshold)}" x2="${W - R}" y2="${y(threshold)}" stroke="#b42318" stroke-dasharray="5 3" />
    <text x="${W - R}" y="${y(threshold) - 3}" font-size="9.5" fill="#b42318" text-anchor="end">阈值 2k=${threshold}</text>
    <line x1="${L}" y1="${H - B}" x2="${W - R}" y2="${H - B}" stroke="#c6cdd6"/>
    <line x1="${L}" y1="${T}" x2="${L}" y2="${H - B}" stroke="#c6cdd6"/>
    <polyline points="${pts}" fill="none" stroke="${confuse ? "#b42318" : "#1f66b0"}" stroke-width="1.7"/>
    <circle cx="${x(markN)}" cy="${y(mk)}" r="3.6" fill="${confuse ? "#b42318" : "#1a7f4e"}"/>
    ${tickStr}${dTickStr}
    <text x="${L + (W - L - R) / 2}" y="${H - 0}" font-size="9.5" fill="#9aa4af" text-anchor="middle">观测位数 n（每位 0.1 s）</text>
  </svg>`;
}

// ---------- 逐位见证 ----------
function witnessHTML(pair, names) {
  const wn = pair.witness;
  const win = pair.witness_window;
  const na = names[pair.a], nb = names[pair.b];
  const line = (label, bits, cls) => {
    const chars = [...bits].map((c) =>
      `<span class="${c === "1" ? "b1" : "b0"}">${c}</span>`).join("");
    return `<div class="bit-strip"><span class="lab">${esc(label)}</span>${chars}</div>`;
  };
  const marks = [...wn.diff_mark].map((c) =>
    `<span class="${c === "^" ? "mark" : ""}">${c === "^" ? "^" : "·"}</span>`).join("");
  // 位置尺：每 10 位标注序号（从 1 起），与每个字符同列
  const ruler = [...Array(win)].map((_, t) =>
    `<span class="${t % 10 === 0 ? "rl" : ""}">${t % 10 === 0 ? String(t + 1).slice(-1) : ""}</span>`
  ).join("");
  const diffs = wn.diff_positions.length
    ? wn.diff_positions.slice(0, 60).map((p) => p + 1).join(", ") +
      (wn.diff_positions.length > 60 ? ` …（共 ${wn.diff_positions.length} 处）` : "")
    : "无（两路完全重合）";
  return `<div class="witness">
    <div class="wh">决定性窗口 n = <b>${win}</b>（${(win * 0.1).toFixed(1)} s）·
      最近相位：${esc(na)} 起始相位 <b>${wn.phase_a}</b>，${esc(nb)} 起始相位 <b>${wn.phase_b}</b>
      <span class="muted">（相位 = 观测第 0 位对应各自循环串中的下标）</span>
    </div>
    <div class="bit-strip"><span class="lab"></span>${marks}</div>
    ${line(na, wn.bits_a)}
    ${line(nb, wn.bits_b)}
    <div class="bit-strip ruler"><span class="lab"></span>${ruler}</div>
    <div class="diff-note">该相位下前 ${win} 位汉明距离 =
      <b class="${wn.distance <= 2 ? "" : ""}">${wn.distance}</b>；
      差异位（从 1 起）：<span class="red">${diffs}</span>。
      ${pair.confused_at_w
        ? `<span class="red">W 位下仍 ≤ 2k，该灯对在窗口 W 内不可辨。</span>`
        : `<span class="green">该距离 &gt; 2k，此窗口下两灯可辨。</span>`}
    </div>
  </div>`;
}

// ---------- 结果渲染 ----------
function renderResult(rec) {
  const empty = $("#result-empty");
  const box = $("#result");
  empty.hidden = true;
  box.hidden = false;
  const r = rec.result;
  const names = rec.snapshot.map((s) => s.name);
  const confusedPairs = r.pairs.filter((p) => p.confused_at_w);

  let verdict;
  if (r.solvable) {
    verdict = `<div class="verdict ok">
      <div class="big">可辨 ✓</div>
      <div class="detail">
        最短可辨观测窗口 n* = <b>${r.shortest_window}</b> 位 = <b>${r.shortest_seconds.toFixed(1)} 秒</b>
        （1 ≤ n* ≤ W=${r.w}）<br/>
        全部 ${r.pair_count} 对灯在 n* 位、穷举全部独立起始相位后的最小汉明距离均 <b>&gt; 2k=${r.threshold}</b>。
      </div></div>`;
  } else {
    const names1 = confusedPairs.map((p) => `${esc(names[p.a])} ↔ ${esc(names[p.b])}`).join("、");
    verdict = `<div class="verdict fail">
      <div class="big">无解 ✗</div>
      <div class="detail">
        在 W=${r.w} 位（${(r.w * 0.1).toFixed(1)} 秒）内，仍有
        <b>${confusedPairs.length}</b> 对灯混淆（最小距离 ≤ 2k=${r.threshold}）：<br/>
        ${names1 || "—"}<br/>
        <span class="muted">下列各卡给出 W 位下距离最近的相位组合及逐位差异（^ 处）。</span>
      </div></div>`;
  }

  const snapshotBadge = viewingSnapshot
    ? `<span class="badge snapshot">历史快照 #${rec.id} · ${esc(rec.created_at)}${rec.note ? " · " + esc(rec.note) : ""}</span>`
    : `<span class="badge">本次分析 #${rec.id} · ${esc(rec.created_at)}${rec.note ? " · " + esc(rec.note) : ""}</span>`;

  const waves = `<div class="section-title">循环波形 <span class="muted">（橙条=1 亮，灰条=0 灭；虚线为循环串边界）</span></div>
    <div class="waveforms">${rec.snapshot.map((s) => `
      <div class="wave-row">
        <div class="wname" title="${esc(s.name)}（${s.pattern.length} 位）">${esc(s.name)}<br><span class="muted">${s.pattern.length} 位 / ${(s.pattern.length * 0.1).toFixed(1)} s 周期</span></div>
        <div class="wave-scroll">${waveformSVG(s.pattern, r.shortest_window || r.w)}</div>
      </div>`).join("")}</div>`;

  const cards = r.pairs.map((p) => {
    const confuse = p.confused_at_w;
    return `<div class="pair-card ${confuse ? "confused" : ""}">
      <h3><span>${esc(names[p.a])} ↔ ${esc(names[p.b])}</span>
        <span class="pair-tag"><span class="phases">穷举 ${p.phase_pairs_enumerated}/${p.phase_pairs_total}</span> 相位 · gcd 类 ${p.phase_equivalence_classes}</span>
      </h3>
      ${pairChartSVG(p, names, r.w, r.threshold, r.shortest_window, r.solvable)}
      ${witnessHTML(p, names)}
    </div>`;
  }).join("");

  box.innerHTML = `
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px">
      ${snapshotBadge}
      <span class="badge">W=${r.w}（${(r.w * 0.1).toFixed(1)} s）</span>
      <span class="badge">k=${r.k}，阈值 2k=${r.threshold}</span>
      <span class="badge">灯 ${r.light_count} 盏 · 灯对 ${r.pair_count} 对</span>
      <span class="badge">${r.exhaustive ? "全相位穷举，无抽样" : ""}</span>
    </div>
    ${verdict}
    ${waves}
    <div class="section-title">各灯对最小汉明距离曲线与逐位见证
      <span class="muted">（曲线 = 穷举全部相位后的最小距离；圆点 = 决定性窗口下的最近相位）</span>
    </div>
    <div class="pair-charts">${cards}</div>`;
}

// ---------- 历史 ----------
async function loadHistory(selectId) {
  const data = await api("/api/analyses");
  analysesCache = data.analyses;
  const box = $("#history-list");
  box.innerHTML = "";
  if (!data.analyses.length) {
    box.innerHTML = `<div class="muted" style="font-size:12px;padding:8px 2px">暂无分析记录。</div>`;
  } else {
    data.analyses.forEach((a) => {
      const div = document.createElement("div");
      div.className = "hist-item" + (selectId === a.id ? " active" : "");
      const pill = a.solvable
        ? `<span class="pill ok">可辨 n*=${a.shortest_window}</span>`
        : `<span class="pill fail">W 内无解</span>`;
      div.innerHTML = `
        <div class="h-id">#${a.id} ${pill}</div>
        <div class="h-meta">${esc(a.created_at.replace("T", " ").replace("Z", " UTC"))}<br/>
          W=${a.w} · k=${a.k} · ${a.light_count} 盏灯</div>
        ${a.note ? `<div class="h-note">${esc(a.note)}</div>` : ""}
        <div class="h-actions">
          <button class="btn small" data-act="view">查看冻结结果</button>
          <button class="btn small danger" data-act="del">删除</button>
        </div>`;
      div.querySelector('[data-act="view"]').addEventListener("click", () => viewAnalysis(a.id));
      div.querySelector('[data-act="del"]').addEventListener("click", async () => {
        if (!confirm(`确定删除分析 #${a.id}？已保存的反演记录自带快照副本，不会被连带删除。`)) return;
        await api(`/api/analyses/${a.id}`, { method: "DELETE" });
        if (currentRecord && currentRecord.id === a.id) {
          currentRecord = null;
          $("#result").hidden = true;
          $("#result-empty").hidden = false;
          $("#result-empty").textContent = "已删除。点击「保存并分析」生成新结果。";
        }
        await Promise.all([loadHistory(), loadInversions()]);
      });
      box.appendChild(div);
    });
  }
  populateSnapshotSelect(selectId);
}

// ---------- 观测反演：表单 ----------
function populateSnapshotSelect(prefId) {
  const sel = $("#inv-snapshot");
  const prev = prefId !== undefined ? prefId
    : sel.options.length ? Number(sel.value) : null;
  sel.innerHTML = "";
  if (!analysesCache.length) {
    const opt = document.createElement("option");
    opt.textContent = "（尚无已保存分析，请先在左侧创建一次分析）";
    opt.value = "";
    sel.add(opt);
    $("#inv-snap-meta").textContent = "";
    return;
  }
  analysesCache.forEach((a) => {
    const opt = document.createElement("option");
    opt.value = a.id;
    opt.textContent = `#${a.id} · W=${a.w} k=${a.k} · ${a.light_count} 盏灯 · ${a.solvable ? `可辨 n*=${a.shortest_window}` : "W 内无解"}${a.note ? ` · ${a.note}` : ""}`;
    sel.add(opt);
  });
  const ids = analysesCache.map((a) => a.id);
  // 回看的反演可能基于已被删除的分析：反演自带快照副本，仍在列表外补一个不可重选的项
  if (prev !== null && prev && !ids.includes(prev)) {
    const opt = document.createElement("option");
    opt.value = String(prev);
    opt.textContent = `#${prev} · 原分析已删除（本反演已自带冻结快照副本，仅可回看）`;
    sel.add(opt, 0);
  }
  if (prev !== null) sel.value = String(prev);
  renderSnapshotMeta();
}

async function renderSnapshotMeta() {
  const meta = $("#inv-snap-meta");
  const id = Number($("#inv-snapshot").value);
  if (!id) { meta.textContent = ""; return; }
  // 列表接口不含快照本体：优先用已载入的分析/反演详情，否则取分析详情
  let rec = currentRecord && currentRecord.id === id ? currentRecord : null;
  if (!rec) rec = analysesCache.find((a) => a.id === id) || null;
  if (!rec || !rec.snapshot) {
    if (currentInversion && currentInversion.analysis_id === id) rec = currentInversion;
    else rec = await api(`/api/analyses/${id}`).catch(() => null);
  }
  if (!rec || !rec.snapshot) {
    meta.textContent = `快照 #${id} 的原分析已删除。`;
    return;
  }
  const names = rec.snapshot.map((s) => `${esc(s.name)}(${s.pattern.length})`).join("、");
  meta.innerHTML = `快照 #${id}（冻结，不受左侧编辑影响）：${names}；观测串最长 W=${rec.w} 位（${(rec.w * 0.1).toFixed(1)} s）`;
}

$("#tab-precheck").addEventListener("click", () => switchTab("precheck"));
$("#tab-invert").addEventListener("click", () => { switchTab("invert"); renderSnapshotMeta(); });
$("#inv-snapshot").addEventListener("change", renderSnapshotMeta);

$("#btn-invert").addEventListener("click", async () => {
  const msg = $("#inv-msg");
  const id = Number($("#inv-snapshot").value);
  const obs = $("#inv-obs").value.trim();
  const e = Number($("#inv-e").value);
  if (!id) { setMsg(msg, "请先选择一条已保存分析的冻结快照。", "err"); return; }
  const errs = [];
  if (!obs) errs.push("观测串不能为空");
  else if (!/^[01]+$/.test(obs)) errs.push("观测串只能包含 0 和 1");
  else if (obs.length > 400) errs.push("观测串最长 400 位");
  if (!Number.isInteger(e) || e < 0 || e > 3) errs.push("e 须为 0~3 的整数");
  if (errs.length) { setMsg(msg, errs[0], "err"); return; }
  const chosen = analysesCache.find((a) => a.id === id);
  if (chosen && obs.length > chosen.w) {
    setMsg(msg, `观测串 ${obs.length} 位超过该快照的 W=${chosen.w}（${(chosen.w * 0.1).toFixed(1)} s）。`, "err");
    return;
  }
  const btn = $("#btn-invert");
  btn.disabled = true;
  setMsg(msg, "正在穷举快照内每盏灯的全部循环相位……", "info");
  try {
    const note = $("#inv-note").value;
    const rec = await api(`/api/analyses/${id}/inversions`, {
      method: "POST", body: JSON.stringify({ observation: obs, e, note }),
    });
    viewingInversionSnapshot = false;
    currentInversion = rec;
    setMsg(msg, `反演完成并已随快照 #${id} 与输入冻结保存（反演记录 #${rec.id}）。`, "ok");
    renderInversion(rec);
    await loadInversions(rec.id);
  } catch (err) {
    setMsg(msg, err.message, "err");
  } finally {
    btn.disabled = false;
  }
});

// ---------- 观测反演：逐位条 ----------
function invStripsHTML(rec, ideal, errorPositions) {
  const obs = rec.observation;
  const errSet = new Set(errorPositions || []);
  const chars = (bits, cls) => [...bits].map((c, t) => {
    const err = errSet.has(t);
    return `<span class="${c === "1" ? "b1" : "b0"}${err ? " er" : ""}">${c}</span>`;
  }).join("");
  const marks = [...obs].map((_, t) =>
    `<span class="${errSet.has(t) ? "mk" : ""}">${errSet.has(t) ? "^" : "·"}</span>`).join("");
  const ruler = [...Array(obs.length)].map((_, t) =>
    `<span>${t % 10 === 0 ? String(t + 1).slice(-1) : ""}</span>`).join("");
  return `<div class="inv-strips">
    <div class="isr"><span class="lab"></span><span class="bits">${marks}</span></div>
    <div class="isr"><span class="lab">实际观测</span><span class="bits">${chars(obs, "b")}</span></div>
    <div class="isr"><span class="lab">理想序列</span><span class="bits">${chars(ideal, "b")}</span></div>
    <div class="isr"><span class="lab"></span><span class="bits" style="color:#9aa4af">${ruler}</span></div>
  </div>`;
}

// ---------- 观测反演：候选漏斗 ----------
function funnelHTML(result) {
  const seq = [result.total_phases, ...result.prefix_counts];
  const cells = seq.map((v, i) => {
    const cls = i === 0 ? "fnum start" : (v < seq[i - 1] ? "fnum drop" : "fnum");
    const label = i === 0 ? "初始" : `第${i}位`;
    return `<div class="fcell"><span class="${cls}">${v}</span><span class="fpos">${label}</span></div>`;
  }).join("");
  return `<div class="funnel">${cells}</div>
    <div class="funnel-note">初始候选 = 快照内全部灯全部循环相位（${result.total_phases} 个）；
      逐位读入观测，累计汉明距离一旦 &gt; e=${result.e} 立即出局。
      排除发生在第 ${result.eliminated_at.length ? result.eliminated_at.join("、") : "（无）"} 位。</div>`;
}

// ---------- 观测反演：结果渲染 ----------
function renderInversion(rec) {
  const box = $("#inv-result");
  const r = rec.result;
  const snap = rec.snapshot;
  const snapBadge = viewingInversionSnapshot
    ? `<span class="badge snapshot">历史反演 #${rec.id} · ${esc(rec.created_at)}</span>`
    : `<span class="badge">本次反演 #${rec.id} · ${esc(rec.created_at)}</span>`;

  let verdict, body;
  if (r.outcome === "unique") {
    const u = r.unique;
    verdict = `<div class="inv-verdict unique">
      <div class="big">唯一候选 ✓</div>
      <div class="detail">
        观测灯为 <b>${esc(u.name)}</b>（循环串 <span class="mono">${esc(u.pattern)}</span>，${u.pattern_len} 位），
        起始相位 <b>${u.phase}</b>（观测第 0 位对应循环串下标 ${u.phase}）；
        ${u.distance === 0
          ? "观测与理想序列完全一致，无需纠正。"
          : `有 <b>${u.distance}</b> 位误判（1 基位置：${u.error_positions.map((p) => p + 1).join("、")}），已按下式纠正。`}
      </div></div>`;
    body = `
      <div class="inv-block"><h3>纠正后的理想序列（前 ${r.n} 位）</h3>
        ${invStripsHTML(rec, u.ideal, u.error_positions)}
        <div class="funnel-note" style="margin-top:4px">理想串 <span class="mono">${esc(u.ideal)}</span>；
          观测串 <span class="mono">${esc(r.observation)}</span>；红底标出误判位。</div>
      </div>
      <div class="inv-block"><h3>候选集随前缀的更新</h3>${funnelHTML(r)}</div>`;
  } else if (r.outcome === "ambiguous") {
    const rows = r.lights.map((lt) => {
      if (lt.candidate_count === 0) {
        return `<tr class="zero"><td>${esc(lt.name)}</td><td>0</td><td>—</td><td>—</td><td class="mono">最近也差 ${lt.nearest.distance} 位（相位 ${lt.nearest.phase}，&gt; e）</td></tr>`;
      }
      const w = lt.witness;
      const phases = lt.candidates.map((c) =>
        `φ${c.phase}(d=${c.distance})`).join(" ");
      return `<tr>
        <td>${esc(lt.name)}</td>
        <td><b>${lt.candidate_count}</b></td>
        <td class="mono">${w.phase}</td>
        <td class="mono">${w.distance}</td>
        <td class="cand-phases">${esc(phases)}</td>
      </tr>`;
    }).join("");
    const posTxt = r.undecidable_beyond
      ? `<b>超出观测串</b>：已读入的 ${r.n} 位每一位都还能排除候选，串内没有“无法排除”的位置，需继续观测（下一位 = 第 ${r.undecidable_position} 位）`
      : `第 <b>${r.undecidable_position}</b> 位（1 基，= ${((r.undecidable_position - 1) * 0.1).toFixed(1)} s 处）`;
    verdict = `<div class="inv-verdict ambiguous">
      <div class="big">仍有多个候选（${r.candidate_count} 个）</div>
      <div class="detail">
        穷举 ${snap.length} 盏灯共 ${r.total_phases} 个循环相位后，
        汉明距离 ≤ e=${r.e} 的候选分布在 <b>${r.lights.filter((l) => l.candidate_count).length}</b> 盏灯上，尚不能唯一判定。<br/>
        最早无法排除的位置：${posTxt}——从该位起读完整个观测串都不会再排除任何候选。
      </div></div>`;
    body = `
      <div class="inv-block"><h3>各灯候选与见证（见证 = 该灯存活相位中汉明距离最小者）</h3>
        <table class="inv-table">
          <thead><tr><th>灯</th><th>候选数</th><th class="mono">见证相位 φ</th><th class="mono">距离</th><th>全部候选相位</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <div class="inv-block"><h3>每灯一条见证（逐位对照）</h3>
        ${r.lights.filter((l) => l.candidate_count).map((lt) => `
          <div style="margin-bottom:10px">
            <div class="funnel-note" style="margin-bottom:2px">
              <b>${esc(lt.name)}</b> · 相位 ${lt.witness.phase} · 距离 ${lt.witness.distance} ·
              误判位（1 基）：${lt.witness.error_positions.length ? lt.witness.error_positions.map((p) => p + 1).join("、") : "无"}
            </div>
            ${invStripsHTML(rec, lt.witness.ideal, lt.witness.error_positions)}
          </div>`).join("")}
      </div>
      <div class="inv-block"><h3>候选集随前缀的更新</h3>${funnelHTML(r)}</div>`;
  } else {
    const nr = r.nearest;
    verdict = `<div class="inv-verdict none">
      <div class="big">无候选 ✗</div>
      <div class="detail">${esc(r.reason)}</div></div>`;
    body = `
      <div class="inv-block"><h3>全相位穷举下的最近匹配（仍超误判上限）</h3>
        <div class="funnel-note" style="margin-bottom:4px">
          <b>${esc(nr.name)}</b>（<span class="mono">${esc(nr.pattern)}</span>）· 相位 ${nr.phase} ·
          汉明距离 ${nr.distance} &gt; e=${r.e} ·
          差异位（1 基）：${nr.error_positions.length ? nr.error_positions.map((p) => p + 1).join("、") : "无"}
        </div>
        ${invStripsHTML(rec, nr.ideal, nr.error_positions)}
      </div>
      <div class="inv-block"><h3>候选集随前缀的更新</h3>${funnelHTML(r)}</div>`;
  }

  box.innerHTML = `
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      ${snapBadge}
      <span class="badge snapshot">基于分析快照 #${rec.analysis_id}</span>
      <span class="badge">n=${r.n}（${r.n_seconds.toFixed(1)} s）</span>
      <span class="badge">e=${r.e}</span>
      <span class="badge">穷举相位 ${r.total_phases} 个</span>
      <span class="badge">观测 <span class="mono">${esc(r.observation)}</span></span>
    </div>
    ${verdict}
    ${body}`;
}

// ---------- 观测反演：历史 ----------
async function loadInversions(selectId) {
  const data = await api("/api/inversions");
  const box = $("#inversion-list");
  box.innerHTML = "";
  if (!data.inversions.length) {
    box.innerHTML = `<div class="muted" style="font-size:12px;padding:8px 2px">暂无反演记录。</div>`;
    return;
  }
  const pillMap = {
    unique: `<span class="pill ok">唯一</span>`,
    ambiguous: `<span class="pill amb">多候选</span>`,
    none: `<span class="pill fail">无候选</span>`,
  };
  data.inversions.forEach((v) => {
    const div = document.createElement("div");
    div.className = "hist-item" + (selectId === v.id ? " active" : "");
    div.innerHTML = `
      <div class="h-id">#${v.id} ${pillMap[v.outcome] || ""}
        <span class="muted" style="font-weight:400">候选 ${v.candidate_count}</span></div>
      <div class="h-meta">${esc(v.created_at.replace("T", " ").replace("Z", " UTC"))}<br/>
        快照分析 #${v.analysis_id} · n=${v.observation.length}（W=${v.w}）· e=${v.e} · ${v.light_count} 盏灯<br/>
        <span class="mono">${esc(v.observation)}</span></div>
      ${v.note ? `<div class="h-note">${esc(v.note)}</div>` : ""}
      <div class="h-actions">
        <button class="btn small" data-act="view">回看冻结反演</button>
        <button class="btn small danger" data-act="del">删除</button>
      </div>`;
    div.querySelector('[data-act="view"]').addEventListener("click", () => viewInversion(v.id));
    div.querySelector('[data-act="del"]').addEventListener("click", async () => {
      if (!confirm(`确定删除反演记录 #${v.id}？`)) return;
      await api(`/api/inversions/${v.id}`, { method: "DELETE" });
      if (currentInversion && currentInversion.id === v.id) {
        currentInversion = null;
        $("#inv-result").innerHTML = "";
        setMsg($("#inv-msg"), "已删除该反演记录。", "info");
      }
      loadInversions();
    });
    box.appendChild(div);
  });
}

async function viewInversion(id) {
  const rec = await api(`/api/inversions/${id}`);
  currentInversion = rec;
  viewingInversionSnapshot = true;
  switchTab("invert");
  populateSnapshotSelect(rec.analysis_id);
  await renderSnapshotMeta();
  // 表单回填为当时冻结的输入（只读展示用途，不修改任何服务端状态）
  $("#inv-obs").value = rec.observation;
  $("#inv-e").value = rec.e;
  $("#inv-note").value = rec.note;
  setMsg($("#inv-msg"),
    `正在回看历史反演 #${id}：所选快照 #${rec.analysis_id}、观测串与 e 均为当时冻结内容；编辑当前灯串不影响本记录。`,
    "info");
  renderInversion(rec);
  document.querySelectorAll("#inversion-list .hist-item").forEach((el) => el.classList.remove("active"));
  await loadInversions(id);
}

async function viewAnalysis(id) {
  const rec = await api(`/api/analyses/${id}`);
  currentRecord = rec;
  viewingSnapshot = true;
  // 同步左侧输入为快照只读展示（不修改服务器当前灯串）
  lights = rec.snapshot.map((s) => ({ ...s }));
  renderLightRows();
  $("#inp-w").value = rec.w;
  $("#inp-k").value = rec.k;
  setMsg($("#edit-msg"),
    `正在查看历史快照 #${id}：其中灯串与参数已冻结。修改左侧并“保存灯串/参数”只会更新当前设计，不会改变此记录。`,
    "info");
  renderResult(rec);
  document.querySelectorAll("#history-list .hist-item").forEach((el) => el.classList.remove("active"));
  await loadHistory(id);
}

// ---------- 初始化 ----------
async function init() {
  const state = await api("/api/state");
  lights = state.lights.map((l) => ({ name: l.name, pattern: l.pattern }));
  settings = state.settings;
  $("#inp-w").value = settings.w;
  $("#inp-k").value = settings.k;
  renderLightRows();
  await loadHistory();
  await loadInversions();
}

init().catch((e) => {
  $("#result-empty").textContent = "初始化失败：" + e.message;
});
