class InteractivePlayer {
  /**
   * @param {HTMLElement} container
   * @param {object}      config    - scene-data.json 的解析结果
   * @param {object}      [opts]
   * @param {string}      [opts.baseUrl=''] - 资源文件的 URL 前缀（末尾带 /）
   */
  constructor(container, config, opts = {}) {
    this.container = container;
    this.config = config;
    this.baseUrl = opts.baseUrl || '';
    this.currentStateId = null;
    this.currentVideo = null;
    this.videoPool = new Map();
    this.transitioning = false;
    this.debug = false;

    this.videoLayer = null;
    this.gridOverlay = null;
    this.loadingOverlay = null;
    this.debugInfo = null;
    this.startOverlay = null;
    this.pulseDots = [];
    this.activeSounds = [];
  }

  async init() {
    this._buildDOM();
    this._bindGlobalEvents();
    await this._preloadAll();
    this._showStartOverlay();
  }

  // ── DOM Construction ──────────────────────────────────────

  _buildDOM() {
    this.container.classList.add('interactive-player');
    this.container.innerHTML = '';

    this.videoLayer = this._el('div', 'player-video-layer');
    this.container.appendChild(this.videoLayer);

    for (const [id, filename] of Object.entries(this.config.resources)) {
      const video = document.createElement('video');
      video.playsInline = true;
      video.preload = 'auto';
      video.crossOrigin = 'anonymous';
      // 完整 URL 直接使用，本地文件拼接 baseUrl
      const isFullUrl = filename.startsWith('http://') || filename.startsWith('https://');
      video.src = isFullUrl ? filename : this.baseUrl + encodeURIComponent(filename);
      this.videoPool.set(id, video);
      this.videoLayer.appendChild(video);
    }

    this._buildGrid();
    this._buildLoadingOverlay();
    this._buildStartOverlay();

    this.debugInfo = this._el('div', 'player-debug-info');
    this.container.appendChild(this.debugInfo);
  }

  _buildGrid() {
    this.gridOverlay = this._el('div', 'player-grid-overlay');
    const { cols, rows } = this.config.config.grid;
    this.gridOverlay.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
    this.gridOverlay.style.gridTemplateRows = `repeat(${rows}, 1fr)`;

    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const cell = this._el('div', 'player-grid-cell');
        cell.dataset.row = r;
        cell.dataset.col = c;
        cell.addEventListener('click', () => this._handleClick(r, c));
        this.gridOverlay.appendChild(cell);
      }
    }
    this.container.appendChild(this.gridOverlay);
  }

  _buildLoadingOverlay() {
    const total = Object.keys(this.config.resources).length;
    this.loadingOverlay = this._el('div', 'player-loading');
    this.loadingOverlay.innerHTML = `
      <div class="loading-spinner"></div>
      <div class="loading-text">加载资源中</div>
      <div class="loading-bar-track"><div class="loading-bar-fill" id="load-bar-fill"></div></div>
      <div class="loading-progress" id="load-progress">0 / ${total}</div>
    `;
    this.container.appendChild(this.loadingOverlay);
  }

  _buildStartOverlay() {
    this.startOverlay = this._el('div', 'player-start-overlay');
    this.startOverlay.innerHTML = `
      <div class="start-icon">▶</div>
      <div class="start-text">点击开始</div>
    `;
    this.startOverlay.style.display = 'none';
    this.startOverlay.addEventListener('click', () => {
      this.startOverlay.style.display = 'none';
      this.enterState(this.config.initialState);
    });
    this.container.appendChild(this.startOverlay);
  }

  _el(tag, className) {
    const el = document.createElement(tag);
    el.className = className;
    return el;
  }

  // ── Events ────────────────────────────────────────────────

  _bindGlobalEvents() {
    document.addEventListener('keydown', (e) => {
      if (e.key === 'd' || e.key === 'D') {
        this.debug = !this.debug;
        this.container.classList.toggle('debug', this.debug);
        this._updateDebug();
        this._highlightGrid();
      }
    });
    window.addEventListener('resize', () => { this._syncGridPosition(); this._renderPulsePoints(); });
  }

  // ── Preloading ────────────────────────────────────────────

  async _preloadAll() {
    const total = this.videoPool.size;
    let loaded = 0;
    const barFill = this.loadingOverlay.querySelector('#load-bar-fill');
    const progressText = this.loadingOverlay.querySelector('#load-progress');

    const promises = [];
    for (const [id, video] of this.videoPool) {
      promises.push(new Promise(resolve => {
        const done = () => {
          loaded++;
          const pct = (loaded / total) * 100;
          barFill.style.width = pct + '%';
          progressText.textContent = `${loaded} / ${total}`;
          resolve();
        };
        if (video.readyState >= 3) { done(); return; }
        video.addEventListener('canplaythrough', done, { once: true });
        video.addEventListener('error', () => {
          console.warn(`Failed to load: ${id}`);
          done();
        }, { once: true });
      }));
    }
    await Promise.all(promises);
  }

  _showStartOverlay() {
    this.loadingOverlay.style.display = 'none';
    this.startOverlay.style.display = '';
  }

  // ── State Machine ─────────────────────────────────────────

  enterState(stateId) {
    if (this.transitioning) return;
    const state = this.config.states[stateId];
    if (!state) { console.error(`Unknown state: ${stateId}`); return; }

    const video = this.videoPool.get(state.video);
    if (!video) { console.error(`Unknown resource: ${state.video}`); return; }

    if (video.readyState < 2) {
      this.transitioning = true;
      this._showMiniLoading(true);
      video.addEventListener('canplay', () => {
        this._showMiniLoading(false);
        this.transitioning = false;
        this._activateState(stateId, state, video);
      }, { once: true });
      return;
    }
    this._activateState(stateId, state, video);
  }

  _activateState(stateId, state, video) {
    if (this.currentVideo) {
      this.currentVideo.pause();
      this.currentVideo.classList.remove('active');
      this.currentVideo.onended = null;
    }

    video.currentTime = 0;
    video.loop = !!state.loop;
    video.classList.add('active');
    video.play().catch(() => {});

    this.currentVideo = video;
    this.currentStateId = stateId;

    this._playSounds(state);

    if (!state.loop && state.next) {
      video.onended = () => {
        video.onended = null;
        this.enterState(state.next);
      };
    }

    this._syncGridPosition();
    this._renderPulsePoints();
    this._highlightGrid();
    this._updateDebug();
  }

  _showMiniLoading(show) {
    let el = this.container.querySelector('.player-mini-loading');
    if (show && !el) {
      el = this._el('div', 'player-mini-loading');
      el.innerHTML = '<div class="loading-spinner small"></div>';
      this.container.appendChild(el);
    } else if (!show && el) {
      el.remove();
    }
  }

  // ── Click Handling ────────────────────────────────────────

  _handleClick(row, col) {
    if (this.transitioning || !this.currentStateId) return;
    const state = this.config.states[this.currentStateId];
    if (!state.on_click) return;

    for (const action of state.on_click) {
      if (this._matchRegion(action.regions, row, col)) {
        this.enterState(action.target);
        return;
      }
    }
  }

  _matchRegion(regions, row, col) {
    if (regions === '*') return true;
    if (Array.isArray(regions))
      return regions.some(([r, c]) => r === row && c === col);
    return regions.rows.includes(row) && regions.cols.includes(col);
  }

  // ── Grid Positioning ──────────────────────────────────────

  _syncGridPosition() {
    const v = this.currentVideo;
    if (!v) return;

    const cw = this.container.clientWidth;
    const ch = this.container.clientHeight;
    const vw = v.videoWidth || cw;
    const vh = v.videoHeight || ch;
    const car = cw / ch;
    const var_ = vw / vh;

    let dw, dh, ox, oy;
    if (var_ > car) {
      dw = cw; dh = cw / var_; ox = 0; oy = (ch - dh) / 2;
    } else {
      dh = ch; dw = ch * var_; ox = (cw - dw) / 2; oy = 0;
    }

    Object.assign(this.gridOverlay.style, {
      left: ox + 'px', top: oy + 'px',
      width: dw + 'px', height: dh + 'px',
    });
  }

  // ── Sound Playback ────────────────────────────────────────

  _stopSounds() {
    for (const audio of this.activeSounds) {
      audio.pause();
      audio.currentTime = 0;
    }
    this.activeSounds = [];
  }

  _playSounds(state) {
    this._stopSounds();
    if (!state.sounds || !state.sounds.length) return;

    for (const resId of state.sounds) {
      if (!resId) continue;
      const filename = this.config.resources[resId];
      if (!filename) continue;
      const isFullUrl = filename.startsWith('http://') || filename.startsWith('https://');
      const audio = new Audio(isFullUrl ? filename : this.baseUrl + encodeURIComponent(filename));
      audio.loop = !!state.loop;
      audio.play().catch(() => {});
      this.activeSounds.push(audio);
    }
  }

  // ── Pulse Points ──────────────────────────────────────────

  _clearPulsePoints() {
    for (const dot of this.pulseDots) dot.remove();
    this.pulseDots = [];
  }

  _renderPulsePoints() {
    this._clearPulsePoints();
    const state = this.currentStateId ? this.config.states[this.currentStateId] : null;
    if (!state || !state.on_click) return;

    const { cols, rows } = this.config.config.grid;
    const gw = this.gridOverlay.offsetWidth;
    const gh = this.gridOverlay.offsetHeight;
    const cellW = gw / cols;
    const cellH = gh / rows;

    for (const action of state.on_click) {
      const pulseCells = action.pulse_cells || [];
      for (const [r, c] of pulseCells) {
        const dot = document.createElement('div');
        dot.className = 'pulse-dot';
        dot.style.left = (c + 0.5) * cellW + 'px';
        dot.style.top = (r + 0.5) * cellH + 'px';
        this.gridOverlay.appendChild(dot);
        this.pulseDots.push(dot);
      }
    }
  }

  // ── Debug ─────────────────────────────────────────────────

  _highlightGrid() {
    const state = this.currentStateId ? this.config.states[this.currentStateId] : null;
    const cells = this.gridOverlay.querySelectorAll('.player-grid-cell');

    cells.forEach(cell => {
      cell.classList.remove('region-a', 'region-b', 'region-c');
      if (!this.debug || !state || !state.on_click) return;

      const r = +cell.dataset.row;
      const c = +cell.dataset.col;

      for (let i = 0; i < state.on_click.length; i++) {
        if (this._matchRegion(state.on_click[i].regions, r, c)) {
          const cls = ['region-a', 'region-b', 'region-c'][i % 3];
          cell.classList.add(cls);
          break;
        }
      }
    });
  }

  _updateDebug() {
    if (!this.debug || !this.currentStateId) {
      this.debugInfo.innerHTML = '';
      return;
    }
    const state = this.config.states[this.currentStateId];
    const lines = [`<b>${this.currentStateId}</b> | video: ${state.video} | loop: ${state.loop}`];
    if (state.next) lines.push(`ended → ${state.next}`);
    if (state.on_click) {
      state.on_click.forEach((a, i) => {
        const tag = ['🟢', '🔵', '🟡'][i % 3];
        const r = a.regions === '*' ? '*' : `rows${JSON.stringify(a.regions.rows)} cols${JSON.stringify(a.regions.cols)}`;
        lines.push(`${tag} ${r} → ${a.target}`);
      });
    }
    this.debugInfo.innerHTML = lines.join('<br>');
  }
}
