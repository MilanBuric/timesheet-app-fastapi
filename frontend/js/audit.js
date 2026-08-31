const audit = (() => {
  // Same reasoning as entries.js's escapeHtml: actor_username and summary
  // ultimately trace back to user-entered text (an activity name, a team
  // name, a rejection reason) that ends up quoted inside a summary string
  // — it must never go into innerHTML unescaped.
  function escapeHtml(s) {
    return String(s).replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  }

  function formatDateTime(iso) {
    // created_at is stored as a naive UTC isoformat string with no 'Z' —
    // same convention as clocked_in_at elsewhere in the app (see
    // reminders.py) — so append one before handing it to Date(), or the
    // browser would parse it as local time and show the wrong moment.
    const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
    return d.toLocaleString("en-GB", {
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  // Action strings follow an "entity.verb" convention (see audit_log.py) —
  // color by the verb so create/update/delete are visually distinct at a
  // glance, regardless of which entity type they apply to.
  function verbBadgeClass(action) {
    const verb = action.split(".")[1] || "";
    if (verb === "create") return "badge-approved"; // green
    if (verb === "delete" || verb === "cancel" || verb === "cancel_series")
      return "badge-rejected"; // red
    return "badge-meeting"; // update/approve/reject/correct/reschedule/rsvp — amber, catch-all "changed"
  }

  function renderTable(list, containerId) {
    const container = document.getElementById(containerId);
    if (!list.length) {
      container.innerHTML =
        '<div class="empty">No matching audit log entries.</div>';
      return;
    }
    container.innerHTML = `
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>When</th>
              <th>Actor</th>
              <th>Action</th>
              <th>Entity</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            ${list
              .map(
                (l) => `
              <tr>
                <td class="td-date">${formatDateTime(l.created_at)}</td>
                <td>${l.actor_username ? escapeHtml(l.actor_username) : "<em>system</em>"}</td>
                <td><span class="badge ${verbBadgeClass(l.action)}">${escapeHtml(l.action)}</span></td>
                <td>${escapeHtml(l.entity_type)}${l.entity_id ? ` #${escapeHtml(l.entity_id)}` : ""}</td>
                <td>${escapeHtml(l.summary)}</td>
              </tr>
            `,
              )
              .join("")}
          </tbody>
        </table>
      </div>`;
  }

  return { renderTable };
})();
