const meetings = (() => {
  function formatDate(d) {
    const [y, m, day] = d.split('-');
    return new Date(y, m - 1, day).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
  }

  function cancelIcon() {
    return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
  }

  function rescheduleIcon() {
    return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg>`;
  }

  function repeatIcon() {
    return `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 2l4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="M7 22l-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/></svg>`;
  }

  function renderAttendeeOptions(users, currentUserId) {
    const container = document.getElementById('meeting-attendees-list');
    const options = users.filter(u => u.id !== currentUserId);
    if (!options.length) {
      container.innerHTML = '<p class="attendees-empty">No other users to invite yet.</p>';
      return;
    }

    const teams = {}; // team_id -> { name, members: [] }
    const noTeam = [];
    options.forEach(u => {
      if (u.team_id) {
        if (!teams[u.team_id]) teams[u.team_id] = { name: u.team_name, members: [] };
        teams[u.team_id].members.push(u);
      } else {
        noTeam.push(u);
      }
    });

    function memberChip(u) {
      const detail = [u.title, u.team_name].filter(Boolean).join(' · ');
      return `
      <label class="attendee-chip">
        <input type="checkbox" data-user-id="${u.id}" onchange="meetings.syncTeamCheckbox(this)" />
        <span>${escapeHtml(u.username)}</span>
        ${detail ? `<span class="attendee-detail">${escapeHtml(detail)}</span>` : ''}
        <span class="attendee-role">${u.role}</span>
      </label>
    `;
    }

    const hasTeams = Object.keys(teams).length > 0;
    let html = Object.entries(teams).map(([teamId, t]) => `
      <div class="team-group">
        <label class="team-chip">
          <input type="checkbox" data-team-id="${teamId}" onchange="meetings.toggleTeamMembers(this)" />
          <span>👥 ${escapeHtml(t.name)}</span>
          <span class="attendee-role">${t.members.length}</span>
        </label>
        <div class="team-members">${t.members.map(memberChip).join('')}</div>
      </div>
    `).join('');

    if (noTeam.length) {
      html += hasTeams
        ? `<div class="team-group"><span class="team-group-label">No team</span><div class="team-members">${noTeam.map(memberChip).join('')}</div></div>`
        : `<div class="team-members">${noTeam.map(memberChip).join('')}</div>`;
    }

    container.innerHTML = html;
  }

  function toggleTeamMembers(teamCheckbox) {
    const group = teamCheckbox.closest('.team-group');
    group.querySelectorAll('input[data-user-id]').forEach(cb => { cb.checked = teamCheckbox.checked; });
    teamCheckbox.indeterminate = false;
  }

  function syncTeamCheckbox(memberCheckbox) {
    const group = memberCheckbox.closest('.team-group');
    const teamCheckbox = group && group.querySelector('input[data-team-id]');
    if (!teamCheckbox) return; // "No team" group has no select-all checkbox
    const members = Array.from(group.querySelectorAll('input[data-user-id]'));
    const checkedCount = members.filter(m => m.checked).length;
    teamCheckbox.checked = checkedCount === members.length;
    teamCheckbox.indeterminate = checkedCount > 0 && checkedCount < members.length;
  }

  function getSelectedAttendees() {
    return Array.from(document.querySelectorAll('#meeting-attendees-list input[data-user-id]:checked'))
      .map(el => parseInt(el.dataset.userId, 10));
  }

  function clearAttendeeSelection() {
    document.querySelectorAll('#meeting-attendees-list input').forEach(el => {
      el.checked = false;
      el.indeterminate = false;
    });
  }

  function statusBadge(status) {
    const map = {
      accepted: '<span class="rsvp-badge rsvp-accepted">Accepted</span>',
      declined: '<span class="rsvp-badge rsvp-declined">Declined</span>',
      pending: '<span class="rsvp-badge rsvp-pending">Pending</span>'
    };
    return map[status] || '';
  }

  let roomsByName = {};

  function setRooms(rooms) {
    roomsByName = {};
    (rooms || []).forEach(r => { roomsByName[r.name] = r; });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function locationInfo(m) {
    if (m.location_type === 'in_person') {
      const room = roomsByName[m.room];
      const meta = room && (room.capacity || room.equipment)
        ? ` <span class="room-meta">${room.capacity ? `· ${room.capacity} seats` : ''}${room.equipment ? ` · ${escapeHtml(room.equipment)}` : ''}</span>`
        : '';
      return `<div class="meeting-room-tag">📍 ${escapeHtml(m.room)}${meta}</div>`;
    }
    if (m.meeting_link) {
      return `<a href="${m.meeting_link}" target="_blank" rel="noopener" class="meeting-join-link">💻 Join meeting</a>`;
    }
    return `<div class="meeting-location-info">💻 Online</div>`;
  }

  let renderedById = {};

  function getMeetingById(id) {
    return renderedById[id] || null;
  }

  function renderList(list, currentUser) {
    const container = document.getElementById('sidebar-meetings-list');
    renderedById = {};
    list.forEach(m => { renderedById[m.id] = m; });
    if (!list.length) {
      container.innerHTML = '<div class="empty">No meetings on this date.</div>';
      return;
    }
    container.innerHTML = list.map(m => {
      const canCancel = currentUser.role === 'manager' || m.organizer_id === currentUser.id;
      const myAttendance = m.attendees.find(a => a.id === currentUser.id);
      const isInvitee = !!myAttendance && m.organizer_id !== currentUser.id;
      return `
        <div class="meeting-card">
          <div class="meeting-card-header">
            <div>
              <span class="meeting-time">${m.start_time} – ${m.end_time}</span>
              ${m.recurrence_rule && m.recurrence_rule !== 'none' ? `<span class="recurring-badge" title="Repeats ${m.recurrence_rule}">${repeatIcon()} ${m.recurrence_rule}</span>` : ''}
            </div>
            ${canCancel ? `
              <div class="meeting-card-actions">
                <button class="btn-icon" onclick="app.openRescheduleForm(${m.id})" title="Reschedule">${rescheduleIcon()}</button>
                <button class="btn-icon danger" onclick="app.cancelMeeting(${m.id}, ${m.recurrence_group_id ? `'${m.recurrence_group_id}'` : 'null'})" title="Cancel meeting">${cancelIcon()}</button>
              </div>` : ''}
          </div>
          <h3 class="meeting-title">${escapeHtml(m.title)}</h3>
          ${m.description ? `<p class="meeting-desc">${escapeHtml(m.description)}</p>` : ''}
          <p class="meeting-organizer">Organized by ${m.organizer_username}</p>
          ${locationInfo(m)}
          ${m.attendees.length ? `<div class="meeting-attendees">${m.attendees.map(a =>
            `<span class="attendee-tag"${a.decline_reason ? ` title="Declined: ${escapeHtml(a.decline_reason)}"` : ''}>${escapeHtml(a.username)} ${statusBadge(a.status)}</span>`
          ).join('')}</div>` : ''}
          ${isInvitee && myAttendance.status === 'pending' ? `
            <div class="rsvp-actions">
              <button class="btn btn-primary btn-sm" onclick="app.respondToMeeting(${m.id}, 'accepted')">Accept</button>
              <button class="btn btn-sm" onclick="app.declineMeeting(${m.id})">Decline</button>
            </div>` : ''}
          ${isInvitee && myAttendance.status !== 'pending' ? `
            <p class="my-rsvp-note">You ${myAttendance.status} this invite.${myAttendance.decline_reason ? ` <em>"${escapeHtml(myAttendance.decline_reason)}"</em>` : ''}</p>` : ''}
        </div>`;
    }).join('');
  }

  function renderSearchResults(list) {
    const container = document.getElementById('meeting-search-results');
    if (!list.length) {
      container.innerHTML = '<div class="empty">No meetings match your search.</div>';
      return;
    }
    container.innerHTML = list.map(m => `
      <div class="search-result-row" onclick="app.jumpToMeetingDate('${m.date}')">
        <div class="search-result-date">${formatDate(m.date)}</div>
        <div class="search-result-info">
          <div class="search-result-title">${escapeHtml(m.title)}</div>
          <div class="search-result-meta">${m.start_time} – ${m.end_time} · Organized by ${escapeHtml(m.organizer_username)}</div>
        </div>
      </div>
    `).join('');
  }

  const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June',
                        'July', 'August', 'September', 'October', 'November', 'December'];

  function pad(n) { return String(n).padStart(2, '0'); }

  const MAX_CHIPS_PER_DAY = 3;

  function renderCalendar(year, month, meetingsList, selectedDate, todayStr) {
    document.getElementById('calendar-month-label').textContent = `${MONTH_NAMES[month]} ${year}`;

    const byDate = {};
    meetingsList.forEach(m => {
      (byDate[m.date] = byDate[m.date] || []).push(m);
    });

    const firstWeekday = new Date(year, month, 1).getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const grid = document.getElementById('calendar-grid');

    let html = '';
    for (let i = 0; i < firstWeekday; i++) {
      html += `<div class="calendar-day empty"></div>`;
    }
    for (let day = 1; day <= daysInMonth; day++) {
      const dateStr = `${year}-${pad(month + 1)}-${pad(day)}`;
      const classes = ['calendar-day'];
      if (dateStr === todayStr) classes.push('today');
      if (dateStr === selectedDate) classes.push('selected');

      const dayMeetings = byDate[dateStr] || [];
      const chips = dayMeetings.slice(0, MAX_CHIPS_PER_DAY)
        .map(m => `<div class="calendar-event-chip">${m.start_time} ${escapeHtml(m.title)}</div>`).join('');
      const extra = dayMeetings.length > MAX_CHIPS_PER_DAY
        ? `<div class="calendar-event-more">+${dayMeetings.length - MAX_CHIPS_PER_DAY} more</div>` : '';

      html += `
        <div class="${classes.join(' ')}" onclick="app.selectCalendarDate('${dateStr}')">
          <span class="calendar-day-number">${day}</span>
          ${chips}${extra}
        </div>`;
    }
    grid.innerHTML = html;
  }

  return {
    renderAttendeeOptions, getSelectedAttendees, clearAttendeeSelection, renderList, renderCalendar,
    formatDate, getMeetingById, setRooms, renderSearchResults, toggleTeamMembers, syncTeamCheckbox
  };
})();