// shared utilities — toast alerts, api wrapper, counter animations

function showToast(msg, type = 'info') {
    const deck = document.getElementById('toast-container');
    if (!deck) return;

    const t = document.createElement('div');
    t.className = `toast toast-${type}`;

    const icons = {
        success: 'fa-circle-check',
        error: 'fa-circle-exclamation',
        warning: 'fa-triangle-exclamation',
        info: 'fa-circle-info'
    };
    t.innerHTML = `<i class="fa-solid ${icons[type] || icons.info}"></i><span>${msg}</span>`;
    deck.appendChild(t);

    setTimeout(() => {
        t.style.animation = 'fadeOut 0.3s ease forwards';
        setTimeout(() => t.remove(), 300);
    }, 4000);
}

function animateValue(el, start, end, dur = 800) {
    if (!el) return;
    const range = end - start;
    const t0 = performance.now();

    function tick(now) {
        const pct = Math.min((now - t0) / dur, 1);
        const eased = 1 - Math.pow(1 - pct, 3);
        el.textContent = Math.round(start + range * eased);
        if (pct < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
}

async function api(url, method = 'GET', body = null) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body && method !== 'GET') opts.body = JSON.stringify(body);

    try {
        const res = await fetch(url, opts);
        return await res.json();
    } catch (err) {
        console.error('api error:', err);
        showToast('Could not reach the server. Check your connection.', 'error');
        return { success: false, message: 'Network error' };
    }
}

function updateWelcomeBar(data) {
    if (data.user_tokens !== undefined) {
        const el = document.getElementById('tokens-count');
        if (el) animateValue(el, parseInt(el.textContent) || 0, data.user_tokens);
    }
    if (data.reliability !== undefined) {
        const el = document.getElementById('reliability-score');
        if (el) el.textContent = data.reliability + '%';
    }
    if (data.global_hours !== undefined) {
        const el = document.getElementById('global-hours-count');
        if (el) animateValue(el, parseInt(el.textContent) || 0, data.global_hours);
    }
}

function openModal(id) {
    const m = document.getElementById(id);
    if (!m) return;
    m.classList.remove('hidden');
    m.classList.add('flex');

    // pre-fill the date picker to 2 hours from now
    const dt = m.querySelector('input[type="datetime-local"]');
    if (dt && !dt.value) {
        const d = new Date();
        d.setHours(d.getHours() + 2);
        d.setMinutes(0);
        dt.value = d.toISOString().slice(0, 16);
    }
}

function closeModal(id) {
    const m = document.getElementById(id);
    if (m) {
        m.classList.add('hidden');
        m.classList.remove('flex');
    }
}

document.addEventListener('click', e => {
    if (e.target.classList.contains('modal-backdrop')) {
        e.target.classList.add('hidden');
        e.target.classList.remove('flex');
    }
});

document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
        document.querySelectorAll('.modal-backdrop').forEach(m => {
            m.classList.add('hidden');
            m.classList.remove('flex');
        });
    }
});
