// ── State ───────────────────────────────────────────────
const state = {
  models: [],
  selectedModels: [],
  conversations: {},   // modelId -> [{role, content}]
  presets: [],
  worldbooks: [],
  characters: [],
  activeCharId: null,  // currently loaded character card id
  streaming: false,
};

// ── DOM refs ────────────────────────────────────────────
const $systemPrompt = document.getElementById("system-prompt");
const $modelSearch = document.getElementById("model-search");
const $modelList = document.getElementById("model-list");
const $selectedTags = document.getElementById("selected-tags");
const $chatColumns = document.getElementById("chat-columns");
const $userInput = document.getElementById("user-input");
const $btnSend = document.getElementById("btn-send");
const $btnClear = document.getElementById("btn-clear");
const $presetSelect = document.getElementById("preset-select");
const $btnSavePreset = document.getElementById("btn-save-preset");
const $btnDeletePreset = document.getElementById("btn-delete-preset");

const $temperature = document.getElementById("param-temperature");
const $topP = document.getElementById("param-top-p");
const $maxTokens = document.getElementById("param-max-tokens");
const $freqPenalty = document.getElementById("param-freq-penalty");
const $presPenalty = document.getElementById("param-pres-penalty");

// ── Init ────────────────────────────────────────────────
async function init() {
  setupSliderLabels();
  await Promise.all([loadModels(), loadPresets(), loadWorldbooks(), loadCharacters()]);
  setupEvents();
}

function setupSliderLabels() {
  const pairs = [
    [$temperature, "temp-val"],
    [$topP, "topp-val"],
    [$freqPenalty, "freq-val"],
    [$presPenalty, "pres-val"],
  ];
  for (const [slider, labelId] of pairs) {
    const label = document.getElementById(labelId);
    slider.addEventListener("input", () => {
      label.textContent = parseFloat(slider.value).toFixed(2);
    });
  }
}

const $filterUnmoderated = document.getElementById("filter-unmoderated");
const $modelCount = document.getElementById("model-count");
const $wbList = document.getElementById("wb-list");
const $btnCreateWb = document.getElementById("btn-create-wb");
const $btnImportWb = document.getElementById("btn-import-wb");
const $wbFileInput = document.getElementById("wb-file-input");

const $charSelect = document.getElementById("char-select");
const $btnNewChar = document.getElementById("btn-new-char");
const $btnGenChar = document.getElementById("btn-gen-char");  // may be null on cached HTML
const $btnImportChar = document.getElementById("btn-import-char");
const $charFileInput = document.getElementById("char-file-input");
const $charInfo = document.getElementById("char-info");
const $charTags = document.getElementById("char-tags");
const $btnEditChar = document.getElementById("btn-edit-char");
const $btnDeleteChar = document.getElementById("btn-delete-char");
const $btnSendGreeting = document.getElementById("btn-send-greeting");

function setupEvents() {
  $modelSearch.addEventListener("input", renderModelList);
  $filterUnmoderated.addEventListener("change", renderModelList);
  $btnSend.addEventListener("click", sendMessage);
  $btnClear.addEventListener("click", clearConversations);
  $btnSavePreset.addEventListener("click", savePresetDialog);
  $btnDeletePreset.addEventListener("click", deletePreset);
  $presetSelect.addEventListener("change", applyPreset);
  $btnCreateWb.addEventListener("click", () => openWbEditor(null));
  $btnImportWb.addEventListener("click", () => $wbFileInput.click());
  $wbFileInput.addEventListener("change", importWorldbook);

  $charSelect.addEventListener("change", onCharSelect);
  $btnNewChar.addEventListener("click", () => openCharEditor(null));
  $btnGenChar?.addEventListener("click", openCharGenerator);
  $btnImportChar.addEventListener("click", () => $charFileInput.click());
  $charFileInput.addEventListener("change", importCharacter);
  $btnEditChar.addEventListener("click", () => {
    const c = getActiveChar();
    if (c) openCharEditor(c);
  });
  $btnDeleteChar.addEventListener("click", deleteCharacter);
  $btnSendGreeting.addEventListener("click", sendGreeting);

  $userInput.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key === "Enter") {
      e.preventDefault();
      sendMessage();
    }
  });
}

// ── Models ──────────────────────────────────────────────
async function loadModels() {
  try {
    const resp = await fetch("/api/models");
    const data = await resp.json();
    state.models = data.models;
    renderModelList();
  } catch (e) {
    console.error("Failed to load models:", e);
  }
}

function formatCtx(n) {
  if (!n) return "";
  if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
  if (n >= 1000) return (n / 1000).toFixed(0) + "K";
  return String(n);
}

function formatPrice(pricing) {
  if (!pricing) return "";
  const prompt = parseFloat(pricing.prompt || 0);
  const completion = parseFloat(pricing.completion || 0);
  if (prompt === 0 && completion === 0) return "Free";
  const perMIn = (prompt * 1_000_000).toFixed(2);
  const perMOut = (completion * 1_000_000).toFixed(2);
  return `$${perMIn}/$${perMOut}`;
}

function renderModelList() {
  const query = $modelSearch.value.toLowerCase();
  const onlyUnmoderated = $filterUnmoderated.checked;

  let filtered = state.models.filter(
    (m) => m.id.toLowerCase().includes(query) || m.name.toLowerCase().includes(query)
  );
  if (onlyUnmoderated) {
    filtered = filtered.filter((m) => !m.is_moderated);
  }

  $modelCount.textContent = `${filtered.length} / ${state.models.length}`;

  $modelList.innerHTML = filtered
    .slice(0, 150)
    .map((m) => {
      const sel = state.selectedModels.includes(m.id) ? "selected" : "";
      const moderationBadge = m.is_moderated
        ? '<span class="badge badge-moderated">审核</span>'
        : '<span class="badge badge-unmoderated">无审核</span>';
      const price = formatPrice(m.pricing);
      const priceBadge = price === "Free" ? '<span class="badge badge-free">Free</span>' : "";
      const ctx = formatCtx(m.context_length);

      return `<div class="model-item ${sel}" data-id="${m.id}" title="${escapeHtml(m.description || m.id)}">
        <span class="check">${sel ? "✓" : ""}</span>
        <div class="model-info">
          <span class="model-name">${m.name}</span>
          <span class="model-meta">
            ${moderationBadge}${priceBadge}
            ${ctx ? `<span>${ctx} ctx</span>` : ""}
            ${price && price !== "Free" ? `<span>${price}/M</span>` : ""}
          </span>
        </div>
      </div>`;
    })
    .join("");

  $modelList.querySelectorAll(".model-item").forEach((el) => {
    el.addEventListener("click", () => toggleModel(el.dataset.id));
  });
}

function toggleModel(modelId) {
  const idx = state.selectedModels.indexOf(modelId);
  if (idx >= 0) {
    state.selectedModels.splice(idx, 1);
    delete state.conversations[modelId];
  } else {
    state.selectedModels.push(modelId);
    state.conversations[modelId] = [];
  }
  renderModelList();
  renderSelectedTags();
  renderChatColumns();
}

function renderSelectedTags() {
  $selectedTags.innerHTML = state.selectedModels
    .map((id) => {
      const shortName = id.split("/").pop();
      return `<span class="model-tag">${shortName}<span class="remove" data-id="${id}">×</span></span>`;
    })
    .join("");

  $selectedTags.querySelectorAll(".remove").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleModel(el.dataset.id);
    });
  });
}

// ── Chat Columns ────────────────────────────────────────
function renderChatColumns() {
  if (state.selectedModels.length === 0) {
    $chatColumns.innerHTML = '<div class="placeholder-msg">← 选择模型后开始对话</div>';
    return;
  }

  $chatColumns.innerHTML = state.selectedModels
    .map((modelId) => {
      const model = state.models.find((m) => m.id === modelId);
      const shortName = modelId.split("/").pop();
      const modBadge = model?.is_moderated
        ? ' <span class="badge badge-moderated">审核</span>'
        : ' <span class="badge badge-unmoderated">无审核</span>';
      return `<div class="chat-column" data-model="${modelId}">
        <div class="chat-column-header">${shortName}${modBadge}</div>
        <div class="chat-messages" id="msgs-${CSS.escape(modelId)}"></div>
      </div>`;
    })
    .join("");

  for (const modelId of state.selectedModels) {
    renderMessages(modelId);
  }
}

function renderMessages(modelId) {
  const container = document.getElementById(`msgs-${CSS.escape(modelId)}`);
  if (!container) return;

  const msgs = state.conversations[modelId] || [];
  container.innerHTML = msgs
    .map((m) => {
      const html = m.role === "assistant" ? renderMarkdown(m.content) : escapeHtml(m.content);
      return `<div class="message ${m.role}"><div class="content">${html}</div>${
        m._streaming ? '<span class="streaming-cursor"></span>' : ""
      }</div>`;
    })
    .join("");

  container.scrollTop = container.scrollHeight;
}

function renderRpContent(text) {
  if (!text) return "";

  let html = escapeHtml(text);

  // Use placeholders to avoid regex matches interfering with each other.
  // Each match is replaced with \x00idx\x00, then restored after all patterns are processed.
  const tokens = [];
  function tokenize(regex, builder) {
    html = html.replace(regex, (...args) => {
      const idx = tokens.length;
      tokens.push(builder(...args));
      return `\x00${idx}\x00`;
    });
  }

  tokenize(/\*\*(.+?)\*\*/g, (_, c) => `<strong>${c}</strong>`);
  tokenize(/\*([^*]+?)\*/g, (_, c) => `<span class="rp-action">${c}</span>`);
  tokenize(/「([^」]+?)」/g, (_, c) => `<span class="rp-dialogue">「${c}」</span>`);
  tokenize(/\u201c([^\u201d]+?)\u201d/g, (_, c) => `<span class="rp-dialogue">\u201c${c}\u201d</span>`);
  tokenize(/&quot;([^&]+?)&quot;/g, (_, c) => `<span class="rp-dialogue">&quot;${c}&quot;</span>`);

  html = html.split("\n").join("<br>");
  html = html.replace(/\x00(\d+)\x00/g, (_, i) => tokens[parseInt(i)]);

  return html;
}

function renderMarkdown(text) {
  try {
    return renderRpContent(text);
  } catch {
    return escapeHtml(text);
  }
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// ── Send Message ────────────────────────────────────────
async function sendMessage() {
  const text = $userInput.value.trim();
  if (!text || state.selectedModels.length === 0 || state.streaming) return;

  state.streaming = true;
  $btnSend.disabled = true;
  $btnSend.textContent = "生成中...";
  $userInput.value = "";

  const systemPrompt = $systemPrompt.value.trim();
  const params = getParams();

  for (const modelId of state.selectedModels) {
    if (!state.conversations[modelId]) state.conversations[modelId] = [];
    state.conversations[modelId].push({ role: "user", content: text });
  }
  renderChatColumns();

  const streams = state.selectedModels.map((modelId) =>
    streamChat(modelId, systemPrompt, params)
  );

  await Promise.all(streams);

  state.streaming = false;
  $btnSend.disabled = false;
  $btnSend.textContent = "发送";
}

function getParams() {
  return {
    temperature: parseFloat($temperature.value),
    top_p: parseFloat($topP.value),
    max_tokens: parseInt($maxTokens.value) || 2048,
    frequency_penalty: parseFloat($freqPenalty.value),
    presence_penalty: parseFloat($presPenalty.value),
  };
}

function getActiveChar() {
  if (!state.activeCharId) return null;
  return state.characters.find((c) => c.id === state.activeCharId) || null;
}

function replacePlaceholders(text, charName) {
  if (!text) return text;
  return text.replace(/\{\{char\}\}/gi, charName || "角色").replace(/\{\{user\}\}/gi, "用户");
}

function buildCharSystemPrompt(char) {
  if (!char) return "";
  const parts = [];
  if (char.system_prompt) parts.push(char.system_prompt);
  if (char.description) parts.push(`[Character Description]\n${char.description}`);
  if (char.personality) parts.push(`[Personality]\n${char.personality}`);
  if (char.scenario) parts.push(`[Scenario]\n${char.scenario}`);
  const combined = parts.join("\n\n");
  return replacePlaceholders(combined, char.name);
}

function parseMesExample(mesExample, charName) {
  if (!mesExample || !mesExample.trim()) return [];
  const text = replacePlaceholders(mesExample, charName);
  const blocks = text.split(/<START>/i).filter((b) => b.trim());
  const examples = [];

  for (const block of blocks) {
    const lines = block.trim().split("\n");
    let currentRole = null;
    let currentContent = "";

    for (const line of lines) {
      const userMatch = line.match(/^(?:\{\{user\}\}|用户|User)\s*[:：]\s*(.*)/i);
      const charMatch = line.match(
        new RegExp(`^(?:\\{\\{char\\}\\}|${charName ? charName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') : '角色'}|Assistant)\\s*[:：]\\s*(.*)`, "i")
      );

      if (userMatch) {
        if (currentRole) examples.push({ role: currentRole, content: currentContent.trim() });
        currentRole = "user";
        currentContent = userMatch[1];
      } else if (charMatch) {
        if (currentRole) examples.push({ role: currentRole, content: currentContent.trim() });
        currentRole = "assistant";
        currentContent = charMatch[1];
      } else if (currentRole) {
        currentContent += "\n" + line;
      }
    }
    if (currentRole && currentContent.trim()) {
      examples.push({ role: currentRole, content: currentContent.trim() });
    }
  }
  return examples;
}

function gatherWorldbookContext(conversationMessages) {
  const allText = conversationMessages.map((m) => m.content).join("\n").toLowerCase();
  const matched = [];

  for (const book of state.worldbooks) {
    if (!book.enabled) continue;
    for (const entry of book.entries) {
      if (!entry.enabled) continue;
      const hit = (entry.keywords || []).some((kw) => allText.includes(kw.toLowerCase()));
      if (hit) {
        matched.push(entry.content);
      }
    }
  }

  if (matched.length === 0) return "";
  return "[World Book Context]\n" + matched.join("\n\n");
}

const RP_FORMAT_INSTRUCTION = `[Response Format]
Use the following formatting in your responses:
- Wrap actions, descriptions, thoughts, and narration in asterisks: *she looked away*
- Write dialogue in quotation marks: 「like this」 or "like this"
- Do not use any other formatting or markdown.`;

async function streamChat(modelId, systemPrompt, params) {
  const messages = [];
  const convMsgs = state.conversations[modelId];
  const activeChar = getActiveChar();
  const rpFormatEnabled = document.getElementById("rp-format-hint")?.checked;

  const charSystem = buildCharSystemPrompt(activeChar);
  const rpHint = rpFormatEnabled ? RP_FORMAT_INSTRUCTION : "";
  const wbContext = gatherWorldbookContext(convMsgs);
  const fullSystem = [charSystem, systemPrompt, rpHint, wbContext].filter(Boolean).join("\n\n");

  if (fullSystem) {
    messages.push({ role: "system", content: fullSystem });
  }

  // Inject mes_example as few-shot examples
  if (activeChar?.mes_example) {
    const examples = parseMesExample(activeChar.mes_example, activeChar.name);
    for (const ex of examples) {
      messages.push({ role: ex.role, content: ex.content });
    }
  }

  for (const m of convMsgs) {
    messages.push({ role: m.role, content: m.content });
  }

  state.conversations[modelId].push({ role: "assistant", content: "", _streaming: true });
  const msgIdx = state.conversations[modelId].length - 1;
  renderMessages(modelId);

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: modelId, messages, params }),
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const payload = line.slice(6).trim();
        if (payload === "[DONE]") continue;

        try {
          const parsed = JSON.parse(payload);
          const delta = parsed.choices?.[0]?.delta?.content;
          if (delta) {
            state.conversations[modelId][msgIdx].content += delta;
            renderMessages(modelId);
          }
        } catch {}
      }
    }
  } catch (e) {
    state.conversations[modelId][msgIdx].content += `\n\n[Error: ${e.message}]`;
  }

  delete state.conversations[modelId][msgIdx]._streaming;
  renderMessages(modelId);
}

// ── Clear ───────────────────────────────────────────────
function clearConversations() {
  for (const modelId of state.selectedModels) {
    state.conversations[modelId] = [];
  }
  renderChatColumns();
}

// ── Presets ─────────────────────────────────────────────
async function loadPresets() {
  try {
    const resp = await fetch("/api/presets");
    const data = await resp.json();
    state.presets = data.presets;
    renderPresetSelect();
  } catch (e) {
    console.error("Failed to load presets:", e);
  }
}

function renderPresetSelect() {
  $presetSelect.innerHTML =
    '<option value="">-- 选择预设 --</option>' +
    state.presets
      .map((p) => `<option value="${p.id}">${escapeHtml(p.name)}</option>`)
      .join("");
}

function applyPreset() {
  const id = parseInt($presetSelect.value);
  if (!id) return;
  const preset = state.presets.find((p) => p.id === id);
  if (!preset) return;

  $systemPrompt.value = preset.systemPrompt || "";
  if (preset.params) {
    if (preset.params.temperature != null) {
      $temperature.value = preset.params.temperature;
      document.getElementById("temp-val").textContent = parseFloat(preset.params.temperature).toFixed(2);
    }
    if (preset.params.top_p != null) {
      $topP.value = preset.params.top_p;
      document.getElementById("topp-val").textContent = parseFloat(preset.params.top_p).toFixed(2);
    }
    if (preset.params.max_tokens != null) $maxTokens.value = preset.params.max_tokens;
    if (preset.params.frequency_penalty != null) {
      $freqPenalty.value = preset.params.frequency_penalty;
      document.getElementById("freq-val").textContent = parseFloat(preset.params.frequency_penalty).toFixed(2);
    }
    if (preset.params.presence_penalty != null) {
      $presPenalty.value = preset.params.presence_penalty;
      document.getElementById("pres-val").textContent = parseFloat(preset.params.presence_penalty).toFixed(2);
    }
  }

  if (preset.models && preset.models.length > 0) {
    state.selectedModels = [...preset.models];
    for (const mid of state.selectedModels) {
      if (!state.conversations[mid]) state.conversations[mid] = [];
    }
    renderModelList();
    renderSelectedTags();
    renderChatColumns();
  }
}

function savePresetDialog() {
  const overlay = document.createElement("div");
  overlay.className = "dialog-overlay";
  overlay.innerHTML = `
    <div class="dialog">
      <h3>保存预设</h3>
      <input type="text" id="preset-name-input" placeholder="预设名称..." autofocus>
      <div class="dialog-actions">
        <button id="dialog-cancel">取消</button>
        <button id="dialog-save" class="primary">保存</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  const $input = document.getElementById("preset-name-input");
  const save = async () => {
    const name = $input.value.trim();
    if (!name) return;
    overlay.remove();

    const preset = {
      name,
      systemPrompt: $systemPrompt.value,
      models: [...state.selectedModels],
      params: getParams(),
    };

    const resp = await fetch("/api/presets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(preset),
    });
    const data = await resp.json();
    state.presets.push(data.preset);
    renderPresetSelect();
    $presetSelect.value = data.preset.id;
  };

  document.getElementById("dialog-cancel").addEventListener("click", () => overlay.remove());
  document.getElementById("dialog-save").addEventListener("click", save);
  $input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") save();
    if (e.key === "Escape") overlay.remove();
  });
}

async function deletePreset() {
  const id = parseInt($presetSelect.value);
  if (!id) return;
  if (!confirm("确定删除这个预设？")) return;

  await fetch(`/api/presets/${id}`, { method: "DELETE" });
  state.presets = state.presets.filter((p) => p.id !== id);
  renderPresetSelect();
}

// ── Worldbooks ─────────────────────────────────────────
async function loadWorldbooks() {
  try {
    const resp = await fetch("/api/worldbooks");
    const data = await resp.json();
    state.worldbooks = data.worldbooks;
    renderWbList();
  } catch (e) {
    console.error("Failed to load worldbooks:", e);
  }
}

function renderWbList() {
  if (state.worldbooks.length === 0) {
    $wbList.innerHTML = '<div style="font-size:11px;color:var(--text-muted);padding:4px 0;">暂无世界书，点击上方新建或导入</div>';
    return;
  }

  $wbList.innerHTML = state.worldbooks
    .map((wb) => {
      const disabledClass = wb.enabled ? "" : "disabled";
      const entryCount = (wb.entries || []).length;
      const enabledCount = (wb.entries || []).filter((e) => e.enabled).length;
      return `<div class="wb-card ${disabledClass}" data-id="${wb.id}">
        <div class="wb-card-header">
          <input type="checkbox" ${wb.enabled ? "checked" : ""} data-action="toggle" data-id="${wb.id}">
          <span class="wb-name" title="${escapeHtml(wb.name)}">${escapeHtml(wb.name)}</span>
          <span class="wb-count">${enabledCount}/${entryCount}</span>
          <div class="wb-card-actions">
            <button data-action="edit" data-id="${wb.id}" title="编辑">✏️</button>
            <button data-action="delete" data-id="${wb.id}" class="danger" title="删除">🗑️</button>
          </div>
        </div>
      </div>`;
    })
    .join("");

  $wbList.querySelectorAll("[data-action=toggle]").forEach((el) => {
    el.addEventListener("change", () => toggleWorldbook(parseInt(el.dataset.id)));
  });
  $wbList.querySelectorAll("[data-action=edit]").forEach((el) => {
    el.addEventListener("click", () => {
      const wb = state.worldbooks.find((b) => b.id === parseInt(el.dataset.id));
      if (wb) openWbEditor(wb);
    });
  });
  $wbList.querySelectorAll("[data-action=delete]").forEach((el) => {
    el.addEventListener("click", () => deleteWorldbook(parseInt(el.dataset.id)));
  });
}

async function toggleWorldbook(id) {
  const wb = state.worldbooks.find((b) => b.id === id);
  if (!wb) return;
  wb.enabled = !wb.enabled;
  await fetch(`/api/worldbooks/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(wb),
  });
  renderWbList();
}

async function deleteWorldbook(id) {
  if (!confirm("确定删除这个世界书？")) return;
  await fetch(`/api/worldbooks/${id}`, { method: "DELETE" });
  state.worldbooks = state.worldbooks.filter((b) => b.id !== id);
  renderWbList();
}

async function importWorldbook() {
  const file = $wbFileInput.files[0];
  if (!file) return;
  $wbFileInput.value = "";

  const formData = new FormData();
  formData.append("file", file);

  try {
    const resp = await fetch("/api/worldbooks/import", { method: "POST", body: formData });
    const data = await resp.json();
    if (data.error) {
      alert("导入失败: " + data.error);
      return;
    }
    state.worldbooks.push(data.worldbook);
    renderWbList();
  } catch (e) {
    alert("导入失败: " + e.message);
  }
}

function openWbEditor(existingBook) {
  const isNew = !existingBook;
  const book = existingBook
    ? JSON.parse(JSON.stringify(existingBook))
    : { name: "", enabled: true, entries: [] };

  const overlay = document.createElement("div");
  overlay.className = "dialog-overlay";
  overlay.innerHTML = `
    <div class="dialog wb-editor">
      <h3>${isNew ? "新建世界书" : "编辑世界书"}</h3>
      <input type="text" class="wb-editor-name" id="wb-edit-name"
        placeholder="世界书名称..." value="${escapeHtml(book.name)}">
      <div class="wb-entries" id="wb-edit-entries"></div>
      <div class="wb-add-entry" id="wb-add-entry">+ 添加条目</div>
      <div class="dialog-actions" style="margin-top:12px;">
        <button id="wb-edit-cancel">取消</button>
        <button id="wb-edit-save" class="primary">保存</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  const $entries = document.getElementById("wb-edit-entries");

  function renderEntries() {
    $entries.innerHTML = book.entries
      .map((entry, idx) => `
        <div class="wb-entry" data-idx="${idx}">
          <div class="wb-entry-header">
            <label>
              <input type="checkbox" ${entry.enabled ? "checked" : ""} data-field="enabled" data-idx="${idx}">
              <span>条目 #${idx + 1}</span>
            </label>
            <button data-remove="${idx}" title="删除条目">×</button>
          </div>
          <input type="text" placeholder="关键词（逗号分隔）"
            value="${escapeHtml((entry.keywords || []).join(", "))}"
            data-field="keywords" data-idx="${idx}">
          <textarea rows="3" placeholder="条目内容..."
            data-field="content" data-idx="${idx}">${escapeHtml(entry.content || "")}</textarea>
        </div>`)
      .join("");

    $entries.querySelectorAll("[data-remove]").forEach((btn) => {
      btn.addEventListener("click", () => {
        book.entries.splice(parseInt(btn.dataset.remove), 1);
        renderEntries();
      });
    });
  }

  renderEntries();

  document.getElementById("wb-add-entry").addEventListener("click", () => {
    collectEntryEdits();
    book.entries.push({ keywords: [], content: "", enabled: true });
    renderEntries();
    const last = $entries.querySelector(".wb-entry:last-child input[type=text]");
    if (last) last.focus();
  });

  function collectEntryEdits() {
    $entries.querySelectorAll(".wb-entry").forEach((el) => {
      const idx = parseInt(el.dataset.idx);
      const kwInput = el.querySelector("[data-field=keywords]");
      const contentInput = el.querySelector("[data-field=content]");
      const enabledInput = el.querySelector("[data-field=enabled]");
      if (kwInput) {
        book.entries[idx].keywords = kwInput.value
          .split(",")
          .map((k) => k.trim())
          .filter(Boolean);
      }
      if (contentInput) book.entries[idx].content = contentInput.value;
      if (enabledInput) book.entries[idx].enabled = enabledInput.checked;
    });
  }

  document.getElementById("wb-edit-cancel").addEventListener("click", () => overlay.remove());
  document.getElementById("wb-edit-save").addEventListener("click", async () => {
    collectEntryEdits();
    book.name = document.getElementById("wb-edit-name").value.trim() || "未命名世界书";
    overlay.remove();

    if (isNew) {
      const resp = await fetch("/api/worldbooks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(book),
      });
      const data = await resp.json();
      state.worldbooks.push(data.worldbook);
    } else {
      await fetch(`/api/worldbooks/${book.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(book),
      });
      const idx = state.worldbooks.findIndex((b) => b.id === book.id);
      if (idx >= 0) state.worldbooks[idx] = book;
    }
    renderWbList();
  });
}

// ── Characters ─────────────────────────────────────────
async function loadCharacters() {
  try {
    const resp = await fetch("/api/characters");
    const data = await resp.json();
    state.characters = data.characters;
    renderCharSelect();
  } catch (e) {
    console.error("Failed to load characters:", e);
  }
}

function renderCharSelect() {
  $charSelect.innerHTML =
    '<option value="">-- 未选择角色 --</option>' +
    state.characters
      .map((c) => `<option value="${c.id}">${escapeHtml(c.name || "未命名")}</option>`)
      .join("");

  if (state.activeCharId) {
    $charSelect.value = state.activeCharId;
  }
}

function onCharSelect() {
  const id = parseInt($charSelect.value);
  state.activeCharId = id || null;
  renderCharInfo();
}

function renderCharInfo() {
  const char = getActiveChar();
  if (!char) {
    $charInfo.hidden = true;
    return;
  }

  $charInfo.hidden = false;
  const tags = char.tags || [];
  $charTags.innerHTML = tags.length > 0
    ? tags.map((t) => `<span class="char-tag">${escapeHtml(t)}</span>`).join("")
    : '<span style="color:var(--text-muted);font-size:10px;">无标签</span>';

  $btnSendGreeting.style.display = char.first_mes ? "" : "none";
}

function sendGreeting() {
  const char = getActiveChar();
  if (!char?.first_mes || state.selectedModels.length === 0) return;

  const greeting = replacePlaceholders(char.first_mes, char.name);
  for (const modelId of state.selectedModels) {
    if (!state.conversations[modelId]) state.conversations[modelId] = [];
    state.conversations[modelId].push({ role: "assistant", content: greeting });
  }
  renderChatColumns();
}

async function importCharacter() {
  const file = $charFileInput.files[0];
  if (!file) return;
  $charFileInput.value = "";

  const formData = new FormData();
  formData.append("file", file);

  try {
    const resp = await fetch("/api/characters/import", { method: "POST", body: formData });
    const data = await resp.json();
    if (data.error) {
      alert("导入失败: " + data.error);
      return;
    }
    state.characters.push(data.character);
    renderCharSelect();
    state.activeCharId = data.character.id;
    $charSelect.value = data.character.id;
    renderCharInfo();
  } catch (e) {
    alert("导入失败: " + e.message);
  }
}

async function deleteCharacter() {
  const char = getActiveChar();
  if (!char) return;
  if (!confirm(`确定删除角色「${char.name}」？`)) return;

  await fetch(`/api/characters/${char.id}`, { method: "DELETE" });
  state.characters = state.characters.filter((c) => c.id !== char.id);
  state.activeCharId = null;
  renderCharSelect();
  renderCharInfo();
}

function openCharGenerator() {
  const overlay = document.createElement("div");
  overlay.className = "dialog-overlay";
  overlay.innerHTML = `
    <div class="dialog" style="max-width:560px">
      <h3>✨ AI 生成角色卡</h3>
      <div class="gen-tabs" style="display:flex;gap:0;margin-top:8px;border-bottom:1px solid var(--border)">
        <button class="gen-tab active" data-tab="text">📝 关键词生成</button>
        <button class="gen-tab" data-tab="image">🖼️ 图片生成</button>
      </div>

      <div id="gen-panel-text" class="gen-panel">
        <div class="char-field" style="margin-top:10px">
          <label>关键词 / 角色概念描述</label>
          <textarea id="gen-keywords" rows="6" placeholder="输入关键词或角色概念，例如：&#10;&#10;冷酷女杀手，双面人格，白天是温柔的花店老板，晚上是地下组织的王牌&#10;&#10;或者直接粘贴一段详细的角色描述"></textarea>
          <div class="hint">描述越详细，生成的角色卡越精准。支持中文/英文。</div>
        </div>
      </div>

      <div id="gen-panel-image" class="gen-panel" style="display:none">
        <div class="char-field" style="margin-top:10px">
          <label>角色图片</label>
          <div id="gen-img-zone" style="border:2px dashed var(--border);border-radius:8px;padding:20px;text-align:center;cursor:pointer;transition:all 0.2s;position:relative;min-height:120px;display:flex;align-items:center;justify-content:center">
            <input type="file" id="gen-img-input" accept="image/*" hidden>
            <div id="gen-img-hint">拖拽图片到此处，或点击选择<br><span style="font-size:11px;color:var(--text-muted)">支持 JPG / PNG / WebP</span></div>
            <img id="gen-img-preview" style="display:none;max-height:200px;max-width:100%;border-radius:6px">
          </div>
        </div>
        <div class="char-field">
          <label>补充设定（可选）</label>
          <textarea id="gen-extra" rows="3" placeholder="可选：补充角色身份、性格、背景等设定，AI 会结合图片和文字一起生成"></textarea>
        </div>
      </div>

      <div class="char-field" style="margin-top:8px">
        <label>生成模型</label>
        <select id="gen-model">
          <option value="">加载中...</option>
        </select>
      </div>
      <div id="gen-status" style="display:none;margin-top:8px;padding:10px;border-radius:6px;background:var(--bg-card);font-size:12px;color:var(--text-muted);white-space:pre-wrap"></div>
      <div class="dialog-actions" style="margin-top:12px">
        <button id="gen-cancel">取消</button>
        <button id="gen-submit" class="primary">开始生成</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  const $model = document.getElementById("gen-model");
  const $status = document.getElementById("gen-status");
  const $submit = document.getElementById("gen-submit");
  const $imgZone = document.getElementById("gen-img-zone");
  const $imgInput = document.getElementById("gen-img-input");
  const $imgPreview = document.getElementById("gen-img-preview");
  const $imgHint = document.getElementById("gen-img-hint");

  let currentTab = "text";
  let selectedFile = null;

  // ── Tab switching ──
  overlay.querySelectorAll(".gen-tab").forEach(btn => {
    btn.addEventListener("click", () => {
      overlay.querySelectorAll(".gen-tab").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      currentTab = btn.dataset.tab;
      document.getElementById("gen-panel-text").style.display = currentTab === "text" ? "" : "none";
      document.getElementById("gen-panel-image").style.display = currentTab === "image" ? "" : "none";
      populateModels();
    });
  });

  // ── Image upload ──
  $imgZone.addEventListener("click", () => $imgInput.click());
  $imgZone.addEventListener("dragover", e => { e.preventDefault(); $imgZone.style.borderColor = "var(--primary)"; });
  $imgZone.addEventListener("dragleave", () => { $imgZone.style.borderColor = ""; });
  $imgZone.addEventListener("drop", e => {
    e.preventDefault();
    $imgZone.style.borderColor = "";
    if (e.dataTransfer.files[0]) setImage(e.dataTransfer.files[0]);
  });
  $imgInput.addEventListener("change", () => { if ($imgInput.files[0]) setImage($imgInput.files[0]); });

  function setImage(file) {
    selectedFile = file;
    const url = URL.createObjectURL(file);
    $imgPreview.src = url;
    $imgPreview.style.display = "";
    $imgHint.style.display = "none";
  }

  // ── Model population ──
  const GEN_PREFERRED = ["grok-4", "grok-3", "claude", "gemini", "deepseek"];
  function populateModels() {
    const isVision = currentTab === "image";
    const genModels = state.models
      .filter(m => {
        if (m.is_moderated) return false;
        if (m.max_completion_tokens !== null && m.max_completion_tokens < 8000) return false;
        if (isVision) {
          const modality = m.architecture?.modality || "";
          return modality.includes("image");
        }
        return true;
      })
      .sort((a, b) => {
        const aP = GEN_PREFERRED.findIndex(p => a.id.toLowerCase().includes(p));
        const bP = GEN_PREFERRED.findIndex(p => b.id.toLowerCase().includes(p));
        return (aP >= 0 ? aP : 999) - (bP >= 0 ? bP : 999) || a.name.localeCompare(b.name);
      });
    $model.innerHTML = genModels.map(m =>
      `<option value="${escapeHtml(m.id)}">${escapeHtml(m.name)}</option>`
    ).join('');
  }
  populateModels();

  document.getElementById("gen-cancel").addEventListener("click", () => overlay.remove());

  $submit.addEventListener("click", async () => {
    if (currentTab === "text") {
      const keywords = document.getElementById("gen-keywords").value.trim();
      if (!keywords) { alert("请输入关键词"); return; }
      await doGenerate({ keywords, model: $model.value });
    } else {
      if (!selectedFile) { alert("请上传一张图片"); return; }
      const form = new FormData();
      form.append("image", selectedFile);
      form.append("extra", document.getElementById("gen-extra").value);
      form.append("model", $model.value);
      await doGenerate(form);
    }
  });

  async function doGenerate(payload) {
    $submit.disabled = true;
    $submit.textContent = "生成中...";
    $status.style.display = "block";
    $status.style.color = "";
    $status.textContent = currentTab === "image"
      ? "正在分析图片并生成完整角色卡（含 character_book），大约需要 30-120 秒..."
      : "正在调用 AI 生成完整角色卡（含 character_book），大约需要 30-90 秒...";

    try {
      const isForm = payload instanceof FormData;
      const url = isForm ? "/api/characters/generate-from-image" : "/api/characters/generate";
      const opts = isForm
        ? { method: "POST", body: payload }
        : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) };
      const resp = await fetch(url, opts);
      const data = await resp.json();

      if (data.error) {
        $status.style.color = "var(--accent)";
        $status.textContent = "生成失败: " + data.error;
        if (data.raw) $status.textContent += "\n\n原始输出片段:\n" + data.raw;
        $submit.disabled = false;
        $submit.textContent = "重试";
        return;
      }

      state.characters.push(data.character);
      state.activeCharId = data.character.id;
      renderCharSelect();
      $charSelect.value = data.character.id;
      renderCharInfo();
      overlay.remove();

      const bookEntries = data.character.character_book?.entries?.length || 0;
      alert(`角色「${data.character.name}」生成成功！\n包含 ${bookEntries} 条 Character Book 条目。`);
    } catch (e) {
      $status.style.color = "var(--accent)";
      $status.textContent = "请求失败: " + e.message;
      $submit.disabled = false;
      $submit.textContent = "重试";
    }
  }
}

function openCharEditor(existingChar) {
  const isNew = !existingChar;
  const char = existingChar
    ? JSON.parse(JSON.stringify(existingChar))
    : { name: "", description: "", personality: "", scenario: "", first_mes: "", mes_example: "", system_prompt: "", creator_notes: "", tags: [] };

  const overlay = document.createElement("div");
  overlay.className = "dialog-overlay";
  overlay.innerHTML = `
    <div class="dialog char-editor">
      <h3>${isNew ? "新建角色卡" : "编辑角色卡"}</h3>
      <div class="char-editor-fields">
        <div class="char-field">
          <label>角色名 Name</label>
          <input type="text" id="ce-name" value="${escapeHtml(char.name)}" placeholder="角色的名字">
        </div>
        <div class="char-field">
          <label>描述 Description</label>
          <textarea id="ce-description" rows="5" placeholder="外貌、身份、背景故事...">${escapeHtml(char.description)}</textarea>
          <div class="hint">核心设定，每次对话都会包含</div>
        </div>
        <div class="char-field">
          <label>性格 Personality</label>
          <textarea id="ce-personality" rows="2" placeholder="性格特征摘要...">${escapeHtml(char.personality)}</textarea>
        </div>
        <div class="char-field">
          <label>场景 Scenario</label>
          <textarea id="ce-scenario" rows="2" placeholder="角色和用户的关系、所处场景...">${escapeHtml(char.scenario)}</textarea>
        </div>
        <div class="char-field">
          <label>开场白 First Message</label>
          <textarea id="ce-first-mes" rows="4" placeholder="角色的第一句话，用于开启对话...">${escapeHtml(char.first_mes)}</textarea>
          <div class="hint">加载角色后可一键发送到对话中</div>
        </div>
        <div class="char-field">
          <label>示例对话 Example Messages</label>
          <textarea id="ce-mes-example" rows="6" placeholder="<START>
{{user}}: 你好
{{char}}: *微微点头* 你好。">${escapeHtml(char.mes_example)}</textarea>
          <div class="hint">用 &lt;START&gt; 分隔多组示例，{{user}} 代表用户，{{char}} 代表角色</div>
        </div>
        <div class="char-field">
          <label>角色专用 System Prompt（可选）</label>
          <textarea id="ce-system-prompt" rows="3" placeholder="额外的 system 指令，如文风要求、输出格式...">${escapeHtml(char.system_prompt)}</textarea>
          <div class="hint">会和侧边栏的 System Prompt 叠加使用</div>
        </div>
        <div class="char-field">
          <label>标签 Tags</label>
          <input type="text" id="ce-tags" value="${escapeHtml((char.tags || []).join(", "))}" placeholder="标签，逗号分隔（如：现代, 傲娇, 邻居）">
        </div>
        <div class="char-field">
          <label>作者备注 Creator Notes</label>
          <textarea id="ce-creator-notes" rows="2" placeholder="使用建议、推荐参数等...">${escapeHtml(char.creator_notes)}</textarea>
        </div>
      </div>
      <div class="dialog-actions" style="margin-top:12px;">
        <button id="ce-cancel">取消</button>
        <button id="ce-save" class="primary">保存</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  document.getElementById("ce-cancel").addEventListener("click", () => overlay.remove());
  document.getElementById("ce-save").addEventListener("click", async () => {
    char.name = document.getElementById("ce-name").value.trim() || "未命名角色";
    char.description = document.getElementById("ce-description").value;
    char.personality = document.getElementById("ce-personality").value;
    char.scenario = document.getElementById("ce-scenario").value;
    char.first_mes = document.getElementById("ce-first-mes").value;
    char.mes_example = document.getElementById("ce-mes-example").value;
    char.system_prompt = document.getElementById("ce-system-prompt").value;
    char.creator_notes = document.getElementById("ce-creator-notes").value;
    char.tags = document.getElementById("ce-tags").value.split(",").map((t) => t.trim()).filter(Boolean);

    overlay.remove();

    if (isNew) {
      const resp = await fetch("/api/characters", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(char),
      });
      const data = await resp.json();
      state.characters.push(data.character);
      state.activeCharId = data.character.id;
    } else {
      await fetch(`/api/characters/${char.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(char),
      });
      const idx = state.characters.findIndex((c) => c.id === char.id);
      if (idx >= 0) state.characters[idx] = char;
    }
    renderCharSelect();
    $charSelect.value = state.activeCharId;
    renderCharInfo();
  });
}

// ── Boot ────────────────────────────────────────────────
init();
