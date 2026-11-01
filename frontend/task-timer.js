/* Live task clocks are derived from saved timestamps, never accumulated ticks. */
(function (root) {
  'use strict';

  const doc = root.document;
  let intervalId = null;

  function timestamp(value) {
    return typeof value === 'string' && value.trim() ? Date.parse(value) : NaN;
  }

  function elapsedMilliseconds(start, finish, now = Date.now()) {
    const startMs = timestamp(start);
    const endMs = finish ? timestamp(finish) : now;
    return Number.isFinite(startMs) && Number.isFinite(endMs)
      ? Math.max(0, endMs - startMs) : null;
  }

  function formatMilliseconds(value) {
    if (value === null || !Number.isFinite(value)) return '—';
    const seconds = Math.floor(Math.max(0, value) / 1000);
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor(seconds % 3600 / 60);
    return [hours, minutes, seconds % 60].map(n => String(n).padStart(2, '0')).join(':');
  }

  function markup(start, finish) {
    const elapsed = elapsedMilliseconds(start, finish);
    if (elapsed === null) return '<span class="task-timer unavailable">—</span>';
    // Canonical dates contain no attribute delimiters, even for unexpected input.
    const from = new Date(timestamp(start)).toISOString();
    const to = finish ? new Date(timestamp(finish)).toISOString() : '';
    return `<span class="task-timer ${to ? 'finished' : 'running'}" data-task-timer data-timer-start="${from}" data-timer-finish="${to}" role="timer" aria-label="任务已用时间" aria-live="off"><strong data-timer-clock>${formatMilliseconds(elapsed)}</strong><small>${to ? '已用时' : '计时中'}</small></span>`;
  }

  function updateClocks() {
    if (!doc) return false;
    const now = Date.now();
    let running = false;
    doc.querySelectorAll('[data-task-timer]').forEach(element => {
      if (!element.isConnected || element.closest('[hidden]')) return;
      const { timerStart, timerFinish } = element.dataset;
      const elapsed = elapsedMilliseconds(timerStart, timerFinish, now);
      const clock = element.querySelector('[data-timer-clock]');
      const text = formatMilliseconds(elapsed);
      if (clock && clock.textContent !== text) clock.textContent = text;
      if (!timerFinish && elapsed !== null) running = true;
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

  root.StudyTaskTimer = { markup, refresh, elapsedMilliseconds, formatMilliseconds };
  if (doc) {
    doc.addEventListener('visibilitychange', refresh);
    root.addEventListener('pageshow', refresh);
  }
})(typeof window === 'undefined' ? globalThis : window);
