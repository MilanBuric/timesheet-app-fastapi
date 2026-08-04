const timer = (() => {
  let interval = null;
  let startTime = null;

  function formatElapsed(ms) {
    const s = Math.floor(ms / 1000);
    const h = Math.floor(s / 3600).toString().padStart(2, '0');
    const m = Math.floor((s % 3600) / 60).toString().padStart(2, '0');
    const sec = (s % 60).toString().padStart(2, '0');
    return `${h}:${m}:${sec}`;
  }

  function tick() {
    document.getElementById('timer-display').textContent = formatElapsed(Date.now() - startTime);
  }

  function start(clockedInAt) {
    startTime = new Date(clockedInAt + 'Z').getTime();
    document.getElementById('timer-display').classList.add('running');
    document.getElementById('clock-btn').textContent = 'Clock out';
    document.getElementById('clock-btn').classList.add('clocked-in');
    clearInterval(interval);
    interval = setInterval(tick, 1000);
    tick();
  }

  function stop() {
    clearInterval(interval);
    interval = null;
    startTime = null;
    document.getElementById('timer-display').textContent = '00:00:00';
    document.getElementById('timer-display').classList.remove('running');
    document.getElementById('clock-btn').textContent = 'Clock in';
    document.getElementById('clock-btn').classList.remove('clocked-in');
  }

  function isRunning() { return interval !== null; }

  return { start, stop, isRunning };
})();
