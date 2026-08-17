document.addEventListener('click', (event) => {
  const link = event.target.closest('a[data-track="alert_example"], a[href="#radar"]');
  if (!link) return;
  const radar = document.getElementById('radar');
  if (!radar) return;
  event.preventDefault();

  const go = () => {
    const featured = document.querySelector('#grid .card, #grid #featured-opportunity');
    const target = featured || radar;
    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    if (featured) {
      const old = featured.style.boxShadow;
      featured.style.transition = 'box-shadow .2s ease';
      featured.style.boxShadow = '0 0 0 3px #38bdf8, 0 0 32px rgba(56,189,248,.25)';
      setTimeout(() => { featured.style.boxShadow = old; }, 1600);
    }
    try { history.replaceState(null, '', '#radar'); } catch (_) {}
  };

  // Cards are rendered from data.json after page load, so wait briefly if needed.
  if (document.querySelector('#grid .card')) go();
  else {
    let tries = 0;
    const timer = setInterval(() => {
      tries++;
      if (document.querySelector('#grid .card') || tries > 20) {
        clearInterval(timer);
        go();
      }
    }, 100);
  }
});
