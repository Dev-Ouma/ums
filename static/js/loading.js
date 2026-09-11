/* ==========================================================================
   UMS Centralized Loading / Processing System (LoadingService)
   Single reusable source for full-screen, section, button and inline
   loading states, plus page-to-page navigation. Driven entirely by real
   request lifecycles -- no artificial delays, no fake success states.
   ========================================================================== */

const LoadingService = {
  _globalCount: 0,
  _sectionCounts: new WeakMap(),
  _overlay: null,
  _overlayText: null,
  _navLoaderActive: false,
  _navTimer: null,
  _navSafetyTimer: null,

  init() {
    this._buildGlobalOverlay();
    this._buildNavProgress();
    this._bindFormSubmissions();
    this._bindNavigationProgress();
    this._bindBfcacheRecovery();
  },

  // ---------------- Global full-screen overlay ----------------
  _buildGlobalOverlay() {
    if (document.getElementById('umsGlobalLoader')) {
      this._overlay = document.getElementById('umsGlobalLoader');
      this._overlayText = this._overlay.querySelector('.ums-loader-text');
      return;
    }
    const el = document.createElement('div');
    el.id = 'umsGlobalLoader';
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.setAttribute('aria-busy', 'true');
    el.innerHTML = `
      <div class="ums-loader-card">
        <span class="ums-loader-halo"><span class="ums-spin ums-spin-lg" aria-hidden="true"></span></span>
        <span class="ums-loader-text">Processing your request&hellip;</span>
      </div>`;
    document.body.appendChild(el);
    this._overlay = el;
    this._overlayText = el.querySelector('.ums-loader-text');
  },

  show(message) {
    this._globalCount++;
    if (this._overlayText) {
      this._overlayText.textContent = message || 'Processing your request…';
    }
    if (this._overlay) this._overlay.classList.add('active');
    clearTimeout(this._globalSafetyTimer);
    // Safety net: never let the overlay stay stuck indefinitely if a caller
    // forgets to call hide() (e.g. an uncaught exception before hide()).
    this._globalSafetyTimer = setTimeout(() => {
      this._globalCount = 0;
      if (this._overlay) this._overlay.classList.remove('active');
    }, 30000);
  },

  hide() {
    this._globalCount = Math.max(0, this._globalCount - 1);
    if (this._globalCount === 0) {
      clearTimeout(this._globalSafetyTimer);
      if (this._overlay) this._overlay.classList.remove('active');
    }
  },

  hideAll() {
    this._globalCount = 0;
    clearTimeout(this._globalSafetyTimer);
    if (this._overlay) this._overlay.classList.remove('active');
  },

  // ---------------- Section / local loader ----------------
  showIn(container, message) {
    if (!container) return;
    const count = (this._sectionCounts.get(container) || 0) + 1;
    this._sectionCounts.set(container, count);

    container.classList.add('ums-section-loading');
    let loader = container.querySelector(':scope > .ums-section-loader');
    if (!loader) {
      loader = document.createElement('div');
      loader.className = 'ums-section-loader';
      loader.setAttribute('role', 'status');
      loader.setAttribute('aria-live', 'polite');
      loader.innerHTML = `
        <span class="ums-spin ums-spin-md" aria-hidden="true"></span>
        <span class="ums-loader-text">${message || 'Loading…'}</span>`;
      container.appendChild(loader);
    } else {
      loader.querySelector('.ums-loader-text').textContent = message || 'Loading…';
    }
    container.setAttribute('aria-busy', 'true');
  },

  hideIn(container) {
    if (!container) return;
    const count = Math.max(0, (this._sectionCounts.get(container) || 0) - 1);
    this._sectionCounts.set(container, count);
    if (count === 0) {
      const loader = container.querySelector(':scope > .ums-section-loader');
      if (loader) loader.remove();
      container.classList.remove('ums-section-loading');
      container.removeAttribute('aria-busy');
    }
  },

  // ---------------- Button processing state ----------------
  _loadingVerbs: {
    save: 'Saving', update: 'Updating', delete: 'Deleting', remove: 'Deleting',
    submit: 'Submitting', approve: 'Approving', reject: 'Rejecting',
    publish: 'Publishing', import: 'Importing', export: 'Preparing',
    download: 'Preparing', generate: 'Generating', pay: 'Processing payment',
    reset: 'Resetting', create: 'Creating', add: 'Adding', send: 'Sending',
    login: 'Authenticating', 'sign in': 'Authenticating', logout: 'Signing out',
    activate: 'Activating', verify: 'Verifying', restore: 'Restoring',
    confirm: 'Confirming', register: 'Registering', enroll: 'Enrolling',
    apply: 'Applying', upload: 'Uploading',
  },

  _guessLoadingText(originalText) {
    const clean = (originalText || '').trim().toLowerCase();
    for (const key in this._loadingVerbs) {
      if (clean.includes(key)) return this._loadingVerbs[key] + '…';
    }
    return 'Processing…';
  },

  /**
   * Puts a button into its processing state and returns a restore()
   * function. Caller must invoke restore() once the operation settles
   * (success or error) -- for normal form submits this happens naturally
   * via page navigation, so no explicit restore call is required there.
   */
  button(btnEl, loadingText) {
    if (!btnEl || btnEl.classList.contains('ums-btn-loading')) return () => {};

    const label = btnEl.querySelector('.ums-btn-label') || (() => {
      const span = document.createElement('span');
      span.className = 'ums-btn-label';
      while (btnEl.firstChild) span.appendChild(btnEl.firstChild);
      btnEl.appendChild(span);
      return span;
    })();

    const spinner = document.createElement('span');
    spinner.className = 'ums-spin ums-spin-sm';
    spinner.setAttribute('aria-hidden', 'true');

    const srText = document.createElement('span');
    srText.className = 'visually-hidden';
    srText.textContent = loadingText || this._guessLoadingText(label.textContent);

    btnEl.appendChild(spinner);
    btnEl.appendChild(srText);
    btnEl.classList.add('ums-btn-loading');
    btnEl.setAttribute('aria-busy', 'true');
    btnEl.disabled = true;

    let restored = false;
    return () => {
      if (restored) return;
      restored = true;
      btnEl.classList.remove('ums-btn-loading');
      btnEl.removeAttribute('aria-busy');
      btnEl.disabled = false;
      spinner.remove();
      srText.remove();
    };
  },

  // ---------------- Inline loader (search boxes, dropdowns, filters) ------
  inline(target, message) {
    if (!target) return () => {};
    const el = document.createElement('span');
    el.className = 'ums-inline-loader';
    el.setAttribute('role', 'status');
    el.innerHTML = `<span class="ums-spin ums-spin-sm" aria-hidden="true"></span><span>${message || 'Loading…'}</span>`;
    target.appendChild(el);
    return () => el.remove();
  },

  // ---------------- Automatic form-submit button loading ----------------
  _bindFormSubmissions() {
    // Bubble phase (not capture): this must run AFTER any inline
    // onsubmit="return confirm(...)" or other handler on the form itself
    // has had a chance to cancel the submit, otherwise a cancelled
    // confirm() dialog would leave the button stuck in a disabled
    // "loading" state with no submit ever happening.
    document.addEventListener('submit', (event) => {
      if (event.defaultPrevented) return; // form itself already cancelled/owns this submit
      const form = event.target;
      if (!(form instanceof HTMLFormElement)) return;
      if (form.hasAttribute('data-no-loading')) return;

      const submitter = event.submitter ||
        form.querySelector('button[type="submit"]:not([disabled])');
      if (!submitter || submitter.tagName !== 'BUTTON') return;
      if (submitter.hasAttribute('data-no-loading')) return;

      // Native validation runs before the submit event fires only when the
      // form is not `novalidate`; if it reached here, fields already passed.
      const loadingText = submitter.getAttribute('data-loading-text');
      this.button(submitter, loadingText);
    });
  },

  // ---------------- Navigation loading popup ----------------
  // Every real page-to-page navigation pops the same centered overlay used
  // for other processing states (dimmed backdrop, centered card) -- the
  // "USSD dialog" pattern: it appears in the middle of the screen while the
  // request is in flight and is gone the instant the next page is ready.
  _buildNavProgress() {
    // Nothing to build separately -- navigation reuses the global overlay
    // built by _buildGlobalOverlay(), via the same show()/hide() reference
    // count as every other loading operation in the app.
  },

  _startNavProgress() {
    clearTimeout(this._navTimer);
    clearTimeout(this._navSafetyTimer);
    // Debounce: only pop the overlay if navigation takes longer than a
    // blink, so instant page loads don't flash a distracting popup.
    this._navTimer = setTimeout(() => {
      this._navLoaderActive = true;
      this.show('Loading…');
    }, 120);
    // Safety net in case the browser never fires unload/pageshow for this navigation.
    this._navSafetyTimer = setTimeout(() => this._finishNavProgress(), 15000);
  },

  _finishNavProgress() {
    clearTimeout(this._navTimer);
    clearTimeout(this._navSafetyTimer);
    if (this._navLoaderActive) {
      this._navLoaderActive = false;
      this.hide();
    }
  },

  _bindNavigationProgress() {
    document.addEventListener('click', (event) => {
      if (event.defaultPrevented || event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

      const link = event.target.closest('a[href]');
      if (!link) return;
      if (link.target && link.target !== '_self') return;
      if (link.hasAttribute('download')) return;
      if (link.hasAttribute('data-no-loading')) return;

      const href = link.getAttribute('href');
      if (!href || href.startsWith('#') || href.startsWith('javascript:') || href.startsWith('mailto:') || href.startsWith('tel:')) return;

      let url;
      try { url = new URL(href, location.href); } catch (e) { return; }
      if (url.origin !== location.origin) return;
      if (url.pathname === location.pathname && url.search === location.search) return;

      this._startNavProgress();
    }, true);

    window.addEventListener('beforeunload', () => this._startNavProgress());
    window.addEventListener('pageshow', () => this._finishNavProgress());
  },

  // ---------------- bfcache recovery ----------------
  _bindBfcacheRecovery() {
    window.addEventListener('pageshow', (event) => {
      if (event.persisted) {
        window.location.reload();
      }
    });
  },
};

window.LoadingService = LoadingService;

document.addEventListener('DOMContentLoaded', () => {
  LoadingService.init();
});
