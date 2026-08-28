/* Shared UI plumbing for the dashboard: toasts and modals.
 *
 * Both exist for the file transfers, which report progress and finish
 * asynchronously — the one part of this app the server cannot narrate, because
 * the bytes never pass through it. Everywhere else, the server renders the
 * message (see ui.py's flash) and there is no script at all.
 *
 * The theme used to live here too; it is a cookie the server reads now, so the
 * page never paints in the wrong palette before JavaScript catches up. */

// ---------- Toasts ----------
function showToast(message, type = 'info') {
  let container = document.getElementById('toastContainer');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toastContainer';
    container.className = 'toast-container position-fixed top-0 end-0 p-3';
    document.body.appendChild(container);
  }

  const bg = type === 'success' ? 'bg-success'
    : type === 'error' ? 'bg-danger'
    : type === 'warning' ? 'bg-warning' : 'bg-info';
  const textColor = type === 'warning' ? 'text-dark' : 'text-white';

  const toastEl = document.createElement('div');
  toastEl.className = `border-0 rounded shadow-sm mb-2 ${bg} ${textColor}`;
  toastEl.setAttribute('role', 'alert');
  toastEl.setAttribute('aria-live', 'assertive');
  Object.assign(toastEl.style, {
    padding: '0.75rem 1rem', minWidth: '260px', display: 'flex',
    justifyContent: 'space-between', alignItems: 'center'
  });

  const span = document.createElement('span');
  span.textContent = message;          // textContent: these carry device output
  const close = document.createElement('button');
  close.type = 'button';
  close.setAttribute('aria-label', 'Fechar');
  close.textContent = '✕';
  close.style.cssText = `background:transparent;border:none;${type === 'warning' ? '' : 'filter:invert(1);'}`;
  close.onclick = () => toastEl.remove();

  toastEl.append(span, close);
  container.appendChild(toastEl);
  setTimeout(() => toastEl.remove(), 5000);
}

/**
 * Modal controller — ours on purpose, rather than bootstrap.Modal.
 *
 * Two reasons:
 *
 *  - `.modal` in style.css is already a self-contained fixed layer carrying its
 *    own backdrop, so Bootstrap's separate .modal-backdrop element is a second,
 *    near-invisible overlay stacked on top of ours.
 *  - Bootstrap's hide() is a silent no-op while its own show transition is still
 *    running (`_isShown && !_isTransitioning`), and nothing ever retries it. Work
 *    that finished inside that ~300ms window left the modal open with the
 *    backdrop swallowing every click on the page, which reads as the whole UI
 *    freezing.
 *
 * Toggling a class is synchronous: it cannot be dropped, and there is no state
 * machine to get out of step with. Bootstrap's JavaScript bundle is not loaded
 * anywhere in this app any more — only its stylesheet.
 */
function makeModal(el) {
  if (!el) return null;

  const hide = () => {
    el.classList.remove('show');
    el.setAttribute('aria-hidden', 'true');
    // Unlock the page only once nothing else is still open.
    if (!document.querySelector('.modal.show')) {
      document.body.classList.remove('modal-open');
    }
  };

  const show = () => {
    el.classList.add('show');
    el.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
  };

  el.addEventListener('click', (event) => {
    // Dismiss on the backdrop itself, and on anything marked as a dismisser.
    if (event.target === el || event.target.closest('[data-bs-dismiss="modal"]')) hide();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && el.classList.contains('show')) hide();
  });

  return { show, hide };
}
