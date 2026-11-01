/* Live task clocks are derived from saved timestamps, never accumulated ticks. */
(function (root) {
  'use strict';

  const doc = root.document;
  let intervalId = null;

  function timestamp(value) {
    return typeof value === 'string' && value.trim() ? Date.parse(value) : NaN;
  }

  function normalizePauses(intervals) {
    if (!Array.isArray(intervals)) return [];
    return intervals.reduce((valid, interval) => {
      if (!interval || typeof interval !== 'object') return valid;
      const start = timestamp(interval.started_at);
      const open = interval.finished_at === null;
      const finish = open ? null : timestamp(interval.finished_at);
      if (Number.isFinite(start) && (open || (Number.isFinite(finish) && finish >= start))) {
        valid.push({ started_at: new Date(start).toISOString(), finished_at: open ? null : new Date(finish).toISOString() });
      }
      return valid;
    }, []);
  }

  function pauseMilliseconds(startMs, endMs, intervals = []) {
    if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return 0;
    const ranges = normalizePauses(intervals).map(interval => [
      Math.max(startMs, timestamp(interval.started_at)),
      Math.min(endMs, interval.finished_at === null ? endMs : timestamp(interval.finished_at))
    ]).filter(([start, end]) => end > start).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    let total = 0, from = null, to = null;
    ranges.forEach(([start, end]) => {
      if (from === null) { from = start; to = end; }
      else if (start <= to) to = Math.max(to, end);
      else { total += to - from; from = start; to = end; }
    });
    return Math.min(endMs - startMs, total + (from === null ? 0 : to - from));
  }

  function isPaused(intervals) {
    return normalizePauses(intervals).some(interval => interval.finished_at === null);
  }

  function elapsedMilliseconds(start, finish, now = Date.now(), pauseIntervals = []) {
    const startMs = timestamp(start);
    const endMs = finish ? timestamp(finish) : now;
    return Number.isFinite(startMs) && Number.isFinite(endMs)
      ? Math.max(0, endMs - startMs - pauseMilliseconds(startMs, endMs, pauseIntervals)) : null;
  }

  function formatMilliseconds(value) {
    if (value === null || !Number.isFinite(value)) return '—';
    const seconds = Math.floor(Math.max(0, value) / 1000);
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor(seconds % 3600 / 60);
    return [hours, minutes, seconds % 60].map(n => String(n).padStart(2, '0')).join(':');
  }

  function markup(start, finish, pauseIntervals = []) {
    const pauses = normalizePauses(pauseIntervals);
    const elapsed = elapsedMilliseconds(start, finish, Date.now(), pauses);
    if (elapsed === null) return '<span class="task-timer unavailable">—</span>';
    // Canonical dates contain no attribute delimiters, even for unexpected input.
    const from = new Date(timestamp(start)).toISOString();
    const to = finish ? new Date(timestamp(finish)).toISOString() : '';
    const paused = !to && isPaused(pauses);
    return `<span class="task-timer ${to ? 'finished' : paused ? 'paused' : 'running'}" data-task-timer data-timer-start="${from}" data-timer-finish="${to}" data-timer-pauses="${encodeURIComponent(JSON.stringify(pauses))}" role="timer" aria-label="任务有效用时" aria-live="off"><strong data-timer-clock>${formatMilliseconds(elapsed)}</strong><small>${to ? '已用时' : paused ? '已暂停' : '计时中'}</small></span>`;
  }

  function updateClocks() {
    if (!doc) return false;
    const now = Date.now();
    let running = false;
    doc.querySelectorAll('[data-task-timer]').forEach(element => {
      if (!element.isConnected || element.closest('[hidden]')) return;
      const { timerStart, timerFinish } = element.dataset;
      let pauses = [];
      try { pauses = normalizePauses(JSON.parse(decodeURIComponent(element.dataset.timerPauses || '%5B%5D'))); } catch (_) {}
      const elapsed = elapsedMilliseconds(timerStart, timerFinish, now, pauses);
      const clock = element.querySelector('[data-timer-clock]');
      const text = formatMilliseconds(elapsed);
      if (clock && clock.textContent !== text) clock.textContent = text;
      if (!timerFinish && !isPaused(pauses) && elapsed !== null) running = true;
    });
    return running;
  }

  function stopInterval() {
    if (intervalId !== null) root.clearInterval(intervalId);
    intervalId = null;
  }

  function refresh() {
    const running = updateClocks();
    if (!running || (doc && doc.visibilityState === 'hidden')) {
      stopInterval();
    } else if (intervalId === null) {
      intervalId = root.setInterval(() => {
        if (!updateClocks()) stopInterval();
      }, 1000);
    }
  }

  root.StudyTaskTimer = { markup, refresh, elapsedMilliseconds, formatMilliseconds, pauseMilliseconds, isPaused };
  if (doc) {
    doc.addEventListener('visibilitychange', refresh);
    root.addEventListener('pageshow', refresh);
  }
})(typeof window === 'undefined' ? globalThis : window);
