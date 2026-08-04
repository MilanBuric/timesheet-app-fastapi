const app = (() => {
  let currentUser = null;
  let allEntries = [];
  let reportPeriod = 'week';
  let reportOffset = 0;
  let currentReport = null;

  // ── Bootstrap ─────────────────────────────────────────────────────────────

  async function bootstrap() {
    if (!api.getToken()) { showLogin(); return; }
    try {
      currentUser = await api.me();
      showApp();
    } catch { showLogin(); }
  }

  function showLogin() {
    document.getElementById('login-screen').classList.remove('hidden');
    document.getElementById('app-screen').classList.add('hidden');
  }

  function showApp() {
    document.getElementById('login-screen').classList.add('hidden');
    document.getElementById('app-screen').classList.remove('hidden');
    document.getElementById('current-username').textContent = currentUser.username;
    document.getElementById('current-role').textContent = currentUser.role;

    document.querySelectorAll('.intern-only').forEach(el =>
      el.classList.toggle('hidden', currentUser.role === 'manager'));
    document.querySelectorAll('.manager-only').forEach(el =>
      el.classList.toggle('hidden', currentUser.role !== 'manager'));

    init();
  }

  async function handleLogin(e) {
    e.preventDefault();
    const username = document.getElementById('login-username').value.trim();
    const password = document.getElementById('login-password').value;
    const errEl = document.getElementById('login-error');
    errEl.textContent = '';
    try {
      const data = await api.login(username, password);
      currentUser = { id: data.user_id, username: data.username, role: data.role };
      showApp();
    } catch (err) { errEl.textContent = err.message; }
  }

  function handleLogout() {
    api.logout();
    currentUser = null;
    showLogin();
  }

  // ── Init ──────────────────────────────────────────────────────────────────

  async function init() {
    document.getElementById('today-label').textContent = new Date().toLocaleDateString('en-GB', {
      weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'
    });
    document.getElementById('entry-date').value = today();
    setupNav();
    document.getElementById('clock-btn').addEventListener('click', toggleClock);
    await Promise.all([loadStats(), loadRecentEntries(), restoreTimer()]);
  }

  function today() { return new Date().toLocaleDateString('en-CA'); }

  // ── Navigation ────────────────────────────────────────────────────────────

  function setupNav() {
    document.querySelectorAll('.nav-item').forEach(link => {
      link.addEventListener('click', e => {
        e.preventDefault();
        const view = link.dataset.view;
        document.querySelectorAll('.nav-item').forEach(l => l.classList.remove('active'));
        link.classList.add('active');
        ['dashboard', 'entries', 'report', 'users'].forEach(v => {
          document.getElementById(`view-${v}`).classList.toggle('hidden', view !== v);
        });
        const titles = { dashboard: 'Dashboard', entries: 'Entries', report: 'Report', users: 'Users' };
        document.getElementById('view-title').textContent = titles[view];
        if (view === 'entries') quickFilter('week');
        if (view === 'report') { reportOffset = 0; renderReport(); }
        if (view === 'users') loadUsers();
      });
    });
  }

  // ── Stats ─────────────────────────────────────────────────────────────────

  async function loadStats() {
    try {
      const stats = await api.getStats();
      document.getElementById('stat-today').textContent = stats.hours_today.toFixed(1) + 'h';
      document.getElementById('stat-week').textContent = stats.hours_week.toFixed(1) + 'h';
      document.getElementById('stat-entries').textContent = stats.total_entries;
    } catch (err) { console.error('Stats error:', err); }
  }

  // ── Entries ───────────────────────────────────────────────────────────────

  async function loadRecentEntries() {
    try {
      const data = await api.getEntries();
      entries.renderTable(data.slice(0, 10), 'recent-entries-container', currentUser.role === 'manager');
    } catch (err) { console.error(err); }
  }

  async function loadEntries() {
    try {
      const params = {
        date_from: document.getElementById('filter-from').value,
        date_to: document.getElementById('filter-to').value,
        category: document.getElementById('filter-category').value,
      };
      allEntries = await api.getEntries(params);
      entries.renderTable(allEntries, 'all-entries-container', currentUser.role === 'manager');
      renderEntriesSummary(allEntries);
    } catch (err) { console.error(err); }
  }

  function renderEntriesSummary(data) {
    const el = document.getElementById('entries-summary');
    if (!data.length) { el.classList.add('hidden'); return; }
    const total = data.reduce((s, e) => s + e.hours, 0);
    const byCategory = data.reduce((acc, e) => { acc[e.category] = (acc[e.category] || 0) + e.hours; return acc; }, {});
    const overtime = data.filter(e => e.overtime).length;
    const catHtml = Object.entries(byCategory).map(([cat, h]) => `<span>${cat}: <strong>${h.toFixed(1)}h</strong></span>`).join('');
    const otHtml = overtime ? `<span style="color:var(--danger)">⚠️ ${overtime} overtime</span>` : '';
    el.innerHTML = `<span>Total: <strong>${total.toFixed(1)}h</strong></span><span style="color:var(--border-strong)">|</span>${catHtml}<span style="color:var(--border-strong)">|</span><span>${data.length} entries</span>${otHtml ? `<span style="color:var(--border-strong)">|</span>${otHtml}` : ''}`;
    el.classList.remove('hidden');
  }

  async function addEntry() {
    const date = document.getElementById('entry-date').value;
    const hours = parseFloat(document.getElementById('entry-hours').value);
    const category = document.getElementById('entry-category').value;
    const activity = document.getElementById('entry-activity').value.trim();
    if (!date || !hours || !activity) { alert('Please fill in all fields.'); return; }
    if (hours > 8 && !confirm(`⚠️ You're logging ${hours}h which exceeds 8h. Continue?`)) return;
    try {
      await api.createEntry({ date, hours, category, activity, force: false });
      document.getElementById('entry-hours').value = '';
      document.getElementById('entry-activity').value = '';
      await Promise.all([loadStats(), loadRecentEntries()]);
    } catch (err) {
      if (err.status === 409) {
        if (confirm(`⚠️ ${err.message}\n\nLog it anyway?`)) {
          await api.createEntry({ date, hours, category, activity, force: true });
          document.getElementById('entry-hours').value = '';
          document.getElementById('entry-activity').value = '';
          await Promise.all([loadStats(), loadRecentEntries()]);
        }
      } else { alert('Failed to save entry.'); }
    }
  }

  async function deleteEntry(id) {
    if (!confirm('Delete this entry?')) return;
    try {
      await api.deleteEntry(id);
      await Promise.all([loadStats(), loadRecentEntries(), loadEntries()]);
    } catch { alert('Failed to delete entry.'); }
  }

  async function approveEntry(id) {
    try {
      await api.approveEntry(id);
      await Promise.all([loadRecentEntries(), loadEntries()]);
    } catch { alert('Failed to approve.'); }
  }

  async function rejectEntry(id) {
    try {
      await api.rejectEntry(id);
      await Promise.all([loadRecentEntries(), loadEntries()]);
    } catch { alert('Failed to reject.'); }
  }

  // ── Quick filters ─────────────────────────────────────────────────────────

  function quickFilter(preset) {
    document.querySelectorAll('.btn-quick').forEach(b => b.classList.remove('active'));
    const btn = document.querySelector(`.btn-quick[onclick*="'${preset}'"]`);
    if (btn) btn.classList.add('active');
    const d = new Date();
    let from = '', to = '';
    if (preset === 'today') {
      from = to = today();
    } else if (preset === 'week') {
      const day = d.getDay();
      const start = new Date(d); start.setDate(d.getDate() - (day === 0 ? 6 : day - 1));
      const end = new Date(start); end.setDate(start.getDate() + 6);
      from = start.toISOString().split('T')[0];
      to = end.toISOString().split('T')[0];
    } else if (preset === 'month') {
      from = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-01`;
      to = new Date(d.getFullYear(), d.getMonth() + 1, 0).toISOString().split('T')[0];
    }
    document.getElementById('filter-from').value = from;
    document.getElementById('filter-to').value = to;
    loadEntries();
  }

  // ── Edit modal ────────────────────────────────────────────────────────────

  function openEdit(entry) {
    document.getElementById('edit-id').value = entry.id;
    document.getElementById('edit-date').value = entry.date;
    document.getElementById('edit-hours').value = entry.hours;
    document.getElementById('edit-category').value = entry.category;
    document.getElementById('edit-activity').value = entry.activity;
    document.getElementById('edit-modal').classList.remove('hidden');
  }

  function closeModal() {
    document.getElementById('edit-modal').classList.add('hidden');
  }

  async function saveEdit() {
    const id = document.getElementById('edit-id').value;
    const data = {
      date: document.getElementById('edit-date').value,
      hours: parseFloat(document.getElementById('edit-hours').value),
      category: document.getElementById('edit-category').value,
      activity: document.getElementById('edit-activity').value.trim(),
    };
    if (!data.date || !data.hours || !data.activity) { alert('Please fill in all fields.'); return; }
    try {
      await api.updateEntry(id, data);
      closeModal();
      await Promise.all([loadStats(), loadRecentEntries(), loadEntries()]);
    } catch { alert('Failed to update entry.'); }
  }

  // ── Clock ─────────────────────────────────────────────────────────────────

  async function toggleClock() {
    if (timer.isRunning()) {
      try {
        const result = await api.clockOut();
        timer.stop();
        document.getElementById('entry-hours').value = result.hours.toFixed(1);
        document.getElementById('entry-date').value = today();
        document.getElementById('entry-activity').focus();
      } catch { alert('Failed to clock out.'); }
    } else {
      try {
        const session = await api.clockIn();
        timer.start(session.clocked_in_at);
      } catch { alert('Failed to clock in.'); }
    }
  }

  async function restoreTimer() {
    try {
      const session = await api.getActiveSession();
      if (session) timer.start(session.clocked_in_at);
    } catch { console.error('Timer restore error'); }
  }

  // ── Export CSV ────────────────────────────────────────────────────────────

  async function exportCSV() {
    try {
      const params = {
        date_from: document.getElementById('filter-from').value,
        date_to: document.getElementById('filter-to').value,
        category: document.getElementById('filter-category').value,
      };
      const data = await api.getEntries(params);
      entries.exportCSV(data);
    } catch { alert('Failed to export.'); }
  }

  // ── Report ────────────────────────────────────────────────────────────────

  function setReportPeriod(period) {
    reportPeriod = period; reportOffset = 0;
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.period === period));
    renderReport();
  }

  function shiftPeriod(dir) { reportOffset += dir; renderReport(); }

  function getReportRange() {
    const d = new Date();
    if (reportPeriod === 'week') {
      const day = d.getDay();
      const start = new Date(d); start.setDate(d.getDate() - (day === 0 ? 6 : day - 1) + reportOffset * 7);
      const end = new Date(start); end.setDate(start.getDate() + 6);
      return { from: start, to: end };
    } else {
      const start = new Date(d.getFullYear(), d.getMonth() + reportOffset, 1);
      const end = new Date(start.getFullYear(), start.getMonth() + 1, 0);
      return { from: start, to: end };
    }
  }

  function formatLabel(from, to) {
    if (reportPeriod === 'week') {
      const o = { day: 'numeric', month: 'short' };
      return `${from.toLocaleDateString('en-GB', o)} – ${to.toLocaleDateString('en-GB', o)}, ${to.getFullYear()}`;
    }
    return from.toLocaleDateString('en-GB', { month: 'long', year: 'numeric' });
  }

  function fmt(d) { return d.toLocaleDateString('en-CA'); }
  function fmtDate(d) {
    const [y, m, day] = d.split('-');
    return new Date(y, m-1, day).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
  }
  function fmtCurrency(n) { return '€' + n.toFixed(2); }

  async function renderReport() {
    const { from, to } = getReportRange();
    document.getElementById('report-period-label').textContent = formatLabel(from, to);

    try {
      currentReport = await api.getWeeklyReport(fmt(from), fmt(to));
    } catch { return; }

    const r = currentReport;

    // Summary stats
    document.getElementById('report-summary').innerHTML = `
      <div class="report-stat"><div class="report-stat-label">Total hours</div><div class="report-stat-value">${r.total_hours.toFixed(1)}h</div></div>
      <div class="report-stat"><div class="report-stat-label">Self-study</div><div class="report-stat-value">${(r.category_totals['Self-study'] || 0).toFixed(1)}h</div></div>
      <div class="report-stat"><div class="report-stat-label">Meetings</div><div class="report-stat-value">${(r.category_totals['Meeting'] || 0).toFixed(1)}h</div></div>
      <div class="report-stat pay-stat"><div class="report-stat-label">Est. pay ${r.hourly_rate > 0 ? '@ €' + r.hourly_rate + '/h' : r.hourly_rate === -1 ? '(mixed rates)' : '(no rate set)'}</div><div class="report-stat-value pay-value">${r.total_pay > 0 ? fmtCurrency(r.total_pay) : '—'}</div></div>
    `;

    // Category breakdown table
    if (r.days.length) {
      document.getElementById('report-category-table').innerHTML = `
        <div class="cat-table-wrap">
          <table class="cat-table">
            <thead>
              <tr>
                <th>Day</th>
                <th>Self-study</th>
                <th>Meeting</th>
                <th>Other</th>
                <th>Total</th>
                ${r.total_pay > 0 ? '<th>Pay</th>' : ''}
              </tr>
            </thead>
            <tbody>
              ${r.days.map(d => `
                <tr class="${d.any_overtime ? 'row-overtime' : ''}">
                  <td class="td-date">${fmtDate(d.date)}</td>
                  <td>${d.self_study > 0 ? d.self_study.toFixed(1) + 'h' : '—'}</td>
                  <td>${d.meeting > 0 ? d.meeting.toFixed(1) + 'h' : '—'}</td>
                  <td>${d.other > 0 ? d.other.toFixed(1) + 'h' : '—'}</td>
                  <td><strong>${d.total.toFixed(1)}h ${d.any_overtime ? '⚠️' : ''}</strong></td>
                  ${r.total_pay > 0 ? `<td>${fmtCurrency(d.approved_pay)}</td>` : ''}
                </tr>
                ${d.user_breakdown && d.user_breakdown.length > 0 ? d.user_breakdown.map(u => `
                  <tr style="font-size:12px;color:var(--text-secondary);">
                    <td class="td-date" style="padding-left:16px;">↳ ${u.username} ${u.overtime ? '⚠️' : ''}</td>
                    <td>${u.self_study > 0 ? u.self_study.toFixed(1) + 'h' : '—'}</td>
                    <td>${u.meeting > 0 ? u.meeting.toFixed(1) + 'h' : '—'}</td>
                    <td>${u.other > 0 ? u.other.toFixed(1) + 'h' : '—'}</td>
                    <td>${u.total.toFixed(1)}h</td>
                    ${r.total_pay > 0 ? `<td>${fmtCurrency(u.approved_pay)}</td>` : ''}
                  </tr>`).join('') : ''}
              `).join('')}
            </tbody>
            <tfoot>
              <tr class="totals-row">
                <td><strong>Total</strong></td>
                <td><strong>${(r.category_totals['Self-study'] || 0).toFixed(1)}h</strong></td>
                <td><strong>${(r.category_totals['Meeting'] || 0).toFixed(1)}h</strong></td>
                <td><strong>${(r.category_totals['Other'] || 0).toFixed(1)}h</strong></td>
                <td><strong>${r.total_hours.toFixed(1)}h</strong></td>
                ${r.total_pay > 0 ? `<td><strong>${fmtCurrency(r.total_pay)}</strong></td>` : ''}
              </tr>
            </tfoot>
          </table>
        </div>`;
    } else {
      document.getElementById('report-category-table').innerHTML = '';
    }

    // Detailed entries grouped by day
    if (!r.entries.length) {
      document.getElementById('report-container').innerHTML = '<div class="report-empty">No entries for this period.</div>';
      return;
    }

    const grouped = r.entries.reduce((acc, e) => { (acc[e.date] = acc[e.date] || []).push(e); return acc; }, {});
    document.getElementById('report-container').innerHTML = `
      <h3 class="section-label">Entry detail</h3>
      ${Object.keys(grouped).sort((a,b) => b.localeCompare(a)).map(date => {
        const dayEntries = grouped[date];
        const dayTotal = dayEntries.reduce((s, e) => s + e.hours, 0);
        // Overtime only if any single user exceeds 8h, not combined total
        const userTotals = dayEntries.reduce((acc, e) => { acc[e.user_id] = (acc[e.user_id] || 0) + e.hours; return acc; }, {});
        const dayOvertime = Object.values(userTotals).some(h => h > 8);
        const label = new Date(date + 'T12:00:00').toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'short' });
        return `
          <div class="report-group">
            <div class="report-group-header ${dayOvertime ? 'overtime-header' : ''}">
              <span class="report-group-title">${label} ${dayOvertime ? '⚠️' : ''}</span>
              <span class="report-group-total">${dayTotal.toFixed(1)}h</span>
            </div>
            <table style="width:100%;border-collapse:collapse;font-size:14px;">
              <tbody>
                ${dayEntries.map(e => `
                  <tr>
                    <td style="padding:8px 0;">${e.activity}</td>
                    <td><span class="badge badge-${e.category === 'Self-study' ? 'study' : e.category === 'Meeting' ? 'meeting' : 'other'}">${e.category}</span></td>
                    <td><span class="badge badge-${e.status}">${e.status}</span></td>
                    <td style="font-weight:500;white-space:nowrap;">${e.hours}h</td>
                  </tr>`).join('')}
              </tbody>
            </table>
          </div>`;
      }).join('')}`;
  }

  // ── PDF / Print ───────────────────────────────────────────────────────────

  function printReport() {
    if (!currentReport) { alert('Load a report first.'); return; }
    window.print();
  }

  // ── Users (manager) ───────────────────────────────────────────────────────

  async function loadUsers() {
    try {
      const users = await api.getUsers();
      const container = document.getElementById('users-container');

      const interns = users.filter(u => u.role === 'intern');
      const managers = users.filter(u => u.role === 'manager');

      function userRow(u) {
        const isManager = u.role === 'manager';
        return `<tr>
          <td style="padding:12px 0;border-bottom:1px solid var(--border);">
            ${u.username}
            <span style="font-size:11px;color:var(--text-muted);margin-left:6px;text-transform:uppercase;">${u.role}</span>
          </td>
          <td style="padding:12px 0;border-bottom:1px solid var(--border);">
            ${!isManager ? `<div style="display:flex;align-items:center;gap:8px;">
              <span style="color:var(--text-muted);">€</span>
              <input type="number" id="rate-${u.id}" value="${u.hourly_rate}" min="0" step="0.5"
                style="width:90px;height:32px;padding:0 8px;border:1px solid var(--border);border-radius:var(--radius);font-size:14px;" />
              <span style="font-size:13px;color:var(--text-muted)">/h</span>
            </div>` : '<span style="color:var(--text-muted);font-size:13px;">—</span>'}
          </td>
          <td style="padding:12px 0;border-bottom:1px solid var(--border);text-align:right;">
            <div style="display:flex;gap:6px;justify-content:flex-end;">
              ${!isManager ? `<button class="btn" onclick="app.saveRate(${u.id})">Save rate</button>` : ''}
              <button class="btn-icon danger" onclick="app.deleteUser(${u.id}, '${u.username}')" title="Delete user">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
              </button>
            </div>
          </td>
        </tr>`;
      }

      container.innerHTML = `
        <div class="create-user-form">
          <h3 style="font-size:14px;font-weight:500;margin-bottom:12px;">Add new user</h3>
          <div style="display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:10px;align-items:end;">
            <div class="form-group">
              <label for="new-username">Username</label>
              <input type="text" id="new-username" placeholder="e.g. john" />
            </div>
            <div class="form-group">
              <label for="new-password">Password</label>
              <input type="password" id="new-password" placeholder="min. 6 characters" />
            </div>
            <div class="form-group">
              <label for="new-role">Role</label>
              <select id="new-role">
                <option value="intern">Intern</option>
                <option value="manager">Manager</option>
              </select>
            </div>
            <button class="btn btn-primary" onclick="app.createUser()" style="height:38px;">Create</button>
          </div>
          <p id="create-user-error" style="font-size:13px;color:var(--danger);margin-top:8px;min-height:18px;"></p>
        </div>

        <table style="width:100%;border-collapse:collapse;font-size:14px;margin-top:1rem;">
          <thead>
            <tr>
              <th style="text-align:left;padding:8px 0;font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.07em;border-bottom:1px solid var(--border);">User</th>
              <th style="text-align:left;padding:8px 0;font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.07em;border-bottom:1px solid var(--border);">Hourly rate</th>
              <th style="border-bottom:1px solid var(--border);"></th>
            </tr>
          </thead>
          <tbody>
            ${[...managers, ...interns].map(u => userRow(u)).join('')}
          </tbody>
        </table>`;
    } catch { alert('Failed to load users.'); }
  }

  async function createUser() {
    const username = document.getElementById('new-username').value.trim();
    const password = document.getElementById('new-password').value;
    const role = document.getElementById('new-role').value;
    const errEl = document.getElementById('create-user-error');
    errEl.textContent = '';
    if (!username || !password) { errEl.textContent = 'Please fill in all fields.'; return; }
    if (password.length < 6) { errEl.textContent = 'Password must be at least 6 characters.'; return; }
    try {
      await api.createUser({ username, password, role, hourly_rate: 0 });
      document.getElementById('new-username').value = '';
      document.getElementById('new-password').value = '';
      await loadUsers();
    } catch (err) { errEl.textContent = err.message; }
  }

  async function deleteUser(userId, username) {
    if (!confirm(`Delete user "${username}"? This will also delete all their entries and clock sessions.`)) return;
    try {
      await api.deleteUser(userId);
      await loadUsers();
    } catch (err) { alert(err.message); }
  }

  async function saveRate(userId) {
    const input = document.getElementById(`rate-${userId}`);
    const rate = parseFloat(input.value);
    if (isNaN(rate) || rate < 0) { alert('Please enter a valid rate.'); return; }
    try {
      await api.setHourlyRate(userId, rate);
      input.style.borderColor = 'var(--success)';
      setTimeout(() => input.style.borderColor = '', 1500);
    } catch { alert('Failed to save rate.'); }
  }

  // ── Theme ─────────────────────────────────────────────────────────────────

  function initTheme() {
    const saved = localStorage.getItem('ts_theme') || 'light';
    applyTheme(saved);
    document.getElementById('theme-toggle').addEventListener('click', () => {
      const current = document.documentElement.getAttribute('data-theme') || 'light';
      applyTheme(current === 'dark' ? 'light' : 'dark');
    });
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('ts_theme', theme);
    document.getElementById('theme-icon-sun').classList.toggle('hidden', theme === 'dark');
    document.getElementById('theme-icon-moon').classList.toggle('hidden', theme === 'light');
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('login-form').addEventListener('submit', handleLogin);
    document.getElementById('logout-btn').addEventListener('click', handleLogout);
    initTheme();
    bootstrap();
  });

  return {
    addEntry, deleteEntry, approveEntry, rejectEntry,
    openEdit, closeModal, saveEdit,
    loadEntries, exportCSV, quickFilter,
    setReportPeriod, shiftPeriod, printReport,
    loadUsers, createUser, deleteUser, saveRate
  };
})();
