(() => {
  const form = document.getElementById('leadForm');
  if (!form) return;

  form.addEventListener('submit', async (event) => {
    event.preventDefault();

    const note = document.getElementById('formNote');
    const button = form.querySelector('button[type="submit"]');
    const originalText = button ? button.textContent : '';

    if (button) {
      button.disabled = true;
      button.textContent = 'Joining…';
    }
    if (note) note.textContent = 'Submitting your email…';

    try {
      const response = await fetch('https://formspree.io/f/xdenwvjy', {
        method: 'POST',
        body: new FormData(form),
        headers: { Accept: 'application/json' }
      });

      if (!response.ok) {
        const details = await response.json().catch(() => ({}));
        throw new Error(details.error || 'Formspree submission failed');
      }

      form.reset();
      if (note) note.textContent = '✓ You’re on the list. We’ll email you when GrowTell finds an important new property-growth opportunity.';
      if (button) button.textContent = 'Joined ✓';

      try {
        const key = 'growtell_analytics_v1';
        const events = JSON.parse(localStorage.getItem(key) || '[]');
        events.push({ type: 'signup_success', detail: 'early_access', at: new Date().toISOString() });
        localStorage.setItem(key, JSON.stringify(events.slice(-500)));
      } catch (_) {}
    } catch (error) {
      console.error('GrowTell signup failed:', error);
      if (note) note.textContent = 'Signup failed. Please try again — if it keeps failing, we’ll fix it.';
      if (button) {
        button.disabled = false;
        button.textContent = originalText || 'Join free alerts';
      }
    }
  });
})();