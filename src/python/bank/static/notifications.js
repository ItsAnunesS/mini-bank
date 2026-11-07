'use strict';
(() => {
    const bell = document.getElementById('notification-bell');
    if (!bell) return;
    const count = document.getElementById('notification-count');
    const status = document.getElementById('notification-update-status');
    const feedUrl = new URL(bell.dataset.feedUrl, window.location.origin);
    const cursor = new URL(window.location.href).searchParams.get('notification_before');
    if (cursor) feedUrl.searchParams.set('notification_before', cursor);
    let timer;
    let polling = false;
    let pollFinished = Promise.resolve();
    let submitting = false;
    let stopped = false;
    let delay = 15000;

    async function send(url, options = {}) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 10000);
        try {
            const response = await fetch(url, {
                ...options, credentials: 'same-origin', cache: 'no-store',
                headers: {Accept: 'application/json'}, signal: controller.signal
            });
            if (response.redirected || response.status === 401 || response.status === 403) {
                stopped = true;
                throw new Error('A sessão terminou. Entra novamente para atualizar as notificações.');
            }
            if (!response.ok) {
                throw new Error(options.method === 'POST'
                    ? 'Não foi possível marcar como lida. Atualiza a página e tenta novamente.'
                    : 'Não foi possível atualizar as notificações. Vamos tentar novamente.');
            }
            return await response.json();
        } finally {
            clearTimeout(timeout);
        }
    }

    function schedule() {
        clearTimeout(timer);
        if (!stopped && !document.hidden) timer = setTimeout(refresh, delay);
    }

    async function refresh(force = false) {
        if (polling || stopped || document.hidden || (submitting && !force)) return;
        polling = true;
        let finishPoll;
        pollFinished = new Promise(resolve => { finishPoll = resolve; });
        try {
            const data = await send(feedUrl);
            count.textContent = String(data.unread_count);
            bell.setAttribute('aria-label', `Notificações: ${data.unread_count} não lidas`);
            const panel = document.getElementById('notifications');
            // The fragment is rendered and escaped by the same-origin Jinja template.
            // Keep keyboard focus stable during background updates.
            const focusedElement = document.activeElement;
            const interacting = panel?.contains(focusedElement) && focusedElement.matches('a, button, input, select, textarea');
            if (panel && (force || !interacting)) {
                const template = document.createElement('template');
                template.innerHTML = data.html;
                panel.replaceWith(template.content);
                if (force || focusedElement.id === 'notifications-title') {
                    document.getElementById('notifications-title')?.focus({preventScroll: !force});
                }
            }
            status.textContent = '';
            delay = 15000;
        } catch (error) {
            status.textContent = error.name === 'AbortError'
                ? 'A atualização demorou demasiado. Vamos tentar novamente.' : error.name === 'TypeError' ? 'Ligação indisponível. Verifica a ligação e tenta novamente.' : error.message;
            delay = Math.min(delay * 2, 60000);
        } finally {
            polling = false;
            finishPoll();
            schedule();
        }
    }

    document.addEventListener('submit', async (event) => {
        const form = event.target.closest('form[data-notification-action]');
        if (!form) return;
        event.preventDefault();
        if (submitting || stopped) return;
        submitting = true;
        clearTimeout(timer);
        const button = form.querySelector('button');
        button.disabled = true;
        try {
            const body = new FormData(form);
            await pollFinished;
            await send(form.action, {method: 'POST', body});
            await refresh(true);
        } catch (error) {
            status.textContent = error.name === 'AbortError'
                ? 'Não foi possível confirmar a leitura. Tenta novamente; a operação não será duplicada.' : error.name === 'TypeError' ? 'Ligação indisponível. Verifica a ligação e tenta novamente.' : error.message;
        } finally {
            button.disabled = false;
            submitting = false;
            schedule();
        }
    });
    document.addEventListener('visibilitychange', () => {
        clearTimeout(timer);
        if (!document.hidden) refresh();
    });
    refresh();
})();
