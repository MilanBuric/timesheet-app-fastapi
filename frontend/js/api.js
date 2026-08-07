const api = (() => {
  let token = sessionStorage.getItem('ts_token') || null;

  function setToken(t) {
    token = t;
    if (t) sessionStorage.setItem('ts_token', t);
    else sessionStorage.removeItem('ts_token');
  }

  function getToken() { return token; }

  function authHeaders() {
    return token
      ? { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' }
      : { 'Content-Type': 'application/json' };
  }

  async function request(method, path, body = null) {
    const opts = { method, headers: authHeaders() };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(path, opts);
    if (res.status === 401) { setToken(null); window.location.reload(); return; }
    return res;
  }

  async function login(username, password) {
    const res = await fetch('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    });
    if (!res.ok) throw new Error('Invalid username or password');
    const data = await res.json();
    setToken(data.access_token);
    return data;
  }

  function logout() { setToken(null); }

  async function me() {
    const res = await request('GET', '/auth/me');
    if (!res.ok) throw new Error('Not authenticated');
    return res.json();
  }

  async function getUsers() {
    const res = await request('GET', '/users');
    if (!res.ok) throw new Error('Failed to fetch users');
    return res.json();
  }

  async function createUser(data) {
    const res = await request('POST', '/users', data);
    if (res.status === 409) throw new Error('Username already taken');
    if (!res.ok) throw new Error('Failed to create user');
    return res.json();
  }

  async function deleteUser(id) {
    const res = await request('DELETE', `/users/${id}`);
    if (!res.ok) throw new Error('Failed to delete user');
  }

  async function setHourlyRate(userId, rate) {
    const res = await request('PATCH', `/users/${userId}/rate`, { hourly_rate: rate });
    if (!res.ok) throw new Error('Failed to update rate');
    return res.json();
  }

  async function getEntries(params = {}) {
    const qs = new URLSearchParams(Object.fromEntries(Object.entries(params).filter(([, v]) => v)));
    const res = await request('GET', `/entries?${qs}`);
    if (!res.ok) throw new Error('Failed to fetch entries');
    return res.json();
  }

  async function createEntry(data) {
    const res = await request('POST', '/entries', data);
    if (res.status === 409) {
      const err = await res.json();
      const e = new Error(err.detail);
      e.status = 409;
      throw e;
    }
    if (!res.ok) throw new Error('Failed to create entry');
    return res.json();
  }

  async function updateEntry(id, data) {
    const res = await request('PATCH', `/entries/${id}`, data);
    if (!res.ok) throw new Error('Failed to update entry');
    return res.json();
  }

  async function deleteEntry(id) {
    const res = await request('DELETE', `/entries/${id}`);
    if (!res.ok) throw new Error('Failed to delete entry');
  }

  async function approveEntry(id) {
    const res = await request('POST', `/entries/${id}/approve`);
    if (!res.ok) throw new Error('Failed to approve entry');
    return res.json();
  }

  async function rejectEntry(id, reason = '') {
    const res = await request('POST', `/entries/${id}/reject`, { reason: reason || null });
    if (!res.ok) throw new Error('Failed to reject entry');
    return res.json();
  }

  async function getBasicUsers() {
    const res = await request('GET', '/users/basic');
    if (!res.ok) throw new Error('Failed to fetch users');
    return res.json();
  }

  async function getMeetings(params = {}) {
    const qs = new URLSearchParams(Object.fromEntries(Object.entries(params).filter(([, v]) => v)));
    const res = await request('GET', `/meetings?${qs}`);
    if (!res.ok) throw new Error('Failed to fetch meetings');
    return res.json();
  }

  async function createMeeting(data) {
    const res = await request('POST', '/meetings', data);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to schedule meeting');
    }
    return res.json();
  }

  async function deleteMeeting(id) {
    const res = await request('DELETE', `/meetings/${id}`);
    if (!res.ok) throw new Error('Failed to cancel meeting');
  }

  async function rsvpMeeting(id, status) {
    const res = await request('POST', `/meetings/${id}/rsvp`, { status });
    if (!res.ok) throw new Error('Failed to respond to meeting');
    return res.json();
  }

  async function updateMyEmail(email) {
    const res = await request('PATCH', '/auth/me/email', { email: email || null });
    if (!res.ok) throw new Error('Failed to update email');
    return res.json();
  }

  async function getStats() {
    const clientDate = new Date().toLocaleDateString('en-CA'); // YYYY-MM-DD in local time
    const res = await request('GET', `/stats?client_date=${clientDate}`);
    if (!res.ok) throw new Error('Failed to fetch stats');
    return res.json();
  }

  async function getWeeklyReport(dateFrom, dateTo, userId = null) {
    const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
    if (userId) params.append('user_id', userId);
    const res = await request('GET', `/reports/weekly?${params}`);
    if (!res.ok) throw new Error('Failed to fetch report');
    return res.json();
  }

  async function clockIn() {
    const res = await request('POST', '/clock/in');
    if (!res.ok) throw new Error('Failed to clock in');
    return res.json();
  }

  async function clockOut() {
    const res = await request('POST', '/clock/out');
    if (!res.ok) throw new Error('Failed to clock out');
    return res.json();
  }

  async function getActiveSession() {
    const res = await request('GET', '/clock/active');
    if (!res.ok) throw new Error('Failed to fetch session');
    return res.json();
  }

  return {
    login, logout, me, getToken,
    getUsers, createUser, deleteUser, setHourlyRate,
    getEntries, createEntry, updateEntry, deleteEntry, approveEntry, rejectEntry,
    getStats, getWeeklyReport,
    clockIn, clockOut, getActiveSession,
    getBasicUsers, getMeetings, createMeeting, deleteMeeting, rsvpMeeting,
    updateMyEmail
  };
})();
