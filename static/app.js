/**
 * ═══════════════════════════════════════════════════════════════════════════════
 * 前端主应用 — 多模型 RP（角色扮演）聊天平台
 * ═══════════════════════════════════════════════════════════════════════════════
 *
 * 本文件是整个前端的核心逻辑，负责：
 *   1. 管理全局状态（模型列表、对话记录、角色卡、世界书、预设等）
 *   2. 与后端 REST API 及 SSE 流式接口通信
 *   3. 渲染多列聊天界面，支持同时对比多个 LLM 模型的输出
 *   4. 角色卡 CRUD、AI 生成、AI 改造、导入导出
 *   5. 世界书（Worldbook）管理与关键词触发上下文注入
 *   6. RP 格式化（动作 / 对话 / 加粗）渲染
 *   7. 文生图 Prompt 生成、TTS 语音朗读
 *   8. 对话历史持久化（保存 / 加载 / 删除）
 *   9. 参数预设管理
 *
 * 使用技术：原生 JavaScript（无框架），通过 fetch + SSE 与 Python 后端交互。
 */

// ── 全局状态管理 ─────────────────────────────────────────
// 所有需要跨函数共享的运行时数据都集中存放于 state 对象中
const state = {
  models: [],            // 从后端获取的全部可用模型列表
  selectedModels: [],    // 用户当前勾选的模型 ID 数组
  conversations: {},     // modelId -> [{role, content}]，每个模型独立维护对话记录
  presets: [],           // 用户保存的参数预设
  worldbooks: [],        // 世界书列表（含条目与关键词）
  characters: [],        // 角色卡列表
  kolCharacters: [],     // KOL 角色卡列表（从 kol-characters.json 加载）
  kolVersionMap: {},     // KOL 角色选中的版本号: { key -> version number }
  charSource: "local",   // 角色卡来源: "local" | "kol"
  activeCharId: null,    // 当前激活的角色卡 ID（本地为数字 id，KOL 为 key 字符串）
  streaming: false,      // 是否正在进行流式响应
  userName: localStorage.getItem("userName") || "用户",
};

// ── DOM 元素引用 ─────────────────────────────────────────
// 缓存页面中频繁访问的 DOM 元素，避免重复查询
const $userName = document.getElementById("user-name");
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
const $btnSaveChat = document.getElementById("btn-save-chat");
const $btnLoadChat = document.getElementById("btn-load-chat");

// 模型参数滑块
const $temperature = document.getElementById("param-temperature");
const $topP = document.getElementById("param-top-p");
const $maxTokens = document.getElementById("param-max-tokens");
const $freqPenalty = document.getElementById("param-freq-penalty");
const $presPenalty = document.getElementById("param-pres-penalty");

// ── 初始化与事件绑定 ─────────────────────────────────────
/**
 * 应用入口：初始化滑块标签同步，并行加载所有远程数据，最后绑定 UI 事件。
 */
async function init() {
  setupSliderLabels();
  await Promise.all([loadModels(), loadPresets(), loadWorldbooks(), loadCharacters(), loadKolCharacters(), loadTtsVoices()]);
  setupEvents();
}

/**
 * 为参数滑块绑定实时数值显示标签。
 * 当用户拖动滑块时，旁边的文本标签会同步更新为当前值。
 */
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

// 更多 DOM 引用：过滤器、世界书、角色卡相关元素
const $filterUnmoderated = document.getElementById("filter-unmoderated");
const $modelCount = document.getElementById("model-count");
const $wbList = document.getElementById("wb-list");
const $btnCreateWb = document.getElementById("btn-create-wb");
const $btnImportWb = document.getElementById("btn-import-wb");
const $wbFileInput = document.getElementById("wb-file-input");

const $charSelect = document.getElementById("char-select");
const $btnNewChar = document.getElementById("btn-new-char");
const $btnGenChar = document.getElementById("btn-gen-char");
const $btnImportChar = document.getElementById("btn-import-char");
const $charFileInput = document.getElementById("char-file-input");
const $charInfo = document.getElementById("char-info");
const $charTags = document.getElementById("char-tags");
const $btnRemixChar = document.getElementById("btn-remix-char");
const $btnEditChar = document.getElementById("btn-edit-char");
const $btnDeleteChar = document.getElementById("btn-delete-char");
const $btnSendGreeting = document.getElementById("btn-send-greeting");
const $btnSrcLocal = document.getElementById("btn-src-local");
const $btnSrcKol = document.getElementById("btn-src-kol");

/**
 * 绑定所有 UI 事件：按钮点击、输入监听、快捷键等。
 * 在 init() 中数据加载完毕后调用。
 */
function setupEvents() {
  $modelSearch.addEventListener("input", renderModelList);
  $filterUnmoderated.addEventListener("change", renderModelList);
  $btnSend.addEventListener("click", sendMessage);
  $btnClear.addEventListener("click", clearConversations);
  $btnSaveChat?.addEventListener("click", saveChatDialog);
  $btnLoadChat?.addEventListener("click", openChatHistory);
  $btnSavePreset.addEventListener("click", savePresetDialog);
  $btnDeletePreset.addEventListener("click", deletePreset);
  $presetSelect.addEventListener("change", applyPreset);
  $btnCreateWb.addEventListener("click", () => openWbEditor(null));
  $btnImportWb.addEventListener("click", () => $wbFileInput.click());
  $wbFileInput.addEventListener("change", importWorldbook);

  $userName.value = state.userName;
  $userName.addEventListener("input", () => {
    state.userName = $userName.value.trim() || "用户";
    localStorage.setItem("userName", state.userName);
  });

  $charSelect.addEventListener("change", onCharSelect);
  $btnNewChar.addEventListener("click", () => openCharEditor(null));
  $btnGenChar?.addEventListener("click", openCharGenerator);
  $btnImportChar.addEventListener("click", () => $charFileInput.click());
  $charFileInput.addEventListener("change", importCharacter);
  $btnRemixChar?.addEventListener("click", () => {
    const c = getActiveChar();
    if (c) openCharRemixer(c);
  });
  $btnEditChar.addEventListener("click", () => {
    const c = getActiveChar();
    if (c) openCharEditor(c);
  });
  $btnDeleteChar.addEventListener("click", deleteCharacter);
  $btnSendGreeting.addEventListener("click", sendGreeting);

  $btnSrcLocal?.addEventListener("click", () => switchCharSource("local"));
  $btnSrcKol?.addEventListener("click", () => switchCharSource("kol"));

  document.getElementById("kol-version-select")?.addEventListener("change", onKolVersionChange);

  // Ctrl+Enter 快捷发送
  $userInput.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key === "Enter") {
      e.preventDefault();
      sendMessage();
    }
  });
}

// ── 模型列表（加载、渲染、切换、格式化辅助） ──────────────
/**
 * 从后端加载可用模型列表，存入 state.models 并渲染。
 */
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

/**
 * 将上下文长度数字格式化为可读字符串（如 128000 → "128K"）。
 */
function formatCtx(n) {
  if (!n) return "";
  if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
  if (n >= 1000) return (n / 1000).toFixed(0) + "K";
  return String(n);
}

/**
 * 将模型定价信息格式化为 "$/M" 格式的字符串。
 * 若 prompt 和 completion 均为 0 则返回 "Free"。
 */
function formatPrice(pricing) {
  if (!pricing) return "";
  const prompt = parseFloat(pricing.prompt || 0);
  const completion = parseFloat(pricing.completion || 0);
  if (prompt === 0 && completion === 0) return "Free";
  const perMIn = (prompt * 1_000_000).toFixed(2);
  const perMOut = (completion * 1_000_000).toFixed(2);
  return `$${perMIn}/$${perMOut}`;
}

/**
 * 根据搜索关键字和"仅显示无审核"过滤条件，渲染模型列表。
 * 最多显示前 150 个匹配结果以保证性能。
 */
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

/**
 * 切换模型选中状态：选中则加入 selectedModels 并初始化对话，取消则移除。
 */
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

/**
 * 渲染已选模型的标签条，每个标签可点击 × 取消选择。
 */
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

// ── 聊天列与消息渲染 ────────────────────────────────────
/**
 * 渲染聊天列区域：为每个已选模型创建一列，列内显示该模型的完整对话。
 * 未选择模型时显示占位提示。
 */
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

// ── 视觉密度检测（用于判断是否适合生成图片） ────────────
// 用于匹配含有视觉描写相关关键词的正则（服饰、身体、场景、动作等中英文词汇）
const _visualKeywords = /穿着|换上|脱下|裙|裤|衬衫|丝袜|内衣|外套|高跟|领口|纽扣|拉链|透明|蕾丝|肌肤|锁骨|肩膀|腰|胸|腿|唇|眼眸|发丝|红晕|脸颊|月光|夕阳|灯光|烛光|霓虹|倒映|夜色|窗边|浴室|卧室|沙发|靠近|贴紧|拥抱|吻|抬起|弯腰|转身|回眸|微笑|凝视|trembl|dress|skirt|silk|lace|lips|eyes|moonlight|candlelight/i;

/**
 * 判断一段文本是否具有足够的"视觉密度"——即包含多个动作描写块且含有视觉关键词。
 * 满足条件时，会在消息下方显示"这个画面很美，点击生成图片"按钮。
 *
 * 判断逻辑：
 *   1. 文本长度 >= 60 字符
 *   2. 至少包含 2 个 *动作描写* 块（用星号包裹的文本）
 *   3. 动作文本中命中视觉关键词，且总长度 > 40
 */
function isVisuallyDense(text) {
  if (!text || text.length < 60) return false;
  const actionBlocks = (text.match(/\*[^*]+\*/g) || []);
  if (actionBlocks.length < 2) return false;
  const actionText = actionBlocks.join(" ");
  const matches = actionText.match(_visualKeywords);
  return matches !== null && actionText.length > 40;
}

/**
 * 渲染指定模型的消息列表。
 * 每条消息会根据角色（user/assistant）应用不同样式。
 * 助手的已完成消息会附带操作按钮：生成图片 Prompt (🎨) 和朗读 (🔊)。
 * 如果消息内容视觉密度足够，还会显示"生成图片"提示按钮。
 */
function renderMessages(modelId) {
  const container = document.getElementById(`msgs-${CSS.escape(modelId)}`);
  if (!container) return;

  const msgs = state.conversations[modelId] || [];
  container.innerHTML = msgs
    .map((m, idx) => {
      const html = m.role === "assistant" ? renderMarkdown(m.content) : escapeHtml(m.content);
      const isFinishedAssistant = m.role === "assistant" && !m._streaming;
      const actions = isFinishedAssistant
        ? `<div class="msg-actions"><button class="msg-action-btn img-prompt-btn" data-model="${escapeHtml(modelId)}" data-idx="${idx}" title="生成文生图 Prompt">🎨</button><button class="msg-action-btn tts-btn" data-model="${escapeHtml(modelId)}" data-idx="${idx}" title="朗读">🔊</button></div>`
        : "";
      const visualHint = (isFinishedAssistant && isVisuallyDense(m.content))
        ? `<div class="visual-scene-hint"><button class="visual-hint-btn" data-model="${escapeHtml(modelId)}" data-idx="${idx}">✨ 这个画面很美，点击生成图片</button></div>`
        : "";
      return `<div class="message ${m.role}"><div class="content">${html}</div>${
        m._streaming ? '<span class="streaming-cursor"></span>' : ""
      }${actions}${visualHint}</div>`;
    })
    .join("");

  // 绑定"视觉场景提示"按钮事件
  container.querySelectorAll(".visual-hint-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const mid = btn.dataset.model;
      const i = parseInt(btn.dataset.idx);
      const text = state.conversations[mid]?.[i]?.content;
      const imgBtn = btn.closest(".message").querySelector(".img-prompt-btn");
      if (text && imgBtn) generateImagePrompt(text, imgBtn);
    });
  });

  // 绑定"生成图片 Prompt"按钮事件
  container.querySelectorAll(".img-prompt-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const mid = btn.dataset.model;
      const i = parseInt(btn.dataset.idx);
      const text = state.conversations[mid]?.[i]?.content;
      if (text) generateImagePrompt(text, btn);
    });
  });

  // 绑定"TTS 朗读"按钮事件
  container.querySelectorAll(".tts-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const mid = btn.dataset.model;
      const i = parseInt(btn.dataset.idx);
      const text = state.conversations[mid]?.[i]?.content;
      if (text) speakText(text, btn);
    });
  });

  container.scrollTop = container.scrollHeight;
}

// ── 文生图 Prompt 生成与弹窗 ────────────────────────────
/**
 * 将聊天消息文本发送给后端，由 AI 生成适合文生图的英文 Prompt，
 * 然后以弹窗形式展示给用户。
 */
async function generateImagePrompt(text, triggerBtn) {
  if (triggerBtn.disabled) return;
  triggerBtn.disabled = true;
  triggerBtn.textContent = "⏳";

  try {
    const resp = await fetch("/api/image-prompt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, model: state.selectedModels[0] || "x-ai/grok-4.1-fast" }),
    });
    const data = await resp.json();
    if (data.error) { alert("生成失败: " + data.error); return; }
    showImagePromptPopup(data.prompt, triggerBtn);
  } catch (e) {
    alert("请求失败: " + e.message);
  } finally {
    triggerBtn.disabled = false;
    triggerBtn.textContent = "🎨";
  }
}

/**
 * 在触发按钮所在消息内创建一个浮动弹窗，显示生成的图片 Prompt 并提供复制功能。
 */
function showImagePromptPopup(prompt, anchor) {
  document.querySelectorAll(".img-prompt-popup").forEach(el => el.remove());

  const popup = document.createElement("div");
  popup.className = "img-prompt-popup";
  popup.innerHTML = `
    <div class="img-prompt-header">
      <span>🎨 Image Prompt</span>
      <button class="img-prompt-close">×</button>
    </div>
    <div class="img-prompt-text">${escapeHtml(prompt)}</div>
    <div class="img-prompt-footer">
      <button class="img-prompt-copy">复制</button>
    </div>`;

  const msgEl = anchor.closest(".message");
  msgEl.appendChild(popup);

  popup.querySelector(".img-prompt-close").addEventListener("click", () => popup.remove());
  popup.querySelector(".img-prompt-copy").addEventListener("click", () => {
    navigator.clipboard.writeText(prompt).then(() => {
      const btn = popup.querySelector(".img-prompt-copy");
      btn.textContent = "已复制 ✓";
      setTimeout(() => btn.textContent = "复制", 1500);
    });
  });
}

// ── TTS 语音合成 ────────────────────────────────────────
let _ttsAudio = null;      // 当前正在播放的 Audio 实例
let _ttsPlayingBtn = null;  // 当前正在播放状态的按钮引用

/**
 * 从后端加载 TTS 可用语音列表，按语言分组填充到下拉框中。
 * 支持中文、英文、日文、韩文语音，并记住用户上次的选择。
 */
async function loadTtsVoices() {
  const $voice = document.getElementById("tts-voice");
  if (!$voice) return;
  try {
    const resp = await fetch("/api/tts/voices");
    const { voices } = await resp.json();
    const zhVoices = voices.filter((v) => v.locale.startsWith("zh-"));
    const enVoices = voices.filter((v) => v.locale.startsWith("en-"));
    const jaVoices = voices.filter((v) => v.locale.startsWith("ja-"));
    const koVoices = voices.filter((v) => v.locale.startsWith("ko-"));

    function voiceOpts(list, groupLabel) {
      if (!list.length) return "";
      return `<optgroup label="${groupLabel}">${list
        .map((v) => {
          const label = `${v.name} (${v.gender === "Female" ? "♀" : "♂"})`;
          return `<option value="${v.id}">${label}</option>`;
        })
        .join("")}</optgroup>`;
    }

    $voice.innerHTML =
      voiceOpts(zhVoices, "中文") +
      voiceOpts(enVoices, "English") +
      voiceOpts(jaVoices, "日本語") +
      voiceOpts(koVoices, "한국어");

    const saved = localStorage.getItem("ttsVoice");
    if (saved && voices.some((v) => v.id === saved)) $voice.value = saved;
    $voice.addEventListener("change", () => localStorage.setItem("ttsVoice", $voice.value));
  } catch (e) {
    console.error("Failed to load TTS voices:", e);
  }
}

function getTtsVoice() {
  return document.getElementById("tts-voice")?.value || "zh-CN-XiaoxiaoNeural";
}

function getTtsRate() {
  return document.getElementById("tts-rate")?.value || "+0%";
}

/**
 * 将文本发送到后端 TTS 接口进行语音合成并播放。
 * 如果当前按钮已在播放状态，则停止播放（切换行为）。
 */
async function speakText(text, btn) {
  if (_ttsAudio && _ttsPlayingBtn === btn) {
    stopTts();
    return;
  }
  stopTts();

  btn.disabled = true;
  btn.textContent = "⏳";

  try {
    const resp = await fetch("/api/tts/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, voice: getTtsVoice(), rate: getTtsRate() }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${resp.status}`);
    }

    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    _ttsAudio = new Audio(url);
    _ttsPlayingBtn = btn;

    btn.disabled = false;
    btn.textContent = "⏹️";
    btn.classList.add("tts-playing");

    _ttsAudio.addEventListener("ended", () => stopTts());
    _ttsAudio.addEventListener("error", () => stopTts());
    _ttsAudio.play();
  } catch (e) {
    alert("语音合成失败: " + e.message);
    btn.disabled = false;
    btn.textContent = "🔊";
  }
}

/**
 * 停止当前 TTS 播放，释放资源并恢复按钮状态。
 */
function stopTts() {
  if (_ttsAudio) {
    _ttsAudio.pause();
    if (_ttsAudio.src) URL.revokeObjectURL(_ttsAudio.src);
    _ttsAudio = null;
  }
  if (_ttsPlayingBtn) {
    _ttsPlayingBtn.textContent = "🔊";
    _ttsPlayingBtn.classList.remove("tts-playing");
    _ttsPlayingBtn = null;
  }
}

// ── RP 内容渲染（基于占位符的 tokenize 格式化） ─────────
/**
 * 将 RP（角色扮演）文本渲染为带格式的 HTML。
 *
 * 采用"占位符 tokenize"策略避免正则之间相互干扰：
 *   1. 依次对 **加粗**、*动作描写*、「中文引号对话」、"英文引号对话" 进行匹配
 *   2. 每次匹配到的内容先替换为 \x00索引\x00 占位符，将实际 HTML 存入 tokens 数组
 *   3. 所有模式处理完毕后，将换行符转为 <br>
 *   4. 最后将占位符替换回对应的 HTML 片段
 *
 * 这样可以确保嵌套或相邻的不同标记不会互相破坏。
 */
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

/**
 * 安全地渲染 Markdown（实际上是 RP 格式化），出错时回退为纯文本。
 */
function renderMarkdown(text) {
  try {
    return renderRpContent(text);
  } catch {
    return escapeHtml(text);
  }
}

/**
 * HTML 转义辅助函数，防止 XSS 注入。
 */
function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// ── 消息发送与流式响应（SSE） ────────────────────────────
/**
 * 发送用户消息：将输入追加到所有已选模型的对话记录中，
 * 然后并行对每个模型发起流式请求。
 */
async function sendMessage() {
  const text = $userInput.value.trim();
  if (!text || state.selectedModels.length === 0 || state.streaming) return;

  state.streaming = true;
  $btnSend.disabled = true;
  $btnSend.textContent = "生成中...";
  $userInput.value = "";

  const params = getParams();

  for (const modelId of state.selectedModels) {
    if (!state.conversations[modelId]) state.conversations[modelId] = [];
    state.conversations[modelId].push({ role: "user", content: text });
  }
  renderChatColumns();

  const streams = state.selectedModels.map((modelId) =>
    streamChat(modelId, params)
  );

  await Promise.all(streams);

  state.streaming = false;
  $btnSend.disabled = false;
  $btnSend.textContent = "发送";
}

/**
 * 收集当前 UI 上的模型参数（温度、top_p、最大令牌数、频率惩罚、存在惩罚）。
 */
function getParams() {
  return {
    temperature: parseFloat($temperature.value),
    top_p: parseFloat($topP.value),
    max_tokens: parseInt($maxTokens.value) || 2048,
    frequency_penalty: parseFloat($freqPenalty.value),
    presence_penalty: parseFloat($presPenalty.value),
  };
}

/**
 * 获取当前激活的角色卡对象，未选择时返回 null。
 */
function getActiveChar() {
  if (!state.activeCharId) return null;
  if (state.charSource === "kol") {
    const entry = state.kolCharacters.find((it) => it.key === state.activeCharId);
    return entry ? _kolToChar(entry) : null;
  }
  return state.characters.find((c) => c.id === state.activeCharId) || null;
}

/**
 * 替换文本中的 {{char}} 和 {{user}} 占位符为实际角色名和用户名。
 */
function replacePlaceholders(text, charName) {
  if (!text) return text;
  return text.replace(/\{\{char\}\}/gi, charName || "角色").replace(/\{\{user\}\}/gi, state.userName || "用户");
}


/**
 * 对单个模型发起流式聊天请求（SSE 协议）。
 *
 * 完整流程：
 *   1. 组装 system 消息：角色系统提示 + 用户自定义 system prompt + 格式指令 + 视觉暗示 + 世界书上下文
 *   2. 注入角色卡的示例对话（mes_example）作为 few-shot
 *   3. 追加实际对话消息
 *   4. 创建一条空的 assistant 占位消息（带 _streaming 标记）
 *   5. 通过 fetch 读取 SSE 流，逐块解析 delta.content 并实时更新界面
 *   6. 流结束后移除 _streaming 标记并最终渲染
 */
async function streamChat(modelId, params) {
  const convMsgs = state.conversations[modelId];
  const activeChar = getActiveChar();
  const rpMode = document.getElementById("rp-format-mode")?.value || "none";

  state.conversations[modelId].push({ role: "assistant", content: "", _streaming: true });
  const msgIdx = state.conversations[modelId].length - 1;
  renderMessages(modelId);

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: modelId,
        conversation: convMsgs.filter(m => !m._streaming).map(m => ({ role: m.role, content: m.content })),
        params,
        character: activeChar,
        rp_format_mode: rpMode,
        user_name: state.userName,
      }),
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    // 逐块读取 SSE 流数据
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop(); // 保留未完成的行

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

// ── 清除对话 ────────────────────────────────────────────
function clearConversations() {
  for (const modelId of state.selectedModels) {
    state.conversations[modelId] = [];
  }
  renderChatColumns();
}

// ── 对话历史管理（保存 / 加载 / 删除） ──────────────────
/**
 * 打开"保存对话"弹窗。
 * 弹窗允许用户输入对话名称，默认包含角色名和当前时间。
 * 保存时会将所有已选模型的对话内容、系统提示、参数等打包发送到后端。
 */
function saveChatDialog() {
  const hasMessages = state.selectedModels.some(
    (m) => (state.conversations[m] || []).length > 0
  );
  if (!hasMessages) {
    alert("当前没有对话内容可保存");
    return;
  }

  const activeChar = getActiveChar();
  const defaultName = [
    activeChar?.name || "",
    new Date().toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }),
  ]
    .filter(Boolean)
    .join(" - ");

  const overlay = document.createElement("div");
  overlay.className = "dialog-overlay";
  overlay.innerHTML = `
    <div class="dialog" style="max-width:420px">
      <h3>💾 保存对话</h3>
      <div class="char-field">
        <label>对话名称</label>
        <input type="text" id="chat-save-name" value="${escapeHtml(defaultName)}" style="width:100%">
      </div>
      <div class="dialog-actions" style="margin-top:12px">
        <button id="chat-save-cancel">取消</button>
        <button id="chat-save-confirm" class="primary">保存</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  const $name = document.getElementById("chat-save-name");
  $name.select();
  document.getElementById("chat-save-cancel").addEventListener("click", () => overlay.remove());
  document.getElementById("chat-save-confirm").addEventListener("click", async () => {
    const name = $name.value.trim() || defaultName;
    const payload = {
      name,
      timestamp: new Date().toISOString(),
      activeCharId: state.activeCharId,
      charName: activeChar?.name || "",
      selectedModels: [...state.selectedModels],
      conversations: {},
      params: getParams(),
    };
    for (const mid of state.selectedModels) {
      payload.conversations[mid] = (state.conversations[mid] || []).map((m) => ({
        role: m.role,
        content: m.content,
      }));
    }
    try {
      const resp = await fetch("/api/chats", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) throw new Error("save failed");
      overlay.remove();
    } catch (e) {
      alert("保存失败: " + e.message);
    }
  });
}

/**
 * 打开"聊天记录"弹窗，展示所有已保存的对话。
 * 支持加载历史对话到当前界面，或删除不需要的记录。
 * 列表按 ID 倒序排列（最新的在前）。
 */
async function openChatHistory() {
  const overlay = document.createElement("div");
  overlay.className = "dialog-overlay";
  overlay.innerHTML = `
    <div class="dialog" style="max-width:560px;max-height:80vh;display:flex;flex-direction:column">
      <h3>📋 聊天记录</h3>
      <div id="chat-history-list" style="flex:1;overflow-y:auto;margin:12px 0">
        <div style="text-align:center;color:var(--text-muted);padding:20px">加载中...</div>
      </div>
      <div class="dialog-actions">
        <button id="chat-history-close">关闭</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  document.getElementById("chat-history-close").addEventListener("click", () => overlay.remove());

  try {
    const resp = await fetch("/api/chats");
    const data = await resp.json();
    const list = document.getElementById("chat-history-list");

    if (!data.chats || data.chats.length === 0) {
      list.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:20px">暂无保存的对话</div>';
      return;
    }

    const sorted = data.chats.sort((a, b) => b.id - a.id);
    list.innerHTML = sorted
      .map((c) => {
        const date = c.timestamp
          ? new Date(c.timestamp).toLocaleString("zh-CN")
          : "";
        const models = (c.models || []).map((m) => m.split("/").pop()).join(", ");
        return `<div class="chat-history-item" data-id="${c.id}">
          <div class="chat-history-main">
            <span class="chat-history-name">${escapeHtml(c.name || "未命名")}</span>
            <span class="chat-history-meta">${escapeHtml(date)}</span>
          </div>
          <div class="chat-history-sub">
            ${c.charName ? `<span class="chat-history-char">🎭 ${escapeHtml(c.charName)}</span>` : ""}
            ${models ? `<span class="chat-history-models">🤖 ${escapeHtml(models)}</span>` : ""}
          </div>
          <div class="chat-history-actions">
            <button class="chat-load-btn" data-id="${c.id}" title="加载此对话">加载</button>
            <button class="chat-delete-btn danger" data-id="${c.id}" title="删除">🗑️</button>
          </div>
        </div>`;
      })
      .join("");

    list.querySelectorAll(".chat-load-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await loadChat(parseInt(btn.dataset.id));
        overlay.remove();
      });
    });

    list.querySelectorAll(".chat-delete-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm("确定删除这条聊天记录？")) return;
        try {
          await fetch(`/api/chats/${btn.dataset.id}`, { method: "DELETE" });
          btn.closest(".chat-history-item").remove();
          if (list.querySelectorAll(".chat-history-item").length === 0) {
            list.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:20px">暂无保存的对话</div>';
          }
        } catch (e) {
          alert("删除失败: " + e.message);
        }
      });
    });
  } catch (e) {
    document.getElementById("chat-history-list").innerHTML =
      `<div style="text-align:center;color:var(--danger);padding:20px">加载失败: ${escapeHtml(e.message)}</div>`;
  }
}

/**
 * 从后端加载指定 ID 的对话记录，恢复系统提示、参数、模型选择和所有对话内容。
 */
async function loadChat(chatId) {
  try {
    const resp = await fetch(`/api/chats/${chatId}`);
    if (!resp.ok) throw new Error("load failed");
    const { chat } = await resp.json();

    // 恢复模型参数到 UI 滑块
    if (chat.params) {
      if (chat.params.temperature != null) {
        $temperature.value = chat.params.temperature;
        document.getElementById("temp-val").textContent = parseFloat(chat.params.temperature).toFixed(2);
      }
      if (chat.params.top_p != null) {
        $topP.value = chat.params.top_p;
        document.getElementById("topp-val").textContent = parseFloat(chat.params.top_p).toFixed(2);
      }
      if (chat.params.max_tokens != null) $maxTokens.value = chat.params.max_tokens;
      if (chat.params.frequency_penalty != null) {
        $freqPenalty.value = chat.params.frequency_penalty;
        document.getElementById("freq-val").textContent = parseFloat(chat.params.frequency_penalty).toFixed(2);
      }
      if (chat.params.presence_penalty != null) {
        $presPenalty.value = chat.params.presence_penalty;
        document.getElementById("pres-val").textContent = parseFloat(chat.params.presence_penalty).toFixed(2);
      }
    }

    // 恢复角色卡选择
    if (chat.activeCharId != null) {
      state.activeCharId = chat.activeCharId;
      $charSelect.value = chat.activeCharId || "";
      onCharSelect();
    }

    // 恢复模型选择和对话内容
    state.selectedModels = chat.selectedModels || [];
    state.conversations = {};
    for (const mid of state.selectedModels) {
      state.conversations[mid] = (chat.conversations?.[mid] || []).map((m) => ({
        role: m.role,
        content: m.content,
      }));
    }

    renderModelList();
    renderSelectedTags();
    renderChatColumns();
  } catch (e) {
    alert("加载对话失败: " + e.message);
  }
}

// ── 预设管理 ────────────────────────────────────────────
/**
 * 从后端加载已保存的预设列表。
 */
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

/**
 * 应用选中的预设：将预设中的系统提示、参数、模型列表恢复到界面上。
 */
function applyPreset() {
  const id = parseInt($presetSelect.value);
  if (!id) return;
  const preset = state.presets.find((p) => p.id === id);
  if (!preset) return;

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

/**
 * 打开"保存预设"弹窗，让用户输入预设名称后保存当前配置。
 */
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

// ── 世界书管理 ──────────────────────────────────────────
/**
 * 从后端加载世界书列表。
 */
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

/**
 * 渲染世界书卡片列表，每张卡片显示名称、启用/禁用开关、条目计数，以及编辑/删除按钮。
 */
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

/**
 * 导入世界书文件（JSON 格式），通过 FormData 上传到后端解析。
 */
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

/**
 * 打开世界书编辑器弹窗（新建或编辑）。
 * 提供世界书名称输入、条目列表（每个条目含关键词和内容），支持动态添加/删除条目。
 * 保存时根据是否为新建调用 POST 或 PUT 接口。
 */
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

  // 从 DOM 中收集用户对条目的编辑内容，同步回 book.entries
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

// ── 角色卡管理（CRUD、导入、AI 生成、AI 改造、编辑器） ──
/**
 * 从后端加载角色卡列表。
 */
async function loadCharacters() {
  try {
    const resp = await fetch("/api/characters");
    const data = await resp.json();
    state.characters = data.characters;
    if (state.charSource === "local") renderCharSelect();
  } catch (e) {
    console.error("Failed to load characters:", e);
  }
}

async function loadKolCharacters() {
  try {
    const resp = await fetch("/api/kol-characters");
    const data = await resp.json();
    state.kolCharacters = (data.items || []).filter(it => it.versions && it.versions.length > 0);
    if (state.charSource === "kol") renderCharSelect();
  } catch (e) {
    console.error("Failed to load KOL characters:", e);
  }
}

function _kolToChar(entry) {
  const selectedVer = state.kolVersionMap[entry.key];
  const verObj = selectedVer != null
    ? entry.versions.find(v => v.version === selectedVer) || entry.versions[entry.versions.length - 1]
    : entry.versions[entry.versions.length - 1];
  return {
    ...verObj.data,
    id: entry.key,
    _kol: true,
    _kolEntry: entry,
    _kolVersion: verObj.version,
  };
}

function onKolVersionChange() {
  const sel = document.getElementById("kol-version-select");
  if (!sel || !state.activeCharId) return;
  state.kolVersionMap[state.activeCharId] = parseInt(sel.value);
  renderCharInfo();
}

function switchCharSource(source) {
  if (state.charSource === source) return;
  state.charSource = source;
  state.activeCharId = null;

  $btnSrcLocal.style.background = source === "local" ? "var(--accent)" : "var(--surface)";
  $btnSrcLocal.style.color = source === "local" ? "#fff" : "var(--text)";
  $btnSrcKol.style.background = source === "kol" ? "var(--accent)" : "var(--surface)";
  $btnSrcKol.style.color = source === "kol" ? "#fff" : "var(--text)";

  const localOnly = source === "local";
  [$btnNewChar, $btnGenChar, $btnImportChar].forEach(el => {
    if (el) el.style.display = localOnly ? "" : "none";
  });

  renderCharSelect();
  renderCharInfo();
}

function renderCharSelect() {
  if (state.charSource === "kol") {
    $charSelect.innerHTML =
      '<option value="">-- 未选择 KOL 角色 --</option>' +
      state.kolCharacters
        .map((it) => {
          const name = it.versions[it.versions.length - 1].data.name || it.kol_name || "未命名";
          const label = `${name} (${it.outfit_code})`;
          return `<option value="${escapeHtml(it.key)}">${escapeHtml(label)}</option>`;
        })
        .join("");
  } else {
    $charSelect.innerHTML =
      '<option value="">-- 未选择角色 --</option>' +
      state.characters
        .map((c) => `<option value="${c.id}">${escapeHtml(c.name || "未命名")}</option>`)
        .join("");
  }

  if (state.activeCharId) {
    $charSelect.value = state.activeCharId;
  }
}

function onCharSelect() {
  if (state.charSource === "kol") {
    state.activeCharId = $charSelect.value || null;
  } else {
    const id = parseInt($charSelect.value);
    state.activeCharId = id || null;
  }
  renderCharInfo();
}

/**
 * 渲染角色卡信息面板：标签、头像（图片或 Prompt 文本）、开场白发送按钮。
 */
function renderCharInfo() {
  const char = getActiveChar();
  const $avatarArea = document.getElementById("char-avatar-area");
  if (!char) {
    $charInfo.hidden = true;
    return;
  }

  $charInfo.hidden = false;

  const isKol = state.charSource === "kol";
  [$btnRemixChar, $btnEditChar, $btnDeleteChar].forEach(el => {
    if (el) el.style.display = isKol ? "none" : "";
  });

  const $kolVersionBar = document.getElementById("kol-version-bar");
  const $kolVersionSelect = document.getElementById("kol-version-select");
  if ($kolVersionBar && $kolVersionSelect) {
    if (isKol && char._kolEntry && char._kolEntry.versions.length > 1) {
      $kolVersionBar.hidden = false;
      const entry = char._kolEntry;
      $kolVersionSelect.innerHTML = entry.versions
        .map((v) => {
          const date = v.created_at ? new Date(v.created_at).toLocaleDateString() : "";
          const label = `v${v.version}${date ? " (" + date + ")" : ""}`;
          return `<option value="${v.version}">${escapeHtml(label)}</option>`;
        })
        .join("");
      const current = state.kolVersionMap[entry.key] ?? entry.versions[entry.versions.length - 1].version;
      $kolVersionSelect.value = current;
    } else {
      $kolVersionBar.hidden = true;
    }
  }

  const tags = char.tags || [];
  $charTags.innerHTML = tags.length > 0
    ? tags.map((t) => `<span class="char-tag">${escapeHtml(t)}</span>`).join("")
    : '<span style="color:var(--text-muted);font-size:10px;">无标签</span>';

  $btnSendGreeting.style.display = char.first_mes ? "" : "none";

  if ($avatarArea) {
    if (char.avatar && char.avatar_type === "image") {
      $avatarArea.hidden = false;
      $avatarArea.innerHTML = `
        <img class="char-avatar-img" src="${char.avatar}" alt="${escapeHtml(char.name)}">`;
    } else if (char.avatar && char.avatar_type === "prompt") {
      $avatarArea.hidden = false;
      $avatarArea.innerHTML = `
        <div class="char-avatar-prompt">
          <div class="char-avatar-prompt-header">
            <span>🖼️ 形象 Prompt</span>
            <button class="char-avatar-copy" title="复制">复制</button>
          </div>
          <div class="char-avatar-prompt-text">${escapeHtml(char.avatar)}</div>
        </div>`;
      $avatarArea.querySelector(".char-avatar-copy")?.addEventListener("click", (e) => {
        navigator.clipboard.writeText(char.avatar).then(() => {
          e.target.textContent = "已复制 ✓";
          setTimeout(() => e.target.textContent = "复制", 1500);
        });
      });
    } else {
      $avatarArea.hidden = true;
      $avatarArea.innerHTML = "";
    }
  }
}

/**
 * 将角色的开场白（first_mes）作为助手消息发送到所有已选模型的对话中。
 */
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

/**
 * 导入角色卡文件（JSON/PNG 格式），上传到后端解析后加入角色列表。
 */
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

/**
 * 打开"AI 生成角色卡"弹窗。
 * 提供两种生成模式：
 *   - 文本模式：用户输入关键词或角色概念描述，AI 据此生成完整角色卡
 *   - 图片模式：用户上传角色图片（支持拖拽），AI 从图片分析并生成角色卡
 * 两种模式都支持选择生成用的 AI 模型（自动过滤无审核、足够长输出的模型）。
 * 生成成功后自动添加到角色列表并选中。
 */
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

  // 标签页切换逻辑
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

  // 图片上传：支持点击选择和拖拽
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

  // 填充模型下拉框：优先排列常用模型，图片模式下过滤支持图像输入的模型
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

  // 执行生成请求：根据 payload 类型选择不同的 API 端点
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

/**
 * 打开"AI 改造角色卡"弹窗。
 * 展示当前角色卡信息预览，用户输入改造指令（如改变性格、背景、增加设定等），
 * AI 会基于原卡数据结合改造指令生成一张新的角色卡。
 * 改造完成后作为新角色保存，不影响原卡。
 */
function openCharRemixer(char) {
  const overlay = document.createElement("div");
  overlay.className = "dialog-overlay";
  overlay.innerHTML = `
    <div class="dialog" style="max-width:580px">
      <h3>🔄 AI 改造角色卡</h3>
      <div class="remix-card-preview">
        <span class="remix-card-name">🎭 ${escapeHtml(char.name)}</span>
        <span class="remix-card-tags">${(char.tags || []).map(t => `<span class="char-tag">${escapeHtml(t)}</span>`).join(" ")}</span>
      </div>
      <div class="char-field" style="margin-top:12px">
        <label>改造指令</label>
        <textarea id="remix-instructions" rows="5" placeholder="描述你想要的改造方向，例如：\n• 添加纹身女特征，全身有大面积花臂纹身\n• 把背景改到赛博朋克世界\n• 性格变得更加叛逆野性\n• 增加一段创伤记忆作为纹身的来源"></textarea>
        <div class="hint">原卡会作为基础，AI 会将改造融入所有字段和 character_book</div>
      </div>
      <div class="char-field" style="margin-top:8px">
        <label>生成模型</label>
        <select id="remix-model"><option value="">加载中...</option></select>
      </div>
      <div id="remix-status" style="display:none;margin-top:8px;padding:10px;border-radius:6px;background:var(--bg-card);font-size:12px;color:var(--text-muted);white-space:pre-wrap"></div>
      <div class="dialog-actions" style="margin-top:12px">
        <button id="remix-cancel">取消</button>
        <button id="remix-submit" class="primary">开始改造</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  const $model = document.getElementById("remix-model");
  const $status = document.getElementById("remix-status");
  const $submit = document.getElementById("remix-submit");

  // 筛选适合的模型：无审核、上下文 >= 16K，按偏好排序
  const preferred = ["grok", "claude", "gemini", "deepseek"];
  const suitable = state.models
    .filter(m => !m.is_moderated && (m.context_length || 0) >= 16000)
    .sort((a, b) => {
      const ai = preferred.findIndex(p => a.id.toLowerCase().includes(p));
      const bi = preferred.findIndex(p => b.id.toLowerCase().includes(p));
      const aPref = ai >= 0 ? ai : 999;
      const bPref = bi >= 0 ? bi : 999;
      return aPref - bPref || a.name.localeCompare(b.name);
    });
  $model.innerHTML = suitable
    .map(m => `<option value="${m.id}">${m.name}</option>`)
    .join("");
  const defaultModel = suitable.find(m => m.id.includes("grok-4.1-fast"));
  if (defaultModel) $model.value = defaultModel.id;

  document.getElementById("remix-cancel").addEventListener("click", () => overlay.remove());

  $submit.addEventListener("click", async () => {
    const instructions = document.getElementById("remix-instructions").value.trim();
    if (!instructions) { alert("请输入改造指令"); return; }

    $submit.disabled = true;
    $submit.textContent = "生成中...";
    $status.style.display = "block";
    $status.textContent = "正在改造角色卡，这可能需要 30-60 秒...";

    // 深拷贝原卡数据，移除 id 以作为新角色保存
    const original = JSON.parse(JSON.stringify(char));
    delete original.id;

    try {
      const resp = await fetch("/api/characters/remix", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ original, instructions, model: $model.value }),
      });
      const data = await resp.json();
      if (data.error) {
        $status.style.color = "var(--danger)";
        $status.textContent = "生成失败: " + data.error;
        if (data.raw) $status.textContent += "\n\n原始输出:\n" + data.raw;
        $submit.disabled = false;
        $submit.textContent = "重试";
        return;
      }

      state.characters.push(data.character);
      state.activeCharId = data.character.id;
      renderCharSelect();
      $charSelect.value = data.character.id;
      onCharSelect();

      $status.style.color = "var(--success)";
      $status.textContent = `✅ 改造完成！新角色「${data.character.name}」已保存`;
      $submit.textContent = "完成";
      $submit.disabled = false;
      $submit.addEventListener("click", () => overlay.remove(), { once: true });
    } catch (e) {
      $status.style.color = "var(--danger)";
      $status.textContent = "请求失败: " + e.message;
      $submit.disabled = false;
      $submit.textContent = "重试";
    }
  });
}

/**
 * 打开角色卡编辑器弹窗（新建或编辑已有角色卡）。
 * 提供所有 V2 角色卡字段的表单：名称、描述、性格、场景、开场白、
 * 示例对话、系统提示、标签、作者备注。
 * 保存时根据是新建还是编辑调用 POST 或 PUT 接口。
 */
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
          <div class="hint">角色专属的系统指令，与角色设定一起注入</div>
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

// ── 启动应用 ────────────────────────────────────────────
init();
