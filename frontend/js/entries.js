const entries = (() => {
  function formatDate(d) {
    const [y, m, day] = d.split('-');
    return new Date(y, m - 1, day).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
  }

  function badgeClass(cat) {
    return cat === 'Self-study' ? 'study' : cat === 'Meeting' ? 'meeting' : 'other';
  }

  function statusBadge(status) {
    const map = { pending: 'badge-pending', approved: 'badge-approved', rejected: 'badge-rejected' };
    const labels = { pending: 'Pending', approved: 'Approved', rejected: 'Rejected' };
    return `<span class="badge ${map[status] || 'badge-pending'}">${labels[status] || status}</span>`;
  }

  function overtimeIcon() {
    return `<span class="overtime-flag" title="Overtime: over 8h logged this day">⚠️</span>`;
  }

  function editIcon() {
    return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>`;
  }

  function trashIcon() {
    return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>`;
  }

  function checkIcon() {
    return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;
  }

  function xIcon() {
    return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
  }

  function renderTable(list, containerId, isManager = false) {
    const container = document.getElementById(containerId);
    if (!list.length) {
      container.innerHTML = '<div class="empty">No entries found.</div>';
      return;
    }

    container.innerHTML = `
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              ${isManager ? '<th>Intern</th>' : ''}
              <th>Date</th>
              <th>Activity</th>
              <th>Category</th>
              <th>Hours</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            ${list.map(e => `
              <tr class="${e.overtime ? 'row-overtime' : ''}">
                ${isManager ? `<td class="td-date">${e.username || '—'}</td>` : ''}
                <td class="td-date">${formatDate(e.date)}</td>
                <td>${e.activity} ${e.overtime ? overtimeIcon() : ''}</td>
                <td><span class="badge badge-${badgeClass(e.category)}">${e.category}</span></td>
                <td class="td-hours">${e.hours}h</td>
                <td>${statusBadge(e.status)}</td>
                <td>
                  <div class="td-actions">
                    ${isManager ? `
                      <button class="btn-icon success" onclick="app.approveEntry(${e.id})" title="Approve">${checkIcon()}</button>
                      <button class="btn-icon danger" onclick="app.rejectEntry(${e.id})" title="Reject">${xIcon()}</button>
                    ` : `
                      <button class="btn-icon" onclick="app.openEdit(${JSON.stringify(e).replace(/"/g, '&quot;')})" aria-label="Edit">${editIcon()}</button>
                      <button class="btn-icon danger" onclick="app.deleteEntry(${e.id})" aria-label="Delete">${trashIcon()}</button>
                    `}
                  </div>
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>`;
  }

  function exportCSV(list) {
    const header = ['Date', 'Activity', 'Category', 'Hours', 'Status'];
    const rows = list.map(e => [e.date, `"${e.activity.replace(/"/g, '""')}"`, e.category, e.hours, e.status]);
    const csv = [header, ...rows].map(r => r.join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `timesheet-${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return { renderTable, exportCSV };
})();
