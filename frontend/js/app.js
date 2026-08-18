const app = (() => {
  let currentUser = null;
  let allEntries = [];
  let entriesPage = 1;
  const ENTRIES_PAGE_SIZE = 20;
  let reportPeriod = 'week';
  let reportOffset = 0;
  let currentReport = null;
  let calendarYear, calendarMonth;
  let selectedCalendarDate = null;
  let monthMeetings = [];
  let roomsList = [];
  let teamsList = [];
  let meetingSearchDebounce = null;
  let meetingsPollTimer = null;
  let meetingsViewActive = false;
  const MEETINGS_POLL_MS = 20000;

  // ── Bootstrap ─────────────────────────────────────────────────────────────

  async function bootstrap() {
    const params = new URLSearchParams(window.location.search);
    if (params.get('reset_token')) { showResetPassword(); return; }
    if (!api.getToken()) { showLogin(); return; }
    try {
      currentUser = await api.me();
      showApp();
    } catch { showLogin(); }
  }

  function showLogin() {
    document.getElementById('login-screen').classList.remove('hidden');
    document.getElementById('app-screen').classList.add('hidden');
    // showLogin() is reachable from several places (logout, session expiry,
    // the tail end of the reset-password flow) — always fully close the
    // other auth sub-screens too, so one never keeps showing stacked
    // underneath/alongside the plain login form.
    document.getElementById('forgot-password-screen').classList.add('hidden');
    document.getElementById('reset-password-screen').classList.add('hidden');
  }

  // Resets everything that must never carry over from one logged-in session
  // to the next on the same browser tab: which view is showing, any open
  // modal, and every module-level cache of a previous user's data. Without
  // this, logging out (or logging in as someone else right after) left
  // whatever view was last open on screen — including its stale, already-
  // rendered HTML from the PREVIOUS user's role and data — visible until a
  // full page reload happened to reset it.
  function resetToCleanState() {
    stopMeetingsPolling();
    meetingsViewActive = false;

    document.querySelectorAll('.nav-item').forEach(l => l.classList.remove('active'));
    const dashboardNav = document.querySelector('.nav-item[data-view="dashboard"]');
    if (dashboardNav) dashboardNav.classList.add('active');
    ['dashboard', 'entries', 'report', 'meetings', 'users'].forEach(v => {
      const el = document.getElementById(`view-${v}`);
      if (el) el.classList.toggle('hidden', v !== 'dashboard');
    });
    const titleEl = document.getElementById('view-title');
    if (titleEl) titleEl.textContent = 'Dashboard';

    ['edit-modal', 'reject-modal', 'reschedule-modal'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.classList.add('hidden');
    });

    // Drop every cached render/fetch result from the previous session —
    // the next login re-fetches everything fresh and role-scoped.
    allEntries = [];
    entriesPage = 1;
    reportPeriod = 'week';
    reportOffset = 0;
    currentReport = null;
    calendarYear = undefined;
    calendarMonth = undefined;
    selectedCalendarDate = null;
    monthMeetings = [];
    roomsList = [];
    teamsList = [];
  }

  function showApp() {
    resetToCleanState();
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
      await api.login(username, password);
      currentUser = await api.me();
      showApp();
    } catch (err) { errEl.textContent = err.message; }
  }

  function handleLogout() {
    resetToCleanState();
    api.logout();
    currentUser = null;
    showLogin();
  }

  // ── Forgot / reset password ──────────────────────────────────────────────

  function showForgotPassword() {
    document.getElementById('login-screen').classList.add('hidden');
    document.getElementById('reset-password-screen').classList.add('hidden');
    document.getElementById('forgot-password-screen').classList.remove('hidden');
    document.getElementById('forgot-password-message').textContent = '';
    document.getElementById('forgot-username').value = '';
  }

  function showLoginFromForgot() {
    document.getElementById('forgot-password-screen').classList.add('hidden');
    document.getElementById('reset-password-screen').classList.add('hidden');
    // Drop any reset_token from the URL so refreshing doesn't reopen this screen
    if (window.location.search.includes('reset_token')) {
      window.history.replaceState({}, '', window.location.pathname);
    }
    showLogin();
  }

  function showResetPassword() {
    document.getElementById('login-screen').classList.add('hidden');
    document.getElementById('forgot-password-screen').classList.add('hidden');
    document.getElementById('reset-password-screen').classList.remove('hidden');
    document.getElementById('reset-password-message').textContent = '';
    document.getElementById('reset-new-password').value = '';
  }

  async function submitForgotPassword() {
    const username = document.getElementById('forgot-username').value.trim();
    const msgEl = document.getElementById('forgot-password-message');
    if (!username) { msgEl.textContent = 'Please enter your username.'; return; }
    try {
      const result = await api.forgotPassword(username);
      msgEl.style.color = 'var(--success)';
      msgEl.textContent = result.message;
    } catch (err) {
      msgEl.style.color = 'var(--danger)';
      msgEl.textContent = err.message || 'Something went wrong.';
    }
  }

  async function submitResetPassword() {
    const params = new URLSearchParams(window.location.search);
    const token = params.get('reset_token');
    const newPassword = document.getElementById('reset-new-password').value;
    const msgEl = document.getElementById('reset-password-message');
    if (!newPassword || newPassword.length < 6) {
      msgEl.style.color = 'var(--danger)';
      msgEl.textContent = 'Password must be at least 6 characters.';
      return;
    }
    try {
      await api.resetPassword(token, newPassword);
      window.history.replaceState({}, '', window.location.pathname);
      msgEl.style.color = 'var(--success)';
      msgEl.textContent = 'Password updated! Redirecting to sign in…';
      setTimeout(showLogin, 1200);
    } catch (err) {
      msgEl.style.color = 'var(--danger)';
      msgEl.textContent = err.message || 'Failed to reset password.';
    }
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
        ['dashboard', 'entries', 'report', 'meetings', 'users'].forEach(v => {
          document.getElementById(`view-${v}`).classList.toggle('hidden', view !== v);
        });
        const titles = { dashboard: 'Dashboard', entries: 'Entries', report: 'Report', meetings: 'Meetings', users: 'Users' };
        document.getElementById('view-title').textContent = titles[view];
        if (view === 'entries') quickFilter('week');
        if (view === 'report') { reportOffset = 0; renderReport(); }
        meetingsViewActive = (view === 'meetings');
        if (view === 'meetings') { loadMeetingsView(); startMeetingsPolling(); }
        else { stopMeetingsPolling(); }
        if (view === 'users') { loadUsers(); loadTeams(); loadRooms(); loadClockSessions(); }
        closeSidebar(); // on mobile the drawer should close after picking a destination
      });
    });
    // A colleague's RSVP (from the emailed link, or another session) doesn't
    // reach this tab on its own — periodic polling plus an immediate refresh
    // when the tab regains focus is what actually keeps "Pending" from
    // sitting stale until a manual reload.
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && meetingsViewActive) refreshCalendarResilient();
    });

    // Mobile sidebar drawer — the hamburger button and its own overlay only
    // do anything visually once the <=768px CSS breakpoint applies, but the
    // listeners are harmless to attach unconditionally either way.
    document.getElementById('sidebar-toggle').addEventListener('click', toggleSidebar);
    document.getElementById('sidebar-overlay').addEventListener('click', closeSidebar);
  }

  function toggleSidebar() {
    document.querySelector('.sidebar').classList.toggle('mobile-open');
    document.getElementById('sidebar-overlay').classList.toggle('hidden');
  }

  function closeSidebar() {
    document.querySelector('.sidebar').classList.remove('mobile-open');
    document.getElementById('sidebar-overlay').classList.add('hidden');
  }

  function startMeetingsPolling() {
    stopMeetingsPolling();
    meetingsPollTimer = setInterval(() => {
      if (!document.hidden) refreshCalendarResilient();
    }, MEETINGS_POLL_MS);
  }

  function stopMeetingsPolling() {
    if (meetingsPollTimer) { clearInterval(meetingsPollTimer); meetingsPollTimer = null; }
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
    entriesPage = 1;
    await fetchAndRenderEntries();
  }

  async function goToEntriesPage(page) {
    entriesPage = page;
    await fetchAndRenderEntries();
  }

  async function fetchAndRenderEntries() {
    try {
      const filters = {
        date_from: document.getElementById('filter-from').value,
        date_to: document.getElementById('filter-to').value,
        category: document.getElementById('filter-category').value,
      };
      // The table is paginated (so a large history never dumps hundreds of
      // rows into the DOM at once), but the summary line above it always
      // reflects the FULL filtered set, not just the current page — so
      // "Total: 84h" stays true regardless of which page you're looking at.
      const [page, full] = await Promise.all([
        api.getEntriesPaginated({ ...filters, limit: ENTRIES_PAGE_SIZE, offset: (entriesPage - 1) * ENTRIES_PAGE_SIZE }),
        api.getEntries(filters)
      ]);
      allEntries = full;
      entries.renderTable(page.data, 'all-entries-container', currentUser.role === 'manager');
      renderEntriesSummary(full);
      renderEntriesPagination(page.total);
    } catch (err) { console.error(err); }
  }

  function renderEntriesPagination(total) {
    const el = document.getElementById('entries-pagination');
    if (!el) return;
    const totalPages = Math.max(1, Math.ceil(total / ENTRIES_PAGE_SIZE));
    if (totalPages <= 1) { el.innerHTML = ''; return; }
    const start = (entriesPage - 1) * ENTRIES_PAGE_SIZE + 1;
    const end = Math.min(entriesPage * ENTRIES_PAGE_SIZE, total);
    el.innerHTML = `
      <span class="pagination-info">${start}–${end} of ${total}</span>
      <div class="pagination-controls">
        <button class="btn" ${entriesPage <= 1 ? 'disabled' : ''} onclick="app.goToEntriesPage(${entriesPage - 1})">‹ Prev</button>
        <span class="pagination-page">Page ${entriesPage} of ${totalPages}</span>
        <button class="btn" ${entriesPage >= totalPages ? 'disabled' : ''} onclick="app.goToEntriesPage(${entriesPage + 1})">Next ›</button>
      </div>`;
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
    } catch (err) {
      if (err.status === 409) {
        if (confirm(`⚠️ ${err.message}\n\nLog it anyway?`)) {
          try {
            await api.createEntry({ date, hours, category, activity, force: true });
          } catch { alert('Failed to save entry.'); return; }
        } else { return; }
      } else { alert('Failed to save entry.'); return; }
    }
    document.getElementById('entry-hours').value = '';
    document.getElementById('entry-activity').value = '';
    await refreshEntriesResilient();
  }

  async function deleteEntry(id) {
    if (!confirm('Delete this entry?')) return;
    try {
      await api.deleteEntry(id);
    } catch { alert('Failed to delete entry.'); return; }
    await refreshEntriesResilient(true);
  }

  async function approveEntry(id) {
    try {
      await api.approveEntry(id);
    } catch { alert('Failed to approve.'); return; }
    await refreshEntriesResilient(true);
  }

  function openRejectModal(id) {
    document.getElementById('reject-entry-id').value = id;
    document.getElementById('reject-reason').value = '';
    document.getElementById('reject-modal').classList.remove('hidden');
  }

  function closeRejectModal() {
    document.getElementById('reject-modal').classList.add('hidden');
  }

  async function confirmReject() {
    const id = document.getElementById('reject-entry-id').value;
    const reason = document.getElementById('reject-reason').value.trim();
    if (!reason) {
      alert('Please provide a reason for rejecting this entry.');
      return;
    }
    try {
      await api.rejectEntry(id, reason);
    } catch { alert('Failed to reject entry.'); return; }
    closeRejectModal();
    await refreshEntriesResilient(true);
  }

  // Wraps the post-action refresh with one quiet retry, same pattern already
  // proven for the Meetings tab — a refresh hiccup must never be reported
  // as if the action itself (add/delete/approve/reject) had failed, since
  // by the time this runs the action has already succeeded server-side.
  async function refreshEntriesResilient(includeEntriesList = false) {
    const doRefresh = () => includeEntriesList
      ? Promise.all([loadStats(), loadRecentEntries(), loadEntries()])
      : Promise.all([loadStats(), loadRecentEntries()]);
    try {
      await doRefresh();
    } catch {
      await new Promise(r => setTimeout(r, 600));
      try {
        await doRefresh();
      } catch {
        console.warn('Could not refresh entries automatically — it will catch up on the next action.');
      }
    }
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
        document.getElementById('entry-hours').value = result.hours.toFixed(2);
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
      <div class="report-stat"><div class="report-stat-label">Hours worked (clocked)</div><div class="report-stat-value">${r.total_hours.toFixed(2)}h</div></div>
      <div class="report-stat"><div class="report-stat-label">Self-study (logged)</div><div class="report-stat-value">${(r.category_totals['Self-study'] || 0).toFixed(1)}h</div></div>
      <div class="report-stat"><div class="report-stat-label">Meetings (logged)</div><div class="report-stat-value">${(r.category_totals['Meeting'] || 0).toFixed(1)}h</div></div>
      <div class="report-stat pay-stat"><div class="report-stat-label">Pay ${r.hourly_rate > 0 ? '@ €' + r.hourly_rate + '/h' : r.hourly_rate === -1 ? '(mixed rates)' : '(no rate set)'}</div><div class="report-stat-value pay-value">${r.total_pay > 0 ? fmtCurrency(r.total_pay) : '—'}</div></div>
    `;

    // Category breakdown table
    if (r.days.length) {
      document.getElementById('report-category-table').innerHTML = `
        <p class="card-subtitle" style="margin-bottom:10px;">
          Self-study/Meeting/Other are task-log estimates for tracking — they may not add up to "Clocked",
          which is actual time worked and the only thing pay is based on.
        </p>
        <div class="cat-table-wrap">
          <table class="cat-table">
            <thead>
              <tr>
                <th>Day</th>
                <th>Self-study</th>
                <th>Meeting</th>
                <th>Other</th>
                <th>Clocked</th>
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
                  <td><strong>${d.total.toFixed(2)}h ${d.any_overtime ? '⚠️' : ''}</strong></td>
                  ${r.total_pay > 0 ? `<td>${fmtCurrency(d.approved_pay)}</td>` : ''}
                </tr>
                ${d.user_breakdown && d.user_breakdown.length > 0 ? d.user_breakdown.map(u => `
                  <tr style="font-size:12px;color:var(--text-secondary);">
                    <td class="td-date" style="padding-left:16px;">↳ ${u.username} ${u.overtime ? '⚠️' : ''}</td>
                    <td>${u.self_study > 0 ? u.self_study.toFixed(1) + 'h' : '—'}</td>
                    <td>${u.meeting > 0 ? u.meeting.toFixed(1) + 'h' : '—'}</td>
                    <td>${u.other > 0 ? u.other.toFixed(1) + 'h' : '—'}</td>
                    <td>${u.total.toFixed(2)}h</td>
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
                <td><strong>${r.total_hours.toFixed(2)}h</strong></td>
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
      const [users, teams] = await Promise.all([api.getUsers(), api.getTeams()]);
      teamsList = teams;
      const container = document.getElementById('users-container');

      const interns = users.filter(u => u.role === 'intern');
      const managers = users.filter(u => u.role === 'manager');

      function teamOptions(selectedId) {
        return `<option value="">No team</option>` + teams.map(t =>
          `<option value="${t.id}"${selectedId === t.id ? ' selected' : ''}>${escapeAttr(t.name)}</option>`
        ).join('');
      }

      function userRow(u) {
        const isManager = u.role === 'manager';
        return `<tr>
          <td style="padding:12px 8px 12px 0;border-bottom:1px solid var(--border);">
            ${escapeAttr(u.username)}
            <span style="font-size:11px;color:var(--text-muted);margin-left:6px;text-transform:uppercase;">${u.role}</span>
          </td>
          <td style="padding:12px 8px 12px 0;border-bottom:1px solid var(--border);">
            <input type="text" id="title-${u.id}" value="${u.title ? escapeAttr(u.title) : ''}" placeholder="e.g. Backend Engineer"
              style="width:100%;height:32px;padding:0 8px;border:1px solid var(--border);border-radius:var(--radius);font-size:13px;background:var(--surface);color:inherit;" />
          </td>
          <td style="padding:12px 8px 12px 0;border-bottom:1px solid var(--border);">
            <select id="team-${u.id}" style="height:32px;border:1px solid var(--border);border-radius:var(--radius);background:var(--surface);color:inherit;font-size:13px;">
              ${teamOptions(u.team_id)}
            </select>
          </td>
          <td style="padding:12px 8px 12px 0;border-bottom:1px solid var(--border);">
            ${!isManager ? `<div style="display:flex;align-items:center;gap:8px;">
              <span style="color:var(--text-muted);">€</span>
              <input type="number" id="rate-${u.id}" value="${u.hourly_rate}" min="0" step="0.5"
                style="width:80px;height:32px;padding:0 8px;border:1px solid var(--border);border-radius:var(--radius);font-size:14px;" />
              <span style="font-size:13px;color:var(--text-muted)">/h</span>
            </div>` : '<span style="color:var(--text-muted);font-size:13px;">—</span>'}
          </td>
          <td style="padding:12px 0;border-bottom:1px solid var(--border);text-align:right;">
            <div style="display:flex;gap:6px;justify-content:flex-end;">
              <button class="btn" onclick="app.saveUserRow(${u.id}, ${!isManager})">Save</button>
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
          <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:10px;align-items:end;margin-bottom:10px;">
            <div class="form-group">
              <label for="new-username">Username</label>
              <input type="text" id="new-username" placeholder="e.g. john" />
            </div>
            <div class="form-group">
              <label for="new-password">Password</label>
              <input type="password" id="new-password" placeholder="min. 6 characters" autocomplete="new-password" />
            </div>
            <div class="form-group">
              <label for="new-password-confirm">Confirm password</label>
              <input type="password" id="new-password-confirm" placeholder="Re-enter password" autocomplete="new-password" />
            </div>
            <div class="form-group">
              <label for="new-role">Role</label>
              <select id="new-role">
                <option value="intern">Intern</option>
                <option value="manager">Manager</option>
              </select>
            </div>
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr auto;gap:10px;align-items:end;">
            <div class="form-group">
              <label for="new-title">Title (optional)</label>
              <input type="text" id="new-title" placeholder="e.g. Backend Engineer" />
            </div>
            <div class="form-group">
              <label for="new-team">Team (optional)</label>
              <select id="new-team">${teamOptions(null)}</select>
            </div>
            <button class="btn btn-primary" onclick="app.createUser()" style="height:38px;">Create</button>
          </div>
          <p id="create-user-error" style="font-size:13px;color:var(--danger);margin-top:8px;min-height:18px;"></p>
        </div>

        <table style="width:100%;border-collapse:collapse;font-size:14px;margin-top:1rem;">
          <thead>
            <tr>
              <th style="text-align:left;padding:8px 8px 8px 0;font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.07em;border-bottom:1px solid var(--border);">User</th>
              <th style="text-align:left;padding:8px 8px 8px 0;font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.07em;border-bottom:1px solid var(--border);">Title</th>
              <th style="text-align:left;padding:8px 8px 8px 0;font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.07em;border-bottom:1px solid var(--border);">Team</th>
              <th style="text-align:left;padding:8px 8px 8px 0;font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.07em;border-bottom:1px solid var(--border);">Hourly rate</th>
              <th style="border-bottom:1px solid var(--border);"></th>
            </tr>
          </thead>
          <tbody>
            ${[...managers, ...interns].map(u => userRow(u)).join('')}
          </tbody>
        </table>`;
    } catch { alert('Failed to load users.'); }
  }

  async function saveUserRow(userId, hasRate) {
    const title = document.getElementById(`title-${userId}`).value.trim();
    const teamRaw = document.getElementById(`team-${userId}`).value;
    const tasks = [
      api.updateUserProfile(userId, { title: title || null, team_id: teamRaw ? parseInt(teamRaw, 10) : null })
    ];
    if (hasRate) {
      const rate = parseFloat(document.getElementById(`rate-${userId}`).value);
      if (isNaN(rate) || rate < 0) { alert('Please enter a valid rate.'); return; }
      tasks.push(api.setHourlyRate(userId, rate));
    }
    try {
      await Promise.all(tasks);
      await loadUsers();
    } catch (err) { alert(err.message || 'Failed to save changes.'); }
  }

  async function createUser() {
    const username = document.getElementById('new-username').value.trim();
    const password = document.getElementById('new-password').value;
    const passwordConfirm = document.getElementById('new-password-confirm').value;
    const role = document.getElementById('new-role').value;
    const title = document.getElementById('new-title').value.trim();
    const teamRaw = document.getElementById('new-team').value;
    const errEl = document.getElementById('create-user-error');
    errEl.textContent = '';
    if (!username || !password) { errEl.textContent = 'Please fill in all fields.'; return; }
    if (password.length < 6) { errEl.textContent = 'Password must be at least 6 characters.'; return; }
    if (password !== passwordConfirm) { errEl.textContent = 'Passwords do not match.'; return; }
    try {
      await api.createUser({
        username, password, role, hourly_rate: 0,
        title: title || null,
        team_id: teamRaw ? parseInt(teamRaw, 10) : null
      });
      document.getElementById('new-username').value = '';
      document.getElementById('new-password').value = '';
      document.getElementById('new-password-confirm').value = '';
      document.getElementById('new-title').value = '';
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


  // ── Meeting rooms (manager) ──────────────────────────────────────────────

  // ── Teams (manager) ──────────────────────────────────────────────────────

  async function loadTeams() {
    try {
      const teams = await api.getTeams();
      teamsList = teams;
      const container = document.getElementById('teams-container');
      container.innerHTML = `
        <div class="create-user-form">
          <h3 style="font-size:14px;font-weight:500;margin-bottom:12px;">Add a team</h3>
          <div style="display:grid;grid-template-columns:2fr auto;gap:10px;align-items:end;">
            <div class="form-group">
              <label for="new-team-name">Name</label>
              <input type="text" id="new-team-name" placeholder="e.g. Platform" />
            </div>
            <button class="btn btn-primary" onclick="app.createTeam()" style="height:38px;">Add</button>
          </div>
          <p id="create-team-error" style="font-size:13px;color:var(--danger);margin-top:8px;min-height:18px;"></p>
        </div>
        <table style="width:100%;border-collapse:collapse;font-size:14px;margin-top:1rem;">
          <tbody>
            ${teams.length ? teams.map(t => `
              <tr>
                <td style="padding:12px 0;border-bottom:1px solid var(--border);">
                  <input type="text" id="team-name-${t.id}" value="${escapeAttr(t.name)}"
                    style="width:100%;max-width:280px;height:32px;padding:0 8px;border:1px solid var(--border);border-radius:var(--radius);background:var(--surface);color:inherit;font-size:14px;" />
                </td>
                <td style="padding:12px 0;border-bottom:1px solid var(--border);text-align:right;">
                  <div style="display:flex;gap:6px;justify-content:flex-end;">
                    <button class="btn" onclick="app.saveTeam(${t.id})">Save</button>
                    <button class="btn-icon danger" onclick="app.deleteTeam(${t.id}, '${t.name.replace(/'/g, "\\'")}')" title="Delete team">
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
                    </button>
                  </div>
                </td>
              </tr>
            `).join('') : `<tr><td style="padding:12px 0;color:var(--text-muted);">No teams configured yet.</td></tr>`}
          </tbody>
        </table>`;
    } catch { alert('Failed to load teams.'); }
  }

  async function createTeam() {
    const name = document.getElementById('new-team-name').value.trim();
    const errEl = document.getElementById('create-team-error');
    errEl.textContent = '';
    if (!name) { errEl.textContent = 'Please enter a team name.'; return; }
    try {
      await api.createTeam(name);
      document.getElementById('new-team-name').value = '';
      await loadTeams();
      await loadUsers(); // team dropdowns in the users table need the new option
    } catch (err) { errEl.textContent = err.message; }
  }

  async function saveTeam(teamId) {
    const name = document.getElementById(`team-name-${teamId}`).value.trim();
    if (!name) { alert('Team name cannot be empty.'); return; }
    try {
      await api.updateTeam(teamId, name);
      await loadTeams();
      await loadUsers();
    } catch (err) { alert(err.message || 'Failed to update team.'); }
  }

  async function deleteTeam(teamId, name) {
    if (!confirm(`Delete team "${name}"? Members will be unassigned, not deleted.`)) return;
    try {
      await api.deleteTeam(teamId);
      await loadTeams();
      await loadUsers();
    } catch { alert('Failed to delete team.'); }
  }

  async function loadRooms() {
    try {
      const rooms = await api.getRooms();
      const container = document.getElementById('rooms-container');
      container.innerHTML = `
        <div class="create-user-form">
          <h3 style="font-size:14px;font-weight:500;margin-bottom:12px;">Add a room</h3>
          <div style="display:grid;grid-template-columns:1.3fr 0.7fr 1.7fr 1fr auto;gap:10px;align-items:end;">
            <div class="form-group">
              <label for="new-room-name">Name</label>
              <input type="text" id="new-room-name" placeholder="e.g. Conference Room B" />
            </div>
            <div class="form-group">
              <label for="new-room-capacity">Capacity</label>
              <input type="number" id="new-room-capacity" min="1" placeholder="8" />
            </div>
            <div class="form-group">
              <label for="new-room-equipment">Equipment</label>
              <input type="text" id="new-room-equipment" placeholder="e.g. Projector, Whiteboard" />
            </div>
            <div class="form-group">
              <label for="new-room-status">Status</label>
              <select id="new-room-status">
                <option value="operational">Operational</option>
                <option value="renovation">Under renovation</option>
              </select>
            </div>
            <button class="btn btn-primary" onclick="app.createRoom()" style="height:38px;">Add</button>
          </div>
          <p id="create-room-error" style="font-size:13px;color:var(--danger);margin-top:8px;min-height:18px;"></p>
        </div>
        <table style="width:100%;border-collapse:collapse;font-size:14px;margin-top:1rem;">
          <thead>
            <tr>
              <th style="text-align:left;padding:8px 0;font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.07em;border-bottom:1px solid var(--border);">Room</th>
              <th style="text-align:left;padding:8px 0;font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.07em;border-bottom:1px solid var(--border);">Capacity</th>
              <th style="text-align:left;padding:8px 0;font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.07em;border-bottom:1px solid var(--border);">Equipment</th>
              <th style="text-align:left;padding:8px 0;font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.07em;border-bottom:1px solid var(--border);">Status</th>
              <th style="border-bottom:1px solid var(--border);"></th>
            </tr>
          </thead>
          <tbody>
            ${rooms.length ? rooms.map(r => `
              <tr>
                <td style="padding:12px 0;border-bottom:1px solid var(--border);">${escapeAttr(r.name)}</td>
                <td style="padding:12px 0;border-bottom:1px solid var(--border);">
                  <input type="number" id="room-capacity-${r.id}" value="${r.capacity ?? ''}" min="1"
                    style="width:70px;height:32px;padding:0 8px;border:1px solid var(--border);border-radius:var(--radius);font-size:14px;" />
                </td>
                <td style="padding:12px 0;border-bottom:1px solid var(--border);">
                  <input type="text" id="room-equipment-${r.id}" value="${r.equipment ? escapeAttr(r.equipment) : ''}"
                    style="width:100%;height:32px;padding:0 8px;border:1px solid var(--border);border-radius:var(--radius);font-size:14px;" />
                </td>
                <td style="padding:12px 0;border-bottom:1px solid var(--border);">
                  <select id="room-status-${r.id}" style="height:32px;border:1px solid var(--border);border-radius:var(--radius);background:var(--surface);color:inherit;">
                    <option value="operational"${r.status === 'operational' ? ' selected' : ''}>Operational</option>
                    <option value="renovation"${r.status === 'renovation' ? ' selected' : ''}>Under renovation</option>
                  </select>
                </td>
                <td style="padding:12px 0;border-bottom:1px solid var(--border);text-align:right;">
                  <div style="display:flex;gap:6px;justify-content:flex-end;">
                    <button class="btn" onclick="app.saveRoom(${r.id})">Save</button>
                    <button class="btn-icon danger" onclick="app.deleteRoom(${r.id}, '${r.name.replace(/'/g, "\\'")}')" title="Delete room">
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
                    </button>
                  </div>
                </td>
              </tr>
            `).join('') : `<tr><td colspan="5" style="padding:12px 0;color:var(--text-muted);">No rooms configured yet.</td></tr>`}
          </tbody>
        </table>
        ${rooms.length ? `<p class="card-subtitle" style="margin-top:14px;">Want to check when a room's actually free? The Meetings tab now has a Room availability lookup for that — visible to everyone, not just managers here.</p>` : ''}`;
    } catch { alert('Failed to load rooms.'); }
  }

  async function createRoom() {
    const name = document.getElementById('new-room-name').value.trim();
    const capacityRaw = document.getElementById('new-room-capacity').value;
    const equipment = document.getElementById('new-room-equipment').value.trim();
    const status = document.getElementById('new-room-status').value;
    const errEl = document.getElementById('create-room-error');
    errEl.textContent = '';
    if (!name) { errEl.textContent = 'Please enter a room name.'; return; }
    try {
      await api.createRoom({ name, capacity: capacityRaw ? parseInt(capacityRaw, 10) : null, equipment: equipment || null, status });
      document.getElementById('new-room-name').value = '';
      document.getElementById('new-room-capacity').value = '';
      document.getElementById('new-room-equipment').value = '';
      await loadRooms();
    } catch (err) { errEl.textContent = err.message; }
  }

  async function saveRoom(roomId) {
    const capacityRaw = document.getElementById(`room-capacity-${roomId}`).value;
    const equipment = document.getElementById(`room-equipment-${roomId}`).value.trim();
    const status = document.getElementById(`room-status-${roomId}`).value;
    try {
      await api.updateRoom(roomId, { capacity: capacityRaw ? parseInt(capacityRaw, 10) : null, equipment: equipment || null, status });
      await loadRooms();
    } catch { alert('Failed to update room.'); }
  }

  async function deleteRoom(roomId, name) {
    if (!confirm(`Delete room "${name}"?`)) return;
    try {
      await api.deleteRoom(roomId);
      await loadRooms();
    } catch { alert('Failed to delete room.'); }
  }

  // ── Clock sessions (manager) ─────────────────────────────────────────────

  async function loadClockSessions() {
    try {
      const now = new Date();
      const thirtyDaysAgo = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
      const sessions = await api.getClockSessions({
        date_from: fmt(thirtyDaysAgo), date_to: fmt(now)
      });
      const container = document.getElementById('clock-sessions-container');
      if (!sessions.length) {
        container.innerHTML = `<p class="card-subtitle">No clock sessions in the last 30 days.</p>`;
        return;
      }
      container.innerHTML = `
        <table style="width:100%;border-collapse:collapse;font-size:14px;">
          <thead>
            <tr>
              <th style="text-align:left;padding:8px 0;font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.07em;border-bottom:1px solid var(--border);">User</th>
              <th style="text-align:left;padding:8px 0;font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.07em;border-bottom:1px solid var(--border);">Clocked in</th>
              <th style="text-align:left;padding:8px 0;font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.07em;border-bottom:1px solid var(--border);">Clocked out</th>
              <th style="text-align:left;padding:8px 0;font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.07em;border-bottom:1px solid var(--border);">Hours</th>
              <th style="text-align:left;padding:8px 0;font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.07em;border-bottom:1px solid var(--border);">Status</th>
              <th style="border-bottom:1px solid var(--border);"></th>
            </tr>
          </thead>
          <tbody>
            ${sessions.map(s => `
              <tr${s.auto_closed ? ' style="background:rgba(230,160,60,0.08);"' : ''}>
                <td style="padding:10px 8px 10px 0;border-bottom:1px solid var(--border);">${escapeAttr(s.username)}</td>
                <td style="padding:10px 8px 10px 0;border-bottom:1px solid var(--border);">
                  <input type="datetime-local" id="cs-in-${s.id}" value="${s.clocked_in_at.slice(0, 16)}"
                    style="height:32px;padding:0 6px;border:1px solid var(--border);border-radius:var(--radius);background:var(--surface);color:inherit;font-size:12px;" />
                </td>
                <td style="padding:10px 8px 10px 0;border-bottom:1px solid var(--border);">
                  <input type="datetime-local" id="cs-out-${s.id}" value="${s.clocked_out_at ? s.clocked_out_at.slice(0, 16) : ''}"
                    style="height:32px;padding:0 6px;border:1px solid var(--border);border-radius:var(--radius);background:var(--surface);color:inherit;font-size:12px;" />
                </td>
                <td style="padding:10px 8px 10px 0;border-bottom:1px solid var(--border);">${s.hours != null ? s.hours.toFixed(2) + 'h' : (s.is_active ? '— (active)' : '—')}</td>
                <td style="padding:10px 8px 10px 0;border-bottom:1px solid var(--border);">
                  ${s.auto_closed ? '<span class="badge badge-pending">Auto-closed</span>' : (s.is_active ? '<span class="badge badge-other">Active</span>' : '<span class="badge badge-approved">Settled</span>')}
                </td>
                <td style="padding:10px 0;border-bottom:1px solid var(--border);text-align:right;">
                  <button class="btn" onclick="app.saveClockSession(${s.id})">Save</button>
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>`;
    } catch { alert('Failed to load clock sessions.'); }
  }

  async function saveClockSession(id) {
    const inRaw = document.getElementById(`cs-in-${id}`).value;
    const outRaw = document.getElementById(`cs-out-${id}`).value;
    if (!inRaw) { alert('Clock-in time is required.'); return; }
    try {
      await api.updateClockSession(id, {
        clocked_in_at: inRaw + ':00',
        clocked_out_at: outRaw ? outRaw + ':00' : null
      });
      await loadClockSessions();
    } catch (err) { alert(err.message || 'Failed to update clock session.'); }
  }

  // ── Meetings ──────────────────────────────────────────────────────────────

  function pad(n) { return String(n).padStart(2, '0'); }
  function monthRange(year, month) {
    const from = `${year}-${pad(month + 1)}-01`;
    const lastDay = new Date(year, month + 1, 0).getDate();
    const to = `${year}-${pad(month + 1)}-${pad(lastDay)}`;
    return { from, to };
  }

  async function loadMeetingsView() {
    if (calendarYear === undefined) {
      const now = new Date();
      calendarYear = now.getFullYear();
      calendarMonth = now.getMonth();
    }
    try {
      const [users, rooms] = await Promise.all([api.getBasicUsers(), api.getRooms()]);
      meetings.renderAttendeeOptions(users, currentUser.id);
      roomsList = rooms;
      meetings.setRooms(rooms);
      populateRoomOptions();
      populateAvailabilityRoomSelect();
      const emailInput = document.getElementById('my-email');
      if (emailInput && document.activeElement !== emailInput) {
        emailInput.value = currentUser.email || '';
      }
      await refreshCalendarMonth();
    } catch { alert('Failed to load meetings.'); }
  }

  function escapeAttr(s) {
    return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
  }

  function populateRoomOptions() {
    const datalist = document.getElementById('room-options');
    if (!datalist) return;
    datalist.innerHTML = roomsList.filter(r => r.status !== 'renovation').map(r => {
      const label = [r.capacity ? `${r.capacity} seats` : null, r.equipment || null].filter(Boolean).join(' · ');
      return `<option value="${escapeAttr(r.name)}"${label ? ` label="${escapeAttr(label)}"` : ''}></option>`;
    }).join('');
  }

  function populateAvailabilityRoomSelect() {
    const select = document.getElementById('availability-room-select');
    if (!select) return;
    const previous = select.value;
    select.innerHTML = `<option value="">Select a room…</option>` + roomsList.map(r =>
      `<option value="${r.id}">${escapeAttr(r.name)}${r.status === 'renovation' ? ' (under renovation)' : ''}</option>`
    ).join('');
    if (previous && roomsList.some(r => String(r.id) === previous)) select.value = previous;
  }

  async function loadMeetingsRoomAvailability(roomId) {
    const listEl = document.getElementById('meetings-room-occupancy-list');
    if (!listEl) return;
    if (!roomId) { listEl.innerHTML = ''; return; }
    listEl.innerHTML = `<p class="card-subtitle">Loading…</p>`;
    try {
      const slots = await api.getRoomOccupancy(roomId);
      if (!slots.length) {
        listEl.innerHTML = `<p class="card-subtitle">No bookings in the next 30 days — this room is fully free.</p>`;
        return;
      }
      listEl.innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:13px;">
        <tbody>
          ${slots.map(s => `
            <tr>
              <td style="padding:6px 10px 6px 0;color:var(--text-muted);white-space:nowrap;">${s.date}</td>
              <td style="padding:6px 10px 6px 0;white-space:nowrap;">${s.start_time} – ${s.end_time}</td>
              <td style="padding:6px 0;">${escapeAttr(s.title)}</td>
              <td style="padding:6px 0;color:var(--text-muted);">by ${escapeAttr(s.organizer_username)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>`;
    } catch { listEl.innerHTML = `<p class="card-subtitle" style="color:var(--danger);">Failed to load this room's schedule.</p>`; }
  }

  async function refreshCalendarMonth() {
    const { from, to } = monthRange(calendarYear, calendarMonth);
    monthMeetings = await api.getMeetings({ date_from: from, date_to: to });
    meetings.renderCalendar(calendarYear, calendarMonth, monthMeetings, selectedCalendarDate, today());
    if (selectedCalendarDate) renderSidebarForSelectedDate();
  }

  function renderSidebarForSelectedDate() {
    const dayMeetings = monthMeetings.filter(m => m.date === selectedCalendarDate);
    const label = selectedCalendarDate === today() ? 'Today' : meetings.formatDate(selectedCalendarDate);
    document.getElementById('sidebar-date-label').textContent = label;
    document.getElementById('meeting-date').value = selectedCalendarDate;
    meetings.renderList(dayMeetings, currentUser);
    renderRoomsBookedToday(dayMeetings);
  }

  function renderRoomsBookedToday(dayMeetings) {
    const el = document.getElementById('rooms-booked-today');
    if (!el) return;
    const booked = dayMeetings
      .filter(m => m.location_type === 'in_person' && m.room)
      .sort((a, b) => a.room.localeCompare(b.room) || a.start_time.localeCompare(b.start_time));
    if (!booked.length) {
      el.innerHTML = `<p class="card-subtitle" style="margin-bottom:14px;">No rooms booked on this date.</p>`;
      return;
    }
    el.innerHTML = `
      <div style="margin-bottom:14px;">
        <h3 style="font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--text-muted);margin-bottom:8px;">Rooms booked on this date</h3>
        ${booked.map(m => `
          <div style="display:flex;justify-content:space-between;font-size:13px;padding:4px 0;">
            <span>📍 ${escapeAttr(m.room)}</span>
            <span style="color:var(--text-muted);">${m.start_time} – ${m.end_time}</span>
          </div>
        `).join('')}
      </div>`;
  }

  function openMeetingPanel() {
    document.getElementById('meeting-panel').classList.remove('hidden');
    document.getElementById('meeting-panel-overlay').classList.remove('hidden');
  }

  function closeMeetingPanel() {
    document.getElementById('meeting-panel').classList.add('hidden');
    document.getElementById('meeting-panel-overlay').classList.add('hidden');
    selectedCalendarDate = null;
    meetings.renderCalendar(calendarYear, calendarMonth, monthMeetings, selectedCalendarDate, today());
  }

  async function shiftCalendarMonth(delta) {
    calendarMonth += delta;
    if (calendarMonth > 11) { calendarMonth = 0; calendarYear++; }
    if (calendarMonth < 0) { calendarMonth = 11; calendarYear--; }
    await refreshCalendarMonth();
  }

  function selectCalendarDate(dateStr) {
    selectedCalendarDate = dateStr;
    meetings.renderCalendar(calendarYear, calendarMonth, monthMeetings, selectedCalendarDate, today());
    renderSidebarForSelectedDate();
    openMeetingPanel();
    // Fire-and-forget: instant render from cache above keeps this snappy,
    // then this quietly picks up any RSVP made outside this tab (an
    // attendee replying from the emailed link, or another browser session)
    // and re-renders once it lands.
    refreshCalendarResilient();
  }

  function onMeetingSearchInput(value) {
    clearTimeout(meetingSearchDebounce);
    const query = value.trim();
    const resultsEl = document.getElementById('meeting-search-results');
    if (!query) {
      resultsEl.classList.add('hidden');
      resultsEl.innerHTML = '';
      return;
    }
    meetingSearchDebounce = setTimeout(async () => {
      try {
        const results = await api.getMeetings({ search: query });
        meetings.renderSearchResults(results);
        resultsEl.classList.remove('hidden');
      } catch { /* leave previous results visible on transient failure */ }
    }, 300);
  }

  async function jumpToMeetingDate(dateStr) {
    document.getElementById('meeting-search').value = '';
    document.getElementById('meeting-search-results').classList.add('hidden');
    const [y, m] = dateStr.split('-').map(Number);
    calendarYear = y; calendarMonth = m - 1;
    await refreshCalendarResilient();
    selectCalendarDate(dateStr);
  }

  async function saveMyEmail() {
    const email = document.getElementById('my-email').value.trim();
    try {
      await api.updateMyEmail(email);
      currentUser.email = email || null;
      const status = document.getElementById('email-save-status');
      status.textContent = 'Saved';
      setTimeout(() => { status.textContent = ''; }, 2000);
    } catch { alert('Failed to save email.'); }
  }

  async function respondToMeeting(id, status) {
    try {
      await api.rsvpMeeting(id, status);
    } catch { alert('Failed to respond to meeting.'); return; }
    await refreshCalendarResilient();
  }

  async function declineMeeting(id) {
    const reason = prompt('Optional — let the organizer know why (leave blank to skip):');
    if (reason === null) return; // cancelled the prompt
    try {
      await api.rsvpMeeting(id, 'declined', reason.trim() || null);
    } catch { alert('Failed to respond to meeting.'); return; }
    await refreshCalendarResilient();
  }

  function toggleMeetingLocation() {
    const isInPerson = document.querySelector('input[name="meeting-location-type"]:checked').value === 'in_person';
    document.getElementById('meeting-room-group').classList.toggle('hidden', !isInPerson);
    document.getElementById('meeting-link-group').classList.toggle('hidden', isInPerson);
  }

  function toggleGoogleMeetOption() {
    const useGoogleMeet = document.getElementById('meeting-use-google-meet').checked;
    document.getElementById('meeting-link').classList.toggle('hidden', useGoogleMeet);
    document.getElementById('meeting-link-label').classList.toggle('hidden', useGoogleMeet);
    if (useGoogleMeet) document.getElementById('meeting-link').value = '';
  }

  function toggleRecurrence() {
    const repeats = document.getElementById('meeting-recurrence').value !== 'none';
    document.getElementById('meeting-recurrence-until-group').classList.toggle('hidden', !repeats);
  }

  async function scheduleMeeting() {
    const title = document.getElementById('meeting-title').value.trim();
    const date = document.getElementById('meeting-date').value;
    const start_time = document.getElementById('meeting-start').value;
    const end_time = document.getElementById('meeting-end').value;
    const description = document.getElementById('meeting-description').value.trim();
    const location_type = document.querySelector('input[name="meeting-location-type"]:checked').value;
    const room = document.getElementById('meeting-room').value.trim();
    const meeting_link = document.getElementById('meeting-link').value.trim();
    const use_google_meet = document.getElementById('meeting-use-google-meet').checked;
    const attendee_ids = meetings.getSelectedAttendees();
    const recurrence = document.getElementById('meeting-recurrence').value;
    const recurrence_until = document.getElementById('meeting-recurrence-until').value;
    if (!title || !date || !start_time || !end_time) {
      alert('Please fill in title, date, start time, and end time.');
      return;
    }
    if (location_type === 'in_person' && !room) {
      alert('Please specify a room for an in-person meeting.');
      return;
    }
    if (recurrence !== 'none' && !recurrence_until) {
      alert('Please choose an end date for the recurring series.');
      return;
    }
    let result;
    try {
      result = await api.createMeeting({
        title, date, start_time, end_time, description: description || null, attendee_ids,
        location_type,
        room: location_type === 'in_person' ? room : null,
        meeting_link: location_type === 'online' ? (meeting_link || null) : null,
        use_google_meet: location_type === 'online' ? use_google_meet : false,
        recurrence, recurrence_until: recurrence !== 'none' ? recurrence_until : null
      });
    } catch (err) {
      alert(err.message || 'Failed to schedule meeting.');
      return;
    }

    // The meeting is saved at this point — everything from here on is just
    // refreshing the view, so a hiccup here must never look like the
    // scheduling itself failed.
    document.getElementById('meeting-title').value = '';
    document.getElementById('meeting-description').value = '';
    document.getElementById('meeting-room').value = '';
    document.getElementById('meeting-link').value = '';
    document.getElementById('meeting-use-google-meet').checked = false;
    document.getElementById('meeting-recurrence').value = 'none';
    document.getElementById('meeting-recurrence-until').value = '';
    toggleGoogleMeetOption();
    toggleRecurrence();
    meetings.clearAttendeeSelection();
    // Jump the calendar to the newly-scheduled date so the new meeting
    // is immediately visible, even if it's in a different month.
    document.getElementById('attendee-conflict-warning').innerHTML = '';
    const [y, m] = date.split('-').map(Number);
    calendarYear = y; calendarMonth = m - 1; selectedCalendarDate = date;
    await refreshCalendarResilient();

    let msg = '';
    if (result.series_count) {
      msg += `Scheduled ${result.series_count} occurrences.`;
      if (result.skipped_dates && result.skipped_dates.length) {
        msg += `\n\nSkipped (room already booked): ${result.skipped_dates.join(', ')}`;
      }
    }
    if (result.attendee_conflicts && result.attendee_conflicts.length) {
      const lines = result.attendee_conflicts.map(c =>
        `${c.username} already has "${c.meeting_title}" on ${c.date} at ${c.start_time}–${c.end_time}`
      );
      msg += (msg ? '\n\n' : '') + `⚠️ Heads up, some attendees are double-booked:\n${lines.join('\n')}`;
    }
    if (msg) alert(msg);
  }

  // ── Attendee conflict warnings ───────────────────────────────────────────

  let conflictCheckDebounce = null;

  function checkAttendeeConflictsLive() {
    clearTimeout(conflictCheckDebounce);
    conflictCheckDebounce = setTimeout(async () => {
      const date = document.getElementById('meeting-date').value;
      const start_time = document.getElementById('meeting-start').value;
      const end_time = document.getElementById('meeting-end').value;
      const attendee_ids = meetings.getSelectedAttendees();
      const warningEl = document.getElementById('attendee-conflict-warning');
      if (!date || !start_time || !end_time || end_time <= start_time || !attendee_ids.length) {
        warningEl.innerHTML = '';
        return;
      }
      try {
        const result = await api.checkMeetingConflicts({
          date, start_time, end_time, attendee_ids: attendee_ids.join(',')
        });
        renderAttendeeConflictWarning(result.conflicts);
      } catch { /* live check is a courtesy, not critical — fail quietly */ }
    }, 250);
  }

  function renderAttendeeConflictWarning(conflicts) {
    const el = document.getElementById('attendee-conflict-warning');
    if (!conflicts || !conflicts.length) { el.innerHTML = ''; return; }
    el.innerHTML = `<div class="conflict-warning">⚠️ ${conflicts.map(c =>
      `${escapeAttr(c.username)} already has "${escapeAttr(c.meeting_title)}" at ${c.start_time}–${c.end_time}`
    ).join('<br>')}</div>`;
  }

  // Wraps refreshCalendarMonth() with one quiet retry so a transient network
  // hiccup right after a create/reschedule/cancel doesn't leave the calendar
  // stale until the user manually reloads the page.
  async function refreshCalendarResilient() {
    try {
      await refreshCalendarMonth();
    } catch {
      await new Promise(r => setTimeout(r, 600));
      try {
        await refreshCalendarMonth();
      } catch {
        console.warn('Could not refresh the calendar automatically — it will catch up on the next action.');
      }
    }
  }

  async function cancelMeeting(id, groupId) {
    try {
      if (groupId) {
        if (confirm('This meeting is part of a recurring series.\n\nOK = cancel the ENTIRE series\nCancel = choose to cancel just this one occurrence')) {
          await api.deleteMeetingSeries(groupId);
        } else if (confirm('Cancel just this one occurrence?')) {
          await api.deleteMeeting(id);
        } else {
          return;
        }
      } else {
        if (!confirm('Cancel this meeting?')) return;
        await api.deleteMeeting(id);
      }
    } catch { alert('Failed to cancel meeting.'); return; }
    await refreshCalendarResilient();
  }

  // ── Reschedule ────────────────────────────────────────────────────────────

  function openRescheduleForm(meetingId) {
    const meeting = meetings.getMeetingById(meetingId);
    if (!meeting) { alert('Could not find that meeting — try reopening the day.'); return; }
    document.getElementById('reschedule-meeting-id').value = meeting.id;
    document.getElementById('reschedule-title-label').textContent = meeting.title;
    document.getElementById('reschedule-date').value = meeting.date;
    document.getElementById('reschedule-start').value = meeting.start_time;
    document.getElementById('reschedule-end').value = meeting.end_time;
    document.getElementById('reschedule-modal').classList.remove('hidden');
  }

  function closeRescheduleForm() {
    document.getElementById('reschedule-modal').classList.add('hidden');
  }

  async function submitReschedule() {
    const id = document.getElementById('reschedule-meeting-id').value;
    const date = document.getElementById('reschedule-date').value;
    const start_time = document.getElementById('reschedule-start').value;
    const end_time = document.getElementById('reschedule-end').value;
    if (!date || !start_time || !end_time) {
      alert('Please fill in date, start time, and end time.');
      return;
    }
    try {
      await api.rescheduleMeeting(id, { date, start_time, end_time });
    } catch (err) { alert(err.message || 'Failed to reschedule meeting.'); return; }
    closeRescheduleForm();
    const [y, m] = date.split('-').map(Number);
    calendarYear = y; calendarMonth = m - 1; selectedCalendarDate = date;
    await refreshCalendarResilient();
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
    addEntry, deleteEntry, approveEntry,
    openRejectModal, closeRejectModal, confirmReject,
    openEdit, closeModal, saveEdit,
    loadEntries, goToEntriesPage, exportCSV, quickFilter,
    setReportPeriod, shiftPeriod, printReport,
    loadUsers, createUser, deleteUser, saveUserRow,
    loadTeams, createTeam, saveTeam, deleteTeam,
    loadRooms, createRoom, saveRoom, deleteRoom, loadMeetingsRoomAvailability,
    scheduleMeeting, cancelMeeting, respondToMeeting, declineMeeting, saveMyEmail,
    checkAttendeeConflictsLive,
    toggleMeetingLocation, toggleGoogleMeetOption,
    toggleRecurrence, openRescheduleForm, closeRescheduleForm, submitReschedule,
    shiftCalendarMonth, selectCalendarDate, closeMeetingPanel,
    onMeetingSearchInput, jumpToMeetingDate,
    showForgotPassword, showLoginFromForgot, submitForgotPassword, submitResetPassword
  };
})();