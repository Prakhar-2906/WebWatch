(() => {
  try { document.documentElement.dataset.theme = localStorage.getItem('webwatch.theme.v1') || 'light'; } catch { /* Use light theme when storage is unavailable. */ }
  const mode = document.body.dataset.mode;
  const form = document.getElementById('auth-form');
  const message = document.getElementById('auth-message');
  const intro = document.getElementById('auth-intro');
  const setupLink = document.getElementById('auth-setup-link');
  const inviteLink = document.querySelector('.auth-links a[href="/invite"]');
  const guestButton = document.getElementById('guest-start');
  async function csrf() {
    const response = await fetch('/auth/csrf', { cache: 'no-store', credentials: 'same-origin' });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || 'Authentication is not configured on this server.');
    const token = result.csrf_token;
    if (!token) throw new Error('A fresh security token could not be created. Reload the page and try again.');
    return token;
  }
  async function send(path, payload, headers = {}) {
    const token = await csrf();
    const response = await fetch(path, { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token, ...headers }, body: JSON.stringify(payload) });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || 'The request could not be completed.');
    return result;
  }
  fetch('/auth/status', { credentials: 'same-origin' }).then((r) => r.json()).then((status) => {
    if (!status.authentication_available) intro.textContent = 'Authentication is not configured. Set a stable server secret before using WebWatch.';
    else if (mode === 'login') intro.textContent = 'Sign in to view your private monitored pages and history.';
    else if (mode === 'setup') {
      intro.textContent = status.initial_setup_available ? 'Create the first owner account using the setup token supplied by the administrator.' : 'Owner setup is unavailable or has already been completed.';
      form.hidden = !status.initial_setup_available;
      if (setupLink) setupLink.hidden = !status.initial_setup_available;
    } else intro.textContent = 'Enter the email address the invitation was sent to and the invitation code from that email.';
    if (mode !== 'invite' && inviteLink) inviteLink.hidden = true;
  }).catch(() => { intro.textContent = 'Account status could not be loaded.'; });
  if (guestButton) guestButton.addEventListener('click', async () => {
    guestButton.disabled = true;
    message.textContent = 'Starting a temporary guest session…';
    try {
      await send('/auth/guest', {});
      window.location.assign('/');
    } catch (error) {
      message.textContent = error.message;
      guestButton.disabled = false;
    }
  });
  form.addEventListener('submit', async (event) => {
    event.preventDefault(); message.textContent = '';
    const payload = { email: document.getElementById('email').value, password: document.getElementById('password').value };
    try {
      if (mode === 'setup') {
        const token = document.getElementById('setup-token').value;
        await send('/auth/setup', payload, { 'X-WebWatch-Owner-Setup': token });
        window.location.assign('/');
      } else if (mode === 'login') {
        await send('/auth/login', payload);
        const requested = new URLSearchParams(location.search).get('next') || '/';
        let safeNext = '/';
        try {
          const candidate = new URL(requested, location.origin);
          if (candidate.origin === location.origin) safeNext = `${candidate.pathname}${candidate.search}`;
        } catch { /* Invalid next paths return to the dashboard. */ }
        window.location.assign(`${safeNext}${location.hash}`);
      } else {
        payload.invitation_token = document.getElementById('invitation-token').value;
        await send('/auth/redeem', payload);
        message.textContent = 'Invitation accepted. You can now sign in.';
        form.reset();
      }
    } catch (error) { message.textContent = error.message; }
  });
})();
