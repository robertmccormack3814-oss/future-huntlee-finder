document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('a[href="#radar"], a[data-track="alert_example"]').forEach(link => {
    link.addEventListener('click', event => {
      const radar = document.getElementById('radar');
      if (!radar) return;
      event.preventDefault();
      radar.scrollIntoView({ behavior: 'smooth', block: 'start' });
      try { history.replaceState(null, '', '#radar'); } catch (_) {}
    });
  });
});
