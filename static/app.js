/**
 * Clinic Portal - Shared JavaScript utilities.
 *
 * Per CLAUDE.md Rule #15: vanilla JS only, no React/Vue.
 * All POST/PUT/DELETE requests include the CSRF token from the meta tag.
 */

/**
 * Get CSRF token from the meta tag injected by base.html.
 */
function getCsrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : '';
}

/**
 * Wrapper for fetch() that includes CSRF token, JSON content type,
 * and same-origin credentials by default.
 */
async function apiFetch(url, options) {
    options = options || {};
    var defaults = {
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCsrfToken(),
        },
        credentials: 'same-origin',
    };
    // Merge headers
    var mergedHeaders = Object.assign({}, defaults.headers, options.headers || {});
    var merged = Object.assign({}, defaults, options);
    merged.headers = mergedHeaders;
    return fetch(url, merged);
}

/**
 * Show an alert message in the #alert-container element.
 * type: 'success', 'error', or 'info'.
 */
function showAlert(message, type) {
    type = type || 'info';
    var container = document.getElementById('alert-container');
    if (!container) return;
    var div = document.createElement('div');
    div.className = 'alert alert-' + type;
    div.setAttribute('role', 'alert');
    div.textContent = message;
    // Auto-dismiss after 5 seconds
    setTimeout(function() {
        if (div.parentNode) div.parentNode.removeChild(div);
    }, 5000);
    container.appendChild(div);
}

/**
 * Escape HTML to prevent XSS when inserting user content.
 */
function escapeHtml(text) {
    var div = document.createElement('div');
    div.textContent = text || '';
    return div.innerHTML;
}

/**
 * Logout handler: POST to /api/auth/logout then redirect.
 */
document.addEventListener('DOMContentLoaded', function() {
    var logoutBtn = document.getElementById('logout-btn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', async function() {
            var resp = await apiFetch('/api/auth/logout', { method: 'POST' });
            if (resp.ok) {
                window.location.href = '/login/';
            } else {
                showAlert('Logout failed.', 'error');
            }
        });
    }
});
