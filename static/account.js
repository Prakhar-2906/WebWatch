(() => {
  try { document.documentElement.dataset.theme = localStorage.getItem('webwatch.theme.v1') || 'light'; } catch { /* Use light theme when storage is unavailable. */ }
  const message = document.getElementById('invite-message');
  const cookie = () => document.cookie.split('; ').find((part) => part.startsWith('webwatch_csrf='))?.slice('webwatch_csrf='.length) || '';
  async function getCsrf() {
    if (cookie()) return cookie();
    await fetch('/auth/csrf', { credentials: 'same-origin' });
    return cookie();
  }
  async function request(path, method, body) {
    const response = await fetch(path, { method, credentials: 'same-origin', headers: {
      'Content-Type': 'application/json', 'X-CSRF-Token': await getCsrf(), 'X-WebWatch-Request': '1'
    }, body: body ? JSON.stringify(body) : undefined });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'The request could not be completed.');
    return data;
  }
  request('/auth/me', 'GET').then(({ user }) => { document.getElementById('account-email').textContent = user.email; })
    .catch(() => window.location.assign('/login'));
  document.getElementById('invite-form').addEventListener('submit', async (event) => {
    event.preventDefault(); message.textContent = '';
    try {
      const result = await request('/auth/invitations', 'POST', { email: document.getElementById('invite-email').value });
      message.textContent = `Invitation sent to ${result.email}. It expires in seven days.`;
      event.currentTarget.reset();
    } catch (error) { message.textContent = error.message; }
  });
  document.getElementById('logout-button').addEventListener('click', async () => {
    try { await request('/auth/logout', 'POST'); window.location.assign('/login'); }
    catch (error) { message.textContent = error.message; }
  });
})();
