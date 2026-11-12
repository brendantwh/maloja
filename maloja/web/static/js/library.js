// shared helpers for the Library admin pages (rules, rule sets, aliases, duplicates, reparse)
// everything is in Lib, so nothing clashes with the globals of the other scripts
// pages register their handlers with Lib.actions({...}) and use data-click, data-change
// and data-input attributes instead of inline handlers

const Lib = (function() {

	const $ = id => document.getElementById(id);
	const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
	const plural = (n, word, words) => `${Number(n).toLocaleString()} ${n === 1 ? word : (words || word + 's')}`;
	// several artists show as separate pills, so "A, B" (one name) and A + B (two artists) look different
	const pills = v => (Array.isArray(v) ? v : [v]).map(a => `<span class="artist">${esc(a)}</span>`).join('');
	const data = id => JSON.parse($(id).textContent);

	// word level diff, so only the changed parts are highlighted
	function diff(a, b) {
		const split = s => String(s).split(/(\s+|,)/).filter(s => s !== '');
		const x = split(a), y = split(b);
		const m = x.length, n = y.length, L = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
		for (let i = m - 1; i >= 0; i--) for (let j = n - 1; j >= 0; j--) L[i][j] = x[i] === y[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
		const mark = (s, cls) => s.trim() ? `<span class="${cls}">${esc(s)}</span>` : esc(s);
		let i = 0, j = 0, left = '', right = '';
		while (i < m && j < n) {
			if (x[i] === y[j]) { left += esc(x[i]); right += esc(y[j]); i++; j++; }
			else if (L[i + 1][j] >= L[i][j + 1]) { left += mark(x[i], 'del'); i++; }
			else { right += mark(y[j], 'ins'); j++; }
		}
		while (i < m) { left += mark(x[i++], 'del'); }
		while (j < n) { right += mark(y[j++], 'ins'); }
		return [left, right];
	}

	// tracks ({artists, title}) are compared by whole artist names, titles word by word
	// artists that only changed position are underlined
	function trackDiff(a, b) {
		const same = a.artists.length === b.artists.length && a.artists.every(x => b.artists.includes(x));
		const before = a.artists.map(x => b.artists.includes(x) ? esc(x) : `<span class="del">${esc(x)}</span>`).join(', ');
		const after = b.artists.map((x, i) => !a.artists.includes(x) ? `<span class="ins">${esc(x)}</span>`
			: same && a.artists[i] !== x ? `<span class="moved">${esc(x)}</span>` : esc(x)).join(', ');
		const [ta, tb] = diff(a.title, b.title);
		return [`${before} – ${ta}`, `${after} – ${tb}`];
	}

	// toasts

	function toast(msg, opts = {}) {
		const t = document.createElement('div');
		t.className = 'toast' + (opts.kind ? ' ' + opts.kind : '');
		const text = document.createElement('span');
		text.className = 'grow';
		text.textContent = msg;
		t.append(text);
		if (opts.undo) {
			const b = document.createElement('button');
			b.textContent = 'Undo';
			b.onclick = () => { opts.undo(); t.remove(); };
			t.append(b);
		}
		$('lib-toasts').append(t);
		setTimeout(() => t.remove(), opts.time || 6000);
		return t;
	}
	const warn = msg => toast(msg, { kind: 'warn', time: 9000 });

	// api calls: resolve with the json, or show what went wrong and reject

	async function api(endpoint, payload = {}, method = 'POST', opts = {}) {
		let url = '/apis/mlj_1/' + endpoint;
		const init = { method, keepalive: !!opts.keepalive, headers: {} };
		if (method === 'GET') {
			const q = new URLSearchParams(payload).toString();
			if (q) { url += '?' + q; }
		} else {
			init.headers['Content-Type'] = 'application/json';
			init.body = JSON.stringify(payload);
		}
		let res, body;
		try {
			res = await fetch(url, init);
			body = await res.json().catch(() => ({}));
		} catch (e) {
			if (!opts.quiet) { warn("Couldn't reach Maloja. Is it still running?"); }
			throw e;
		}
		if (!res.ok) {
			const msg = (body.error && (body.error.desc || body.error.type)) || `Something went wrong (${res.status})`;
			if (!opts.quiet) { warn(msg); }
			throw new Error(msg);
		}
		return body;
	}

	// changes that can't be undone on the server wait until the toast is gone
	// (or the page is left), so Undo only has to put the row back

	const pending = new Set();
	function later(msg, commit, restore) {
		const job = { commit };
		pending.add(job);
		const run = () => { if (pending.delete(job)) { commit(false).catch(restore); } };
		job.timer = setTimeout(run, 6000);
		toast(msg, { undo: () => { clearTimeout(job.timer); pending.delete(job); restore(); } });
	}
	window.addEventListener('pagehide', () => {
		pending.forEach(job => { clearTimeout(job.timer); job.commit(true).catch(() => {}); });
		pending.clear();
	});

	// menus and help popovers

	document.addEventListener('click', e => {
		const opener = e.target.closest('[data-menu]');
		document.querySelectorAll('.library .menu.open').forEach(m => { if (!opener || m.id !== opener.dataset.menu) m.classList.remove('open'); });
		if (opener) { $(opener.dataset.menu).classList.toggle('open'); }
		document.querySelectorAll('.library .help.open').forEach(h => { if (!h.contains(e.target)) h.classList.remove('open'); });
		const help = e.target.closest('.library .help');
		if (help && !e.target.closest('.tip')) { help.classList.toggle('open'); }
	});

	// drawer (side panel)

	let drawerInput = null;
	function openDrawer(title, body, foot, onInput) {
		$('lib-drawer-title').textContent = title;
		$('lib-drawer-body').innerHTML = body;
		$('lib-drawer-foot').innerHTML = foot || '';
		drawerInput = onInput || null;
		$('lib-drawer').classList.add('open');
		$('lib-scrim').classList.add('open');
		$('lib-drawer-body').scrollTop = 0;
	}
	function closeDrawer() {
		$('lib-drawer').classList.remove('open');
		$('lib-scrim').classList.remove('open');
		drawerInput = null;
	}
	const drawerOpen = () => $('lib-drawer').classList.contains('open');
	document.addEventListener('keydown', e => { if (e.key === 'Escape' && drawerOpen()) closeDrawer(); });
	document.addEventListener('input', e => { if (drawerInput && e.target.closest('#lib-drawer-body')) drawerInput(e); });

	// chip input for lists of artists: Enter or comma adds one, Backspace removes the last
	// the items are in box.dataset.items, a pending name in the text box counts too

	function chipInput(box, items, onChange) {
		items = [...items];
		const draw = focus => {
			box.innerHTML = items.map((it, k) => `<span class="item">${esc(it)}<button type="button" data-k="${k}" title="Remove">×</button></span>`).join('')
				+ '<input type="text" placeholder="Add artist">';
			box.dataset.items = JSON.stringify(items);
			const input = box.querySelector('input');
			input.onkeydown = e => {
				if ((e.key === 'Enter' || e.key === ',') && input.value.trim()) { e.preventDefault(); items.push(input.value.trim()); draw(true); onChange && onChange(); }
				else if (e.key === 'Enter') { e.preventDefault(); }
				else if (e.key === 'Backspace' && !input.value && items.length) { items.pop(); draw(true); onChange && onChange(); }
			};
			box.querySelectorAll('.item button').forEach(b => b.onclick = e => { e.stopPropagation(); items.splice(+b.dataset.k, 1); draw(true); onChange && onChange(); });
			if (focus) { input.focus(); }
		};
		box.onclick = () => box.querySelector('input').focus();
		draw(false);
	}
	function chipValues(box) {
		const pending = box.querySelector('input').value.trim();
		return [...JSON.parse(box.dataset.items || '[]'), ...(pending ? [pending] : [])];
	}

	// page handlers: data-click="name", data-change="name", data-input="name"

	const handlers = { 'lib-close': closeDrawer };
	function actions(map) { Object.assign(handlers, map); }
	for (const [event, attr] of [['click', 'click'], ['change', 'change'], ['input', 'input']]) {
		document.addEventListener(event, e => {
			const el = e.target.closest(`[data-${attr}]`);
			if (!el || !el.closest('.library')) { return; }
			const fn = handlers[el.dataset[attr]];
			if (!fn) { return; }
			if (event === 'click' && (el.tagName === 'A' || el.tagName === 'BUTTON')) { e.preventDefault(); }
			fn(el, e);
		});
	}

	document.addEventListener('DOMContentLoaded', () => {
		const footer = document.getElementById('footer');
		if (footer) { document.documentElement.style.setProperty('--lib-footer', footer.offsetHeight + 'px'); }
	});

	function setBadge(id, n) {
		const b = $(id);
		if (b) { b.textContent = n > 0 ? n : ''; }
	}

	return { $, esc, plural, pills, data, diff, trackDiff, toast, warn, api, later, openDrawer, closeDrawer, drawerOpen, chipInput, chipValues, actions, setBadge };
})();
