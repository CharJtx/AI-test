// ── State ────────────────────────────────────────────────
let currentScene = null;
let resources = [];
let sceneData = {
  config: { grid: { cols: 5, rows: 2 }, loadingBg: '', kolId: '' },
  resources: {},
  states: {},
  initialState: '',
};
let stateOrder = [];
let dirty = false;

// ── DOM refs ─────────────────────────────────────────────
const $ = (s) => document.querySelector(s);
const el = {
  sceneSelect:  $('#scene-select'),
  btnNew:       $('#btn-new-scene'),
  btnSave:      $('#btn-save'),
  linkPreview:  $('#link-preview'),
  gridCols:     $('#grid-cols'),
  gridRows:     $('#grid-rows'),
  uploadZone:   $('#upload-zone'),
  fileInput:    $('#file-input'),
  btnBrowse:    $('#btn-browse'),
  resList:      $('#resource-list'),
  resCount:     $('#res-count'),
  urlInput:     $('#url-input'),
  btnAddUrl:    $('#btn-add-url'),
  kolId:        $('#kol-id'),
  loadingBg:    $('#loading-bg'),
  loadingBgPreview: $('#loading-bg-preview'),
  initialState: $('#initial-state'),
  stateList:    $('#state-list'),
  btnAddState:  $('#btn-add-state'),
  btnDownloadJson: $('#btn-download-json'),
  toast:        $('#toast'),
};

// ── Init ─────────────────────────────────────────────────
(async () => {
  await loadSceneList();
  bindEvents();

  const params = new URLSearchParams(location.search);
  if (params.get('scene')) {
    el.sceneSelect.value = params.get('scene');
    await selectScene(params.get('scene'));
  }
})();

// ── Events ───────────────────────────────────────────────
function bindEvents() {
  el.sceneSelect.addEventListener('change', () => selectScene(el.sceneSelect.value));
  el.btnNew.addEventListener('click', createScene);
  el.btnSave.addEventListener('click', saveScene);
  el.btnDownloadJson.addEventListener('click', downloadSceneJson);
  el.btnAddState.addEventListener('click', () => addState());

  el.gridCols.addEventListener('change', () => { sceneData.config.grid.cols = +el.gridCols.value; markDirty(); });
  el.gridRows.addEventListener('change', () => { sceneData.config.grid.rows = +el.gridRows.value; markDirty(); });
  el.initialState.addEventListener('change', () => { sceneData.initialState = el.initialState.value; markDirty(); });
  el.kolId.addEventListener('input', () => { sceneData.config.kolId = el.kolId.value.trim(); markDirty(); });
  el.loadingBg.addEventListener('input', () => {
    if (!sceneData.config) sceneData.config = {};
    sceneData.config.loadingBg = el.loadingBg.value.trim();
    markDirty();
    updateLoadingBgPreview();
  });

  // File upload
  el.btnBrowse.addEventListener('click', (e) => { e.stopPropagation(); el.fileInput.click(); });
  el.uploadZone.addEventListener('click', () => el.fileInput.click());
  el.fileInput.addEventListener('change', () => { uploadFiles(el.fileInput.files); el.fileInput.value = ''; });

  el.uploadZone.addEventListener('dragover', (e) => { e.preventDefault(); el.uploadZone.classList.add('drag-over'); });
  el.uploadZone.addEventListener('dragleave', () => el.uploadZone.classList.remove('drag-over'));
  el.uploadZone.addEventListener('drop', (e) => {
    e.preventDefault();
    el.uploadZone.classList.remove('drag-over');
    uploadFiles(e.dataTransfer.files);
  });

  // URL resource
  el.btnAddUrl.addEventListener('click', addUrlResource);
  el.urlInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') addUrlResource(); });
}

// ── Scene CRUD ───────────────────────────────────────────
async function loadSceneList() {
  const res = await api('GET', '/api/playground/scenes');
  el.sceneSelect.innerHTML = '<option value="">-- 选择场景 --</option>';
  for (const name of res.scenes) {
    el.sceneSelect.innerHTML += `<option value="${esc(name)}">${esc(name)}</option>`;
  }
}

async function createScene() {
  const name = prompt('输入场景名称（英文/数字/中文均可）：');
  if (!name?.trim()) return;
  try {
    await api('POST', '/api/playground/scenes', { name: name.trim() });
    await loadSceneList();
    el.sceneSelect.value = name.trim();
    await selectScene(name.trim());
    toast('场景已创建');
  } catch (e) { toast(e.message, true); }
}

async function selectScene(name) {
  if (!name) { currentScene = null; resetEditor(); return; }
  currentScene = name;
  history.replaceState(null, '', `?scene=${encodeURIComponent(name)}`);

  const [dataRes, resRes] = await Promise.all([
    api('GET', `/api/playground/scenes/${encodeURIComponent(name)}/data`),
    api('GET', `/api/playground/scenes/${encodeURIComponent(name)}/resources`),
  ]);

  resources = resRes.files || [];

  if (dataRes && dataRes.config) {
    sceneData = dataRes;
    stateOrder = Object.keys(sceneData.states);
    for (const s of Object.values(sceneData.states)) {
      if (s.on_click) s.on_click.forEach(a => {
      a.regions = normRegions(a.regions);
      if (!a.pulse_cells) a.pulse_cells = [];
    });
    }
  } else {
    sceneData = { config: { grid: { cols: 5, rows: 2 }, loadingBg: '' }, resources: {}, states: {}, initialState: '' };
    stateOrder = [];
  }

  syncToUI();
  dirty = false;
  updateSaveBtn();
}

async function saveScene() {
  if (!currentScene) return;
  try {
    await api('PUT', `/api/playground/scenes/${encodeURIComponent(currentScene)}/data`, sceneData);
    dirty = false;
    updateSaveBtn();
    toast('保存成功');
  } catch (e) { toast('保存失败: ' + e.message, true); }
}

// ── Resources ────────────────────────────────────────────
async function uploadFiles(fileList) {
  if (!currentScene || !fileList.length) return;
  el.uploadZone.classList.add('uploading');

  for (const file of fileList) {
    const form = new FormData();
    form.append('file', file);
    try {
      const res = await fetch(`/api/playground/scenes/${encodeURIComponent(currentScene)}/upload`, { method: 'POST', body: form });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      resources.push(data);

      const id = file.name.replace(/\.[^.]+$/, '');
      if (!sceneData.resources[id]) {
        sceneData.resources[id] = file.name;
        markDirty();
      }
    } catch (e) { toast(`上传失败: ${file.name}`, true); }
  }

  el.uploadZone.classList.remove('uploading');
  renderResources();
  renderAllStateSelects();
  toast(`${fileList.length} 个文件上传完成`);
}

async function deleteResource(filename) {
  if (!confirm(`确定删除 ${filename}？`)) return;
  try {
    await api('DELETE', `/api/playground/scenes/${encodeURIComponent(currentScene)}/resources?filename=${encodeURIComponent(filename)}`);
    resources = resources.filter(r => r.name !== filename);
    for (const [id, fn] of Object.entries(sceneData.resources)) {
      if (fn === filename) delete sceneData.resources[id];
    }
    markDirty();
    renderResources();
    renderAllStateSelects();
  } catch (e) { toast(e.message, true); }
}

function renderResources() {
  // 合并：上传的文件 + URL 资源（从 sceneData.resources 中找出 URL 类型的）
  const allRes = [...resources];
  for (const [id, val] of Object.entries(sceneData.resources)) {
    if (typeof val === 'string' && (val.startsWith('http://') || val.startsWith('https://'))) {
      if (!allRes.some(r => r.name === val)) {
        allRes.push({ name: val, size: 0, isUrl: true });
      }
    }
  }

  el.resCount.textContent = allRes.length;
  if (!allRes.length) { el.resList.innerHTML = ''; return; }
  el.resList.innerHTML = allRes.map(r => {
    const isUrl = r.isUrl || r.name.startsWith('http://') || r.name.startsWith('https://');
    const id = Object.entries(sceneData.resources).find(([, fn]) => fn === r.name)?.[0] || '';
    const displayName = isUrl ? (r.name.length > 50 ? r.name.slice(0, 50) + '…' : r.name) : r.name;
    const sizeText = isUrl ? '<span class="res-size url-badge">URL</span>' : `<span class="res-size">${fmtSize(r.size)}</span>`;
    const delBtn = isUrl
      ? `<button class="res-del" onclick="removeUrlResource('${esc(r.name)}')" title="移除">×</button>`
      : `<button class="res-del" onclick="deleteResource('${esc(r.name)}')" title="删除">×</button>`;
    return `<div class="res-item${isUrl ? ' is-url' : ''}">
      <input class="text-input" style="width:80px;font-size:11px" value="${esc(id)}" placeholder="资源ID"
        data-filename="${esc(r.name)}" onchange="renameResource(this)">
      <span class="res-name" title="${esc(r.name)}">${esc(displayName)}</span>
      ${sizeText}
      ${delBtn}
    </div>`;
  }).join('');
}

function removeUrlResource(url) {
  for (const [id, fn] of Object.entries(sceneData.resources)) {
    if (fn === url) delete sceneData.resources[id];
  }
  resources = resources.filter(r => r.name !== url);
  markDirty();
  renderResources();
  renderAllStateSelects();
}

function addUrlResource() {
  const url = el.urlInput.value.trim();
  if (!url) return;
  if (!url.startsWith('http://') && !url.startsWith('https://')) {
    toast('请输入完整的 URL（以 http:// 或 https:// 开头）', true);
    return;
  }
  // 用 URL 的最后一段路径作为默认 ID
  const urlPath = new URL(url).pathname;
  const defaultId = urlPath.split('/').pop()?.replace(/\.[^.]+$/, '') || `url_${Date.now()}`;
  const id = prompt('设置资源 ID：', defaultId);
  if (!id?.trim()) return;

  // URL 资源直接存入 resources 映射，值为完整 URL
  sceneData.resources[id.trim()] = url;
  // 同时加入 resources 列表用于 UI 显示
  resources.push({ name: url, size: 0, isUrl: true });
  markDirty();
  renderResources();
  renderAllStateSelects();
  el.urlInput.value = '';
  toast(`URL 资源「${id.trim()}」已添加`);
}

function renameResource(input) {
  const filename = input.dataset.filename;
  const newId = input.value.trim();
  for (const [id, fn] of Object.entries(sceneData.resources)) {
    if (fn === filename) delete sceneData.resources[id];
  }
  if (newId) sceneData.resources[newId] = filename;
  markDirty();
  renderAllStateSelects();
}

// ── States ───────────────────────────────────────────────
function addState() {
  const base = 'state';
  let idx = stateOrder.length + 1;
  while (sceneData.states[base + idx]) idx++;
  const id = base + idx;
  sceneData.states[id] = { video: '', loop: true, next: null, sounds: [], on_click: [], is_basic: false, require_login: false, require_pay: false };
  stateOrder.push(id);
  if (!sceneData.initialState) sceneData.initialState = id;
  markDirty();
  renderStates();
  updateInitialSelect();
}

function removeState(id) {
  if (!confirm(`确定删除状态 ${id}？`)) return;
  delete sceneData.states[id];
  stateOrder = stateOrder.filter(s => s !== id);
  if (sceneData.initialState === id) sceneData.initialState = stateOrder[0] || '';
  for (const state of Object.values(sceneData.states)) {
    if (state.next === id) state.next = null;
    if (state.on_click) state.on_click = state.on_click.filter(a => a.target !== id);
  }
  markDirty();
  renderStates();
  updateInitialSelect();
}

function renameState(oldId, newId) {
  newId = newId.trim();
  if (!newId || newId === oldId || sceneData.states[newId]) return oldId;
  sceneData.states[newId] = sceneData.states[oldId];
  delete sceneData.states[oldId];
  stateOrder = stateOrder.map(s => s === oldId ? newId : s);
  if (sceneData.initialState === oldId) sceneData.initialState = newId;
  for (const state of Object.values(sceneData.states)) {
    if (state.next === oldId) state.next = newId;
    if (state.on_click) state.on_click.forEach(a => { if (a.target === oldId) a.target = newId; });
  }
  markDirty();
  renderStates();
  updateInitialSelect();
  return newId;
}

function renderStates() {
  if (!stateOrder.length) {
    el.stateList.innerHTML = '<div class="empty-hint">点击「+ 添加状态」开始构建流程</div>';
    return;
  }

  el.stateList.innerHTML = stateOrder.map(id => {
    const s = sceneData.states[id];
    const isInit = sceneData.initialState === id;
    const tags = [
      s.loop ? '<span class="tag loop">LOOP</span>' : '<span class="tag once">ONCE</span>',
      isInit ? '<span class="tag initial">初始</span>' : '',
      s.is_basic ? '<span class="tag basic">⭐基础</span>' : '',
      s.require_login ? '<span class="tag login">🔒登录</span>' : '',
      s.require_pay ? '<span class="tag pay">💰付费</span>' : '',
    ].join('');

    return `<div class="state-card open${isInit ? ' is-initial' : ''}" data-state="${esc(id)}">
      <div class="state-header" onclick="toggleCard(this)">
        <span class="state-id-display">${esc(id)}</span>
        <span class="state-badges">${tags}</span>
        <span class="state-collapse">▸</span>
      </div>
      <div class="state-body">
        <div class="state-field">
          <label>状态 ID</label>
          <input class="text-input" value="${esc(id)}" onchange="handleRename(this, '${esc(id)}')">
        </div>
        <div class="state-field">
          <label>视频资源</label>
          <select onchange="updState('${esc(id)}','video',this.value)">
            <option value="">-- 选择 --</option>
            ${resourceOptions(s.video)}
          </select>
        </div>
        <div class="state-field">
          <label>声音资源</label>
          <div class="sounds-list" id="sounds-${esc(id)}">
            ${(s.sounds || []).map((snd, si) => `<div class="sound-item">
              <select onchange="updateSound('${esc(id)}',${si},this.value)">
                <option value="">-- 选择 --</option>
                ${resourceOptions(snd)}
              </select>
              <button class="btn danger small" onclick="removeSound('${esc(id)}',${si})">×</button>
            </div>`).join('')}
          </div>
          <button class="btn small" onclick="addSound('${esc(id)}')">+ 添加声音</button>
        </div>
        <div class="state-field">
          <div class="toggle-row">
            <input type="checkbox" ${s.loop ? 'checked' : ''} onchange="updState('${esc(id)}','loop',this.checked)">
            <span>循环播放（等待点击交互）</span>
          </div>
        </div>
        <div class="state-field gate-flags">
          <div class="toggle-row">
            <input type="checkbox" ${s.is_basic ? 'checked' : ''} onchange="updState('${esc(id)}','is_basic',this.checked)">
            <span>⭐ 基础状态</span>
          </div>
          <div class="toggle-row">
            <input type="checkbox" ${s.require_login ? 'checked' : ''} onchange="updState('${esc(id)}','require_login',this.checked)">
            <span>🔒 需要登录</span>
          </div>
          <div class="toggle-row">
            <input type="checkbox" ${s.require_pay ? 'checked' : ''} onchange="updState('${esc(id)}','require_pay',this.checked)">
            <span>💰 需要付费</span>
          </div>
        </div>
        ${!s.loop ? `<div class="state-field">
          <label>播完跳转</label>
          <select onchange="updState('${esc(id)}','next',this.value||null)">
            <option value="">-- 无 --</option>
            ${stateOptions(s.next)}
          </select>
        </div>` : ''}
        <div class="click-actions-section">
          <div class="click-actions-header">
            <span>点击跳转规则</span>
            <button class="btn small" onclick="addAction('${esc(id)}')">+ 规则</button>
          </div>
          ${renderActions(id, s.on_click || [])}
        </div>
        <div class="state-actions">
          <button class="btn danger small" onclick="removeState('${esc(id)}')">删除状态</button>
        </div>
      </div>
    </div>`;
  }).join('');
}

function resourceOptions(selected) {
  return Object.keys(sceneData.resources).map(id =>
    `<option value="${esc(id)}" ${id === selected ? 'selected' : ''}>${esc(id)} (${esc(sceneData.resources[id])})</option>`
  ).join('');
}

function stateOptions(selected) {
  return stateOrder.map(id =>
    `<option value="${esc(id)}" ${id === selected ? 'selected' : ''}>${esc(id)}</option>`
  ).join('');
}

// ── Click Actions ────────────────────────────────────────

function normRegions(regions) {
  if (regions === '*') return '*';
  if (Array.isArray(regions)) return regions;
  const cells = [];
  for (const r of (regions.rows || [])) for (const c of (regions.cols || [])) cells.push([r, c]);
  return cells;
}

function cellKey(r, c) { return r + ',' + c; }

function addAction(stateId) {
  const s = sceneData.states[stateId];
  if (!s.on_click) s.on_click = [];
  const { cols, rows } = sceneData.config.grid;
  const allCells = [];
  for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) allCells.push([r, c]);
  s.on_click.push({ regions: allCells, target: '', pulse_cells: [] });
  markDirty();
  renderStates();
}

function removeAction(stateId, idx) {
  sceneData.states[stateId].on_click.splice(idx, 1);
  markDirty();
  renderStates();
}

function setActionWildcard(stateId, idx, isWild) {
  const action = sceneData.states[stateId].on_click[idx];
  if (isWild) {
    action.regions = '*';
  } else {
    const { cols, rows } = sceneData.config.grid;
    const allCells = [];
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) allCells.push([r, c]);
    action.regions = allCells;
  }
  cleanStalePulseCells(action);
  markDirty();
  renderStates();
}

function toggleRegionCell(stateId, idx, r, c) {
  const action = sceneData.states[stateId].on_click[idx];
  if (action.regions === '*') return;
  const key = cellKey(r, c);
  const exists = action.regions.findIndex(([cr, cc]) => cr === r && cc === c);
  if (exists >= 0) {
    action.regions.splice(exists, 1);
  } else {
    action.regions.push([r, c]);
    action.regions.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  }
  cleanStalePulseCells(action);
  markDirty();
  renderStates();
}

function toggleRegionRow(stateId, idx, r) {
  const action = sceneData.states[stateId].on_click[idx];
  if (action.regions === '*') return;
  const { cols } = sceneData.config.grid;
  const set = new Set(action.regions.map(([cr, cc]) => cellKey(cr, cc)));
  const allInRow = Array.from({ length: cols }, (_, c) => c).every(c => set.has(cellKey(r, c)));
  if (allInRow) {
    for (let c = 0; c < cols; c++) set.delete(cellKey(r, c));
  } else {
    for (let c = 0; c < cols; c++) set.add(cellKey(r, c));
  }
  action.regions = [...set].map(k => k.split(',').map(Number)).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  cleanStalePulseCells(action);
  markDirty();
  renderStates();
}

function toggleRegionCol(stateId, idx, c) {
  const action = sceneData.states[stateId].on_click[idx];
  if (action.regions === '*') return;
  const { rows } = sceneData.config.grid;
  const set = new Set(action.regions.map(([cr, cc]) => cellKey(cr, cc)));
  const allInCol = Array.from({ length: rows }, (_, r) => r).every(r => set.has(cellKey(r, c)));
  if (allInCol) {
    for (let r = 0; r < rows; r++) set.delete(cellKey(r, c));
  } else {
    for (let r = 0; r < rows; r++) set.add(cellKey(r, c));
  }
  action.regions = [...set].map(k => k.split(',').map(Number)).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  cleanStalePulseCells(action);
  markDirty();
  renderStates();
}

function setActionTarget(stateId, idx, target) {
  sceneData.states[stateId].on_click[idx].target = target;
  markDirty();
}

// 清理规则中不在 region 内的 pulse_cells（当 regions 变更后调用）
function cleanStalePulseCells(action) {
  if (!action.pulse_cells || !action.pulse_cells.length) return;
  if (action.regions === '*') return; // 通配：全部保留
  const regionSet = new Set(action.regions.map(([r, c]) => cellKey(r, c)));
  action.pulse_cells = action.pulse_cells.filter(([r, c]) => regionSet.has(cellKey(r, c)));
}

function togglePulseCell(stateId, idx, r, c) {
  const action = sceneData.states[stateId].on_click[idx];
  if (!action.pulse_cells) action.pulse_cells = [];
  const exists = action.pulse_cells.findIndex(([pr, pc]) => pr === r && pc === c);
  if (exists >= 0) {
    action.pulse_cells.splice(exists, 1);
  } else {
    // 只允许选择 regions 中已有的 cell
    const inRegion = action.regions === '*' || action.regions.some(([rr, rc]) => rr === r && rc === c);
    if (inRegion) {
      action.pulse_cells.push([r, c]);
      action.pulse_cells.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    }
  }
  markDirty();
  renderStates();
}

function renderActions(stateId, actions) {
  if (!actions.length) return '<div style="color:#3f3f46;font-size:12px">暂无规则</div>';
  const { cols, rows } = sceneData.config.grid;

  return actions.map((a, idx) => {
    const isWild = a.regions === '*';
    let gridHtml = '';
    if (!isWild) {
      const selSet = new Set(a.regions.map(([r, c]) => cellKey(r, c)));

      // Column headers (click to toggle entire column)
      let colHeaders = '<div class="region-corner"></div>';
      for (let c = 0; c < cols; c++) {
        const allSel = Array.from({ length: rows }, (_, r) => r).every(r => selSet.has(cellKey(r, c)));
        colHeaders += `<div class="region-col-hdr${allSel ? ' selected' : ''}" onclick="toggleRegionCol('${esc(stateId)}',${idx},${c})">C${c}</div>`;
      }

      // Row headers + individual cells
      let rowsHtml = '';
      for (let r = 0; r < rows; r++) {
        const allSel = Array.from({ length: cols }, (_, c) => c).every(c => selSet.has(cellKey(r, c)));
        rowsHtml += `<div class="region-row-hdr${allSel ? ' selected' : ''}" onclick="toggleRegionRow('${esc(stateId)}',${idx},${r})">R${r}</div>`;
        for (let c = 0; c < cols; c++) {
          const sel = selSet.has(cellKey(r, c)) ? ' selected' : '';
          rowsHtml += `<div class="region-cell${sel}" onclick="toggleRegionCell('${esc(stateId)}',${idx},${r},${c})"></div>`;
        }
      }

      gridHtml = `<div class="region-grid" style="grid-template-columns:40px repeat(${cols},1fr)">
        ${colHeaders}${rowsHtml}
      </div>`;
    }

    // Pulse cell selector grid
    const pulseSet = new Set((a.pulse_cells || []).map(([r, c]) => cellKey(r, c)));
    const regionSet = isWild
      ? new Set(Array.from({ length: rows }, (_, r) => Array.from({ length: cols }, (_, c) => cellKey(r, c))).flat())
      : new Set(a.regions.map(([r, c]) => cellKey(r, c)));

    let pulseGridHtml = '';
    if (regionSet.size > 0) {
      let pCells = '';
      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          const key = cellKey(r, c);
          const inRegion = regionSet.has(key);
          const isPulse = pulseSet.has(key);
          const cls = inRegion ? (isPulse ? 'pulse-cell available active' : 'pulse-cell available') : 'pulse-cell';
          const onclick = inRegion ? `onclick="togglePulseCell('${esc(stateId)}',${idx},${r},${c})"` : '';
          pCells += `<div class="${cls}" ${onclick}>${isPulse ? '●' : ''}</div>`;
        }
      }
      pulseGridHtml = `<div class="pulse-section">
        <label class="pulse-label">触发点（脉冲圆点）</label>
        <div class="pulse-grid" style="grid-template-columns:repeat(${cols},1fr)">${pCells}</div>
        <div class="pulse-hint">点击已选区域中的格子来设置脉冲提示点</div>
      </div>`;
    }

    return `<div class="action-card">
      <div class="action-top">
        <span class="action-label">规则 ${idx + 1}</span>
        <button class="btn danger small" onclick="removeAction('${esc(stateId)}',${idx})">删除</button>
      </div>
      <div class="action-wildcard">
        <label><input type="checkbox" ${isWild ? 'checked' : ''} onchange="setActionWildcard('${esc(stateId)}',${idx},this.checked)"> 匹配全部区域（*通配）</label>
      </div>
      ${gridHtml}
      ${pulseGridHtml}
      <div class="action-target">
        <label>跳转到 →</label>
        <select onchange="setActionTarget('${esc(stateId)}',${idx},this.value)">
          <option value="">-- 选择 --</option>
          ${stateOptions(a.target)}
        </select>
      </div>
    </div>`;
  }).join('');
}

// ── Helpers ──────────────────────────────────────────────
function updState(id, field, value) {
  sceneData.states[id][field] = value;
  if (field === 'loop' && value) sceneData.states[id].next = null;
  markDirty();
  renderStates();
}

function addSound(stateId) {
  const s = sceneData.states[stateId];
  if (!s.sounds) s.sounds = [];
  s.sounds.push('');
  markDirty();
  renderStates();
}

function removeSound(stateId, idx) {
  sceneData.states[stateId].sounds.splice(idx, 1);
  markDirty();
  renderStates();
}

function updateSound(stateId, idx, value) {
  sceneData.states[stateId].sounds[idx] = value;
  markDirty();
}

function handleRename(input, oldId) {
  renameState(oldId, input.value);
}

function toggleCard(header) {
  header.parentElement.classList.toggle('open');
}

function syncToUI() {
  el.gridCols.value = sceneData.config.grid.cols;
  el.gridRows.value = sceneData.config.grid.rows;
  el.kolId.value = sceneData.config.kolId || '';
  el.loadingBg.value = sceneData.config.loadingBg || '';
  renderResources();
  renderStates();
  updateInitialSelect();
  updatePreviewLink();
  updateLoadingBgPreview();
}

function updateLoadingBgPreview() {
  const url = sceneData.config.loadingBg || '';
  if (url) {
    el.loadingBgPreview.hidden = false;
    el.loadingBgPreview.innerHTML = `<img src="${esc(url)}" alt="preview" onerror="this.parentElement.innerHTML='<span style=color:#f87171>图片加载失败</span>'">`;
  } else {
    el.loadingBgPreview.hidden = true;
    el.loadingBgPreview.innerHTML = '';
  }
}

function updateInitialSelect() {
  el.initialState.innerHTML = '<option value="">-- 选择 --</option>' + stateOrder.map(id =>
    `<option value="${esc(id)}" ${sceneData.initialState === id ? 'selected' : ''}>${esc(id)}</option>`
  ).join('');
}

function renderAllStateSelects() {
  renderStates();
}

function markDirty() {
  dirty = true;
  updateSaveBtn();
}

function updateSaveBtn() {
  el.btnSave.disabled = !currentScene || !dirty;
  el.btnDownloadJson.disabled = !currentScene;
}

function downloadSceneJson() {
  if (!currentScene) return;
  const json = JSON.stringify(sceneData, null, 2);
  const blob = new Blob([json], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${currentScene}-scene-data.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function updatePreviewLink() {
  if (currentScene) {
    el.linkPreview.href = `/playground/?scene=${encodeURIComponent(currentScene)}`;
    el.linkPreview.style.display = '';
  } else {
    el.linkPreview.style.display = 'none';
  }
}

function resetEditor() {
  resources = [];
  sceneData = { config: { grid: { cols: 5, rows: 2 }, loadingBg: '' }, resources: {}, states: {}, initialState: '' };
  stateOrder = [];
  dirty = false;
  syncToUI();
  updateSaveBtn();
  updatePreviewLink();
  el.stateList.innerHTML = '<div class="empty-hint">选择或创建场景后开始编辑</div>';
}

// ── API helper ───────────────────────────────────────────
async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body && method !== 'GET') {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  if (method === 'DELETE' && res.ok) return {};
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function esc(str) {
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function fmtSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1048576).toFixed(1) + ' MB';
}

let toastTimer;
function toast(msg, isError) {
  el.toast.textContent = msg;
  el.toast.className = 'toast show' + (isError ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.toast.className = 'toast', 2500);
}
