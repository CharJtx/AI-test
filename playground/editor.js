// ── State ────────────────────────────────────────────────
let currentScene = null;
let resources = [];
let sceneData = {
  config: { grid: { cols: 5, rows: 2 } },
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
  initialState: $('#initial-state'),
  stateList:    $('#state-list'),
  btnAddState:  $('#btn-add-state'),
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
  el.btnAddState.addEventListener('click', () => addState());

  el.gridCols.addEventListener('change', () => { sceneData.config.grid.cols = +el.gridCols.value; markDirty(); });
  el.gridRows.addEventListener('change', () => { sceneData.config.grid.rows = +el.gridRows.value; markDirty(); });
  el.initialState.addEventListener('change', () => { sceneData.initialState = el.initialState.value; markDirty(); });

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
  } else {
    sceneData = { config: { grid: { cols: 5, rows: 2 } }, resources: {}, states: {}, initialState: '' };
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
  el.resCount.textContent = resources.length;
  if (!resources.length) { el.resList.innerHTML = ''; return; }

  el.resList.innerHTML = resources.map(r => {
    const id = Object.entries(sceneData.resources).find(([, fn]) => fn === r.name)?.[0] || '';
    return `<div class="res-item">
      <input class="text-input" style="width:80px;font-size:11px" value="${esc(id)}" placeholder="资源ID"
        data-filename="${esc(r.name)}" onchange="renameResource(this)">
      <span class="res-name" title="${esc(r.name)}">${esc(r.name)}</span>
      <span class="res-size">${fmtSize(r.size)}</span>
      <button class="res-del" onclick="deleteResource('${esc(r.name)}')" title="删除">×</button>
    </div>`;
  }).join('');
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
  sceneData.states[id] = { video: '', loop: true, next: null, on_click: [] };
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
          <div class="toggle-row">
            <input type="checkbox" ${s.loop ? 'checked' : ''} onchange="updState('${esc(id)}','loop',this.checked)">
            <span>循环播放（等待点击交互）</span>
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
function addAction(stateId) {
  const s = sceneData.states[stateId];
  if (!s.on_click) s.on_click = [];
  const { cols, rows } = sceneData.config.grid;
  s.on_click.push({
    regions: { rows: Array.from({ length: rows }, (_, i) => i), cols: Array.from({ length: cols }, (_, i) => i) },
    target: '',
  });
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
    action.regions = { rows: Array.from({ length: rows }, (_, i) => i), cols: Array.from({ length: cols }, (_, i) => i) };
  }
  markDirty();
  renderStates();
}

function toggleRegionRow(stateId, idx, r) {
  const action = sceneData.states[stateId].on_click[idx];
  if (action.regions === '*') return;
  const rowSet = new Set(action.regions.rows);
  if (rowSet.has(r)) rowSet.delete(r); else rowSet.add(r);
  action.regions.rows = [...rowSet].sort((a, b) => a - b);
  markDirty();
  renderStates();
}

function toggleRegionCol(stateId, idx, c) {
  const action = sceneData.states[stateId].on_click[idx];
  if (action.regions === '*') return;
  const colSet = new Set(action.regions.cols);
  if (colSet.has(c)) colSet.delete(c); else colSet.add(c);
  action.regions.cols = [...colSet].sort((a, b) => a - b);
  markDirty();
  renderStates();
}

function setActionTarget(stateId, idx, target) {
  sceneData.states[stateId].on_click[idx].target = target;
  markDirty();
}

function renderActions(stateId, actions) {
  if (!actions.length) return '<div style="color:#3f3f46;font-size:12px">暂无规则</div>';
  const { cols, rows } = sceneData.config.grid;

  return actions.map((a, idx) => {
    const isWild = a.regions === '*';
    let gridHtml = '';
    if (!isWild) {
      const rowSet = new Set(a.regions.rows);
      const colSet = new Set(a.regions.cols);

      // Column headers
      let colHeaders = '<div class="region-corner"></div>';
      for (let c = 0; c < cols; c++) {
        const sel = colSet.has(c) ? ' selected' : '';
        colHeaders += `<div class="region-col-hdr${sel}" onclick="toggleRegionCol('${esc(stateId)}',${idx},${c})">C${c}</div>`;
      }

      // Rows with row header + cells
      let rowsHtml = '';
      for (let r = 0; r < rows; r++) {
        const rSel = rowSet.has(r) ? ' selected' : '';
        rowsHtml += `<div class="region-row-hdr${rSel}" onclick="toggleRegionRow('${esc(stateId)}',${idx},${r})">R${r}</div>`;
        for (let c = 0; c < cols; c++) {
          const active = rowSet.has(r) && colSet.has(c) ? ' selected' : '';
          rowsHtml += `<div class="region-cell${active}"></div>`;
        }
      }

      gridHtml = `<div class="region-grid" style="grid-template-columns:40px repeat(${cols},1fr)">
        ${colHeaders}${rowsHtml}
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

function handleRename(input, oldId) {
  renameState(oldId, input.value);
}

function toggleCard(header) {
  header.parentElement.classList.toggle('open');
}

function syncToUI() {
  el.gridCols.value = sceneData.config.grid.cols;
  el.gridRows.value = sceneData.config.grid.rows;
  renderResources();
  renderStates();
  updateInitialSelect();
  updatePreviewLink();
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
  sceneData = { config: { grid: { cols: 5, rows: 2 } }, resources: {}, states: {}, initialState: '' };
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
