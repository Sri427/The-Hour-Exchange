// The Hour Exchange — Community Map
// Teardrop pins, warm tile terrain, always-visible name+skill labels.
// Gold pins = help requests, forest pins = skill offers.

class CommunityMap {
    constructor(canvasId, tooltipId) {
        this.canvas = document.getElementById(canvasId);
        if (!this.canvas) return;
        this.ctx = this.canvas.getContext('2d');
        this.tooltip = document.getElementById(tooltipId);
        this.tasks = [];
        this.highlightedId = null;
        this.hoveredTask = null;
        this.glowPhase = 0;
        this.visibleIds = null;
        this.focusedIdx = -1;

        this._resize();
        window.addEventListener('resize', () => this._resize());
        this.canvas.addEventListener('mousemove', e => this._onMove(e));
        this.canvas.addEventListener('mouseleave', () => this._clearHover());
        this.canvas.addEventListener('click', () => this._clickPin());
        this.canvas.setAttribute('tabindex', '0');
        this.canvas.addEventListener('keydown', e => this._onKey(e));

        this._loop();
    }

    _resize() {
        const box = this.canvas.getBoundingClientRect();
        this.w = box.width || 400;
        this.h = box.height || 400;
        this.canvas.width  = this.w * devicePixelRatio;
        this.canvas.height = this.h * devicePixelRatio;
        this.ctx.scale(devicePixelRatio, devicePixelRatio);
        this._reproject();
    }

    _reproject() {
        this.tasks = this.tasks.map(t => ({
            ...t,
            cx: (t.lat / 800) * this.w,
            cy: (t.lng / 500) * this.h,
        }));
    }

    setTasks(list) {
        this.tasks = list.map(t => ({
            ...t,
            cx: (t.lat / 800) * this.w,
            cy: (t.lng / 500) * this.h,
        }));
    }

    highlightPin(id) { this.highlightedId = id; }
    clearHighlight()  { this.highlightedId = null; }
    setVisibleTaskIds(ids) { this.visibleIds = ids; }

    // ── Warm terrain ─────────────────────────────────────
    _drawTerrain() {
        const c = this.ctx;
        const w = this.w, h = this.h;

        // Warm cream base — matches app palette
        c.fillStyle = '#F5F0E6';
        c.fillRect(0, 0, w, h);

        // Subtle radial warmth
        const grad = c.createRadialGradient(w * 0.4, h * 0.5, 0, w * 0.4, h * 0.5, w * 0.8);
        grad.addColorStop(0, 'rgba(255,248,228,0.6)');
        grad.addColorStop(1, 'rgba(232,220,195,0.3)');
        c.fillStyle = grad;
        c.fillRect(0, 0, w, h);

        // Street grid — warm stone, very soft
        c.strokeStyle = 'rgba(180,160,120,0.18)';
        c.lineWidth = 1;
        for (let x = 0; x < w; x += 48) {
            c.beginPath(); c.moveTo(x, 0); c.lineTo(x, h); c.stroke();
        }
        for (let y = 0; y < h; y += 48) {
            c.beginPath(); c.moveTo(0, y); c.lineTo(w, y); c.stroke();
        }

        // A slightly darker road through the middle — gives it map feel
        c.strokeStyle = 'rgba(160,140,100,0.14)';
        c.lineWidth = 3;
        c.beginPath(); c.moveTo(w * 0.2, 0); c.lineTo(w * 0.35, h); c.stroke();
        c.beginPath(); c.moveTo(0, h * 0.4); c.lineTo(w, h * 0.55); c.stroke();

        // Town Green — muted sage, not bright green
        c.fillStyle = 'rgba(88,130,100,0.09)';
        c.strokeStyle = 'rgba(88,130,100,0.16)';
        c.lineWidth = 1;
        c.beginPath();
        c.roundRect(w * 0.38, h * 0.3, w * 0.26, h * 0.28, 10);
        c.fill(); c.stroke();
        c.fillStyle = 'rgba(60,90,70,0.28)';
        c.font = `500 9px 'Plus Jakarta Sans', sans-serif`;
        c.textAlign = 'left';
        c.textBaseline = 'top';
        c.fillText('Town Green', w * 0.38 + 8, h * 0.3 + 8);

        // Mill River — very subtle blue wash
        c.fillStyle = 'rgba(100,160,200,0.05)';
        c.beginPath();
        c.moveTo(0, h * 0.15);
        c.bezierCurveTo(w * 0.3, h * 0.06, w * 0.55, h * 0.38, w, h * 0.26);
        c.lineTo(w, h * 0.34);
        c.bezierCurveTo(w * 0.55, h * 0.46, w * 0.3, h * 0.14, 0, h * 0.23);
        c.closePath(); c.fill();
    }

    // ── Teardrop pin ─────────────────────────────────────
    _drawTeardrop(c, cx, cy, r, color, alpha = 1) {
        // Teardrop: circle on top, point below
        c.globalAlpha = alpha;
        c.fillStyle = color;
        c.shadowColor = 'rgba(44,40,32,0.2)';
        c.shadowBlur = 6;
        c.shadowOffsetY = 2;

        c.beginPath();
        // circle part
        c.arc(cx, cy - r, r, Math.PI, 0, false);
        // curve to point
        c.bezierCurveTo(cx + r, cy - r + r * 0.6, cx + r * 0.45, cy + r * 0.6, cx, cy + r * 1.2);
        c.bezierCurveTo(cx - r * 0.45, cy + r * 0.6, cx - r, cy - r + r * 0.6, cx, cy - r * 2);
        c.closePath();
        c.fill();
        c.shadowBlur = 0; c.shadowOffsetY = 0;

        // White dot inside
        c.fillStyle = 'rgba(255,255,255,0.85)';
        c.beginPath();
        c.arc(cx, cy - r, r * 0.38, 0, Math.PI * 2);
        c.fill();

        c.globalAlpha = 1;
    }

    // ── Pins ─────────────────────────────────────────────
    _drawPins() {
        const c = this.ctx;
        this.glowPhase = (this.glowPhase + 0.018) % (Math.PI * 2);
        const pulse = (Math.sin(this.glowPhase) + 1) / 2;

        this.tasks.forEach(t => {
            if (this.visibleIds && !this.visibleIds.includes(t.id)) return;

            const isReq = t.task_type === 'request';
            const color = isReq ? '#C9920B' : '#1B4332';
            const highlighted = this.highlightedId === t.id;
            const hovered = this.hoveredTask?.id === t.id;
            const focused = this.focusedIdx >= 0 &&
                this.tasks.filter(x => !this.visibleIds || this.visibleIds.includes(x.id))[this.focusedIdx]?.id === t.id;

            const r = (highlighted || hovered || focused) ? 12 : 9;
            const { cx, cy } = t;
            // pin tip is at cy, circle top is at cy - 2r
            const pinTipY = cy;

            // High-urgency pulse ring — drawn before pin so it's behind
            if (t.urgency === 'high') {
                const pulseR = r + 8 + pulse * 10;
                c.globalAlpha = 0.08 + pulse * 0.14;
                c.fillStyle = color;
                c.beginPath();
                c.arc(cx, pinTipY - r, pulseR, 0, Math.PI * 2);
                c.fill();
                c.globalAlpha = 1;
            }

            // Keyboard/hover focus ring
            if (focused || highlighted) {
                c.strokeStyle = '#C9920B';
                c.lineWidth = 2;
                c.globalAlpha = 0.7;
                c.beginPath();
                c.arc(cx, pinTipY - r, r + 5, 0, Math.PI * 2);
                c.stroke();
                c.globalAlpha = 1;
            }

            this._drawTeardrop(c, cx, pinTipY, r, color);

            // ── Name + skill label always visible below pin ──
            const firstName = (t.creator_username || '').split('_')[0];
            const skill = t.category || '';
            const labelY = pinTipY + r * 1.4;

            c.textAlign = 'center';
            c.textBaseline = 'top';

            // Measure pill width
            c.font = `600 8px 'Plus Jakarta Sans', sans-serif`;
            const nameW = c.measureText(firstName).width;
            c.font = `500 7px 'Plus Jakarta Sans', sans-serif`;
            const skillW = c.measureText(skill).width;
            const pillW = Math.max(nameW, skillW) + 14;
            const pillH = 24;

            // Soft pill background — no border, just a warm shadow
            c.shadowColor = 'rgba(44,40,32,0.08)';
            c.shadowBlur = 4;
            c.shadowOffsetY = 1;
            c.fillStyle = 'rgba(253,250,243,0.92)';
            c.beginPath();
            c.roundRect(cx - pillW / 2, labelY, pillW, pillH, 5);
            c.fill();
            c.shadowBlur = 0; c.shadowOffsetY = 0;

            // Name
            c.fillStyle = '#2C2820';
            c.font = `600 8px 'Plus Jakarta Sans', sans-serif`;
            c.fillText(firstName, cx, labelY + 3);

            // Skill
            c.fillStyle = color;
            c.font = `500 7px 'Plus Jakarta Sans', sans-serif`;
            c.fillText(skill, cx, labelY + 13);
        });
    }

    _loop() {
        this._drawTerrain();
        this._drawPins();
        requestAnimationFrame(() => this._loop());
    }

    // ── Mouse ─────────────────────────────────────────────
    _onMove(e) {
        const rect = this.canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left, my = e.clientY - rect.top;

        let found = null;
        for (const t of this.tasks) {
            if (this.visibleIds && !this.visibleIds.includes(t.id)) continue;
            const r = 9;
            // hit-test the teardrop: circle part at (cx, cy - r), point at (cx, cy + r*1.2)
            const distCircle = Math.hypot(mx - t.cx, my - (t.cy - r));
            const distTip = Math.hypot(mx - t.cx, my - (t.cy + r * 1.2));
            if (distCircle < r + 6 || distTip < 5) { found = t; break; }
        }

        if (found) {
            this.hoveredTask = found;
            this.canvas.style.cursor = 'pointer';
            this._showTooltip(found);
        } else {
            this._clearHover();
        }
    }

    _showTooltip(t) {
        if (!this.tooltip) return;
        const isReq = t.task_type === 'request';
        const badgeColor = isReq ? '#8A6300' : '#1B4332';
        const badgeBg = isReq ? 'rgba(201,146,11,0.09)' : 'rgba(27,67,50,0.07)';
        const label = isReq ? 'Needs help' : 'Offering a skill';

        this.tooltip.innerHTML = `
            <div style="padding:13px 15px;background:#FDFAF3;border-radius:13px;box-shadow:0 8px 24px rgba(44,40,32,0.13);max-width:220px;">
                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:7px;">
                    <span style="padding:2px 8px;border-radius:20px;font-size:9px;font-weight:600;background:${badgeBg};color:${badgeColor};">${label}</span>
                    <span style="font-size:11px;font-weight:600;color:#8A6300;">${t.tokens_value}h</span>
                </div>
                <div style="font-weight:600;font-size:11px;color:#2C2820;margin-bottom:3px;">${t.title}</div>
                <div style="font-size:10px;color:#6B6460;line-height:1.4;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;">${t.description}</div>
                <div style="margin-top:8px;font-size:9px;font-weight:600;color:#9C9490;">@${t.creator_username} · ${t.category}</div>
            </div>`;

        let tx = t.cx + 18, ty = t.cy - 60;
        if (tx + 240 > this.w) tx = t.cx - 240;
        if (ty < 0) ty = 8;

        this.tooltip.style.left = tx + 'px';
        this.tooltip.style.top  = ty + 'px';
        this.tooltip.style.opacity = '1';
        this.tooltip.classList.remove('hidden');
    }

    _clearHover() {
        this.hoveredTask = null;
        if (this.canvas) this.canvas.style.cursor = 'default';
        if (this.tooltip) { this.tooltip.style.opacity = '0'; this.tooltip.classList.add('hidden'); }
    }

    _clickPin() {
        if (!this.hoveredTask) return;
        this._scrollToCard(this.hoveredTask.id);
    }

    _scrollToCard(id) {
        const card = document.getElementById('task-card-' + id);
        if (!card) return;
        card.scrollIntoView({ behavior: 'smooth', block: 'center' });
        card.style.outline = '2px solid rgba(201,146,11,0.6)';
        card.style.outlineOffset = '3px';
        setTimeout(() => { card.style.outline = ''; card.style.outlineOffset = ''; }, 1600);
    }

    // ── Keyboard nav ──────────────────────────────────────
    _onKey(e) {
        const visible = this.tasks.filter(t => !this.visibleIds || this.visibleIds.includes(t.id));
        if (!visible.length) return;

        if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
            e.preventDefault();
            this.focusedIdx = (this.focusedIdx + 1) % visible.length;
            this._announcePin(visible[this.focusedIdx]);
        } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
            e.preventDefault();
            this.focusedIdx = (this.focusedIdx - 1 + visible.length) % visible.length;
            this._announcePin(visible[this.focusedIdx]);
        } else if (e.key === 'Enter' && this.focusedIdx >= 0) {
            e.preventDefault();
            this._scrollToCard(visible[this.focusedIdx].id);
        }
    }

    _announcePin(t) {
        if (this.canvas && t) {
            this.canvas.setAttribute('aria-label',
                `Map pin: ${t.title}, by @${t.creator_username}, ${t.category}, ${t.tokens_value} token${t.tokens_value !== 1 ? 's' : ''}`);
        }
    }
}

// Boot
let communityMap = null;
document.addEventListener('DOMContentLoaded', () => {
    const el = document.getElementById('chronoMap');
    if (!el) return;
    communityMap = new CommunityMap('chronoMap', 'mapTooltip');
    window.communityMap = communityMap;

    fetch('/api/tasks')
        .then(r => r.json())
        .then(list => { if (communityMap) communityMap.setTasks(list); })
        .catch(err => console.error('Map load error:', err));
});
