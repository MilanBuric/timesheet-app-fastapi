const meetings = (() => {
  function formatDate(d) {
    const [y, m, day] = d.split('-');
    return new Date(y, m - 1, day).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
  }

  function cancelIcon() {
    return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
  }

  function renderAttendeeOptions(users, currentUserId) {
    const container = document.getElementById('meeting-attendees-list');
    const options = users.filter(u => u.id !== currentUserId);
    if (!options.length) {
      container.innerHTML = '<p class="attendees-empty">No other users to invite yet.</p>';
      return;
    }
    container.innerHTML = options.map(u => `
      <label class="attendee-chip">
        <input type="checkbox" value="${u.id}" />
        <span>${u.username}</span>
        <span class="attendee-role">${u.role}</span>
      </label>
    `).join('');
  }

  function getSelectedAttendees() {
    return Array.from(document.querySelectorAll('#meeting-attendees-list input:checked')).map(el => parseInt(el.value, 10));
  }

  function clearAttendeeSelection() {
    document.querySelectorAll('#meeting-attendees-list input:checked').forEach(el => el.checked = false);
  }

  function statusBadge(status) {
    const map = {
      accepted: '<span class="rsvp-badge rsvp-accepted">Accepted</span>',
      declined: '<span class="rsvp-badge rsvp-declined">Declined</span>',
      pending: '<span class="rsvp-badge rsvp-pending">Pending</span>'
    };
    return map[status] || '';
  }

  function locationInfo(m) {
    if (m.location_type === 'in_person') {
      return `<div class="meeting-room-tag">📍 ${m.room}</div>`;
    }
    if (m.meeting_link) {
      return `<a href="${m.meeting_link}" target="_blank" rel="noopener" class="meeting-join-link">💻 Join meeting</a>`;
    }
    return `<div class="meeting-location-info">💻 Online</div>`;
  }

  function renderList(list, currentUser) {
    const container = document.getElementById('meetings-container');
    if (!list.length) {
      container.innerHTML = '<div class="empty">No meetings scheduled.</div>';
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
              <span class="meeting-date">${formatDate(m.date)}</span>
              <span class="meeting-time">${m.start_time} – ${m.end_time}</span>
            </div>
            ${canCancel ? `<button class="btn-icon danger" onclick="app.cancelMeeting(${m.id})" title="Cancel meeting">${cancelIcon()}</button>` : ''}
          </div>
          <h3 class="meeting-title">${m.title}</h3>
          ${m.description ? `<p class="meeting-desc">${m.description}</p>` : ''}
          <p class="meeting-organizer">Organized by ${m.organizer_username}</p>
          ${locationInfo(m)}
          ${m.attendees.length ? `<div class="meeting-attendees">${m.attendees.map(a =>
            `<span class="attendee-tag">${a.username} ${statusBadge(a.status)}</span>`
          ).join('')}</div>` : ''}
          ${isInvitee && myAttendance.status === 'pending' ? `
            <div class="rsvp-actions">
              <button class="btn btn-primary btn-sm" onclick="app.respondToMeeting(${m.id}, 'accepted')">Accept</button>
              <button class="btn btn-sm" onclick="app.respondToMeeting(${m.id}, 'declined')">Decline</button>
            </div>` : ''}
          ${isInvitee && myAttendance.status !== 'pending' ? `
            <p class="my-rsvp-note">You ${myAttendance.status} this invite.</p>` : ''}
        </div>`;
    }).join('');
  }

  return { renderAttendeeOptions, getSelectedAttendees, clearAttendeeSelection, renderList };
})();
