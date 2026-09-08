// Sidebar toggle (mobile)
function toggleSidebar() {
  const sidebar = document.querySelector('.sidebar');
  const backdrop = document.getElementById('sidebarBackdrop');
  if (!sidebar) return;
  const isOpen = sidebar.classList.toggle('open');
  if (backdrop) {
    backdrop.classList.toggle('active', isOpen);
  }
}

function closeMobileSidebar() {
  const sidebar = document.querySelector('.sidebar');
  const backdrop = document.getElementById('sidebarBackdrop');
  if (sidebar) sidebar.classList.remove('open');
  if (backdrop) backdrop.classList.remove('active');
}

function getCookie(name) {
  const v = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
  return v ? v.pop() : '';
}

// ---------- AI chat widget ----------
async function aiSend(message, boxId = 'aiMessages', inputId = null) {
  const box = document.getElementById(boxId);
  if (!box) return;
  addBubble(box, message, 'me');
  if (inputId) document.getElementById(inputId).value = '';
  const typing = addBubble(box, '<i class="fa-solid fa-ellipsis fa-fade"></i>', 'bot');
  try {
    const res = await fetch(AI_REPLY_URL, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken')},
      body: JSON.stringify({message})
    });
    const data = await res.json();
    typing.innerHTML = `<i class="fa-solid ${data.icon} me-2 text-primary"></i>${data.reply}`;
    renderChips(data.suggestions, boxId, inputId);
  } catch (e) {
    typing.innerHTML = 'Sorry, I could not reach the assistant.';
  }
  box.scrollTop = box.scrollHeight;
}

function addBubble(box, html, cls) {
  const d = document.createElement('div');
  d.className = 'chat-bubble ' + cls;
  d.innerHTML = html;
  box.appendChild(d);
  box.scrollTop = box.scrollHeight;
  return d;
}

function renderChips(list, boxId, inputId) {
  if (!list) return;
  const box = document.getElementById(boxId);
  const wrap = document.createElement('div');
  wrap.className = 'mb-2';
  list.forEach(s => {
    const c = document.createElement('span');
    c.className = 'chip';
    c.textContent = s;
    c.onclick = () => aiSend(s, boxId, inputId);
    wrap.appendChild(c);
  });
  box.appendChild(wrap);
  box.scrollTop = box.scrollHeight;
}

function aiFormSubmit(ev, inputId, boxId) {
  ev.preventDefault();
  const val = document.getElementById(inputId).value.trim();
  if (val) aiSend(val, boxId, inputId);
}


// ---------- Account menu (header profile dropdown) ----------
// Written against plain DOM rather than Bootstrap's dropdown so the menu
// behaves identically on the public pages and the dashboard, and so it does
// not depend on the Bootstrap bundle having finished loading.
(function () {
  const root = document.querySelector('[data-acct]');
  if (!root) return;
  const trigger = root.querySelector('[data-acct-trigger]');
  const menu = root.querySelector('.acct-menu');
  if (!trigger || !menu) return;

  const isOpen = () => trigger.getAttribute('aria-expanded') === 'true';

  function open() {
    menu.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
  }

  function close(refocus) {
    menu.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
    if (refocus) trigger.focus();
  }

  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    isOpen() ? close(false) : open();
  });

  // Click outside closes the menu; clicks inside must not bubble up to it.
  menu.addEventListener('click', (e) => e.stopPropagation());
  document.addEventListener('click', () => { if (isOpen()) close(false); });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && isOpen()) close(true);
  });

  // Roving arrow-key navigation over the menu's focusable controls.
  const items = () => Array.from(menu.querySelectorAll('.acct-item'));
  menu.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    e.preventDefault();
    const list = items();
    const at = list.indexOf(document.activeElement);
    const next = e.key === 'ArrowDown' ? at + 1 : at - 1;
    list[(next + list.length) % list.length].focus();
  });

  trigger.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      open();
      const list = items();
      if (list.length) list[0].focus();
    }
  });

  // Leaving the menu by tabbing past its last control should close it.
  root.addEventListener('focusout', (e) => {
    if (isOpen() && !root.contains(e.relatedTarget)) close(false);
  });
})();


// ---------- Collapsible sidebar ----------
// The collapsed class lives on <html> and is restored inline in the document
// head, so this only handles toggling, persistence and the labelling that the
// icon-only rail needs.
(function () {
  const toggle = document.getElementById('sidebarToggle');
  const sidebar = document.getElementById('appSidebar');
  if (!toggle || !sidebar) return;

  const root = document.documentElement;
  const STORAGE_KEY = 'ums.sidebar.collapsed';
  const isCollapsed = () => root.classList.contains('sidebar-collapsed');

  // In the rail there is no visible text, so each item needs a native tooltip.
  // They are added and removed with the state rather than left in the markup,
  // which would produce redundant tooltips over labels that are already legible.
  function syncTooltips() {
    const collapsed = isCollapsed();
    sidebar.querySelectorAll('.item').forEach((item) => {
      const label = item.querySelector('.item-label');
      if (!label) return;
      if (collapsed) {
        item.setAttribute('title', label.textContent.trim());
      } else {
        item.removeAttribute('title');
      }
    });
  }

  function sync() {
    const collapsed = isCollapsed();
    toggle.setAttribute('aria-expanded', String(!collapsed));
    toggle.setAttribute('aria-label', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
    syncTooltips();
  }

  toggle.addEventListener('click', () => {
    const collapsed = root.classList.toggle('sidebar-collapsed');
    try {
      localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0');
    } catch (e) { /* storage blocked — the choice just will not persist */ }
    sync();
  });

  sync();
})();

// ==========================================================================
// SYSTEM ENHANCEMENTS SUITE: CLIENT-SIDE INTERACTIVITY & UTILITIES
// ==========================================================================

// ---------- 1. TOAST NOTIFICATION ENGINE ----------
const UmsToast = {
  getContainer() {
    let container = document.getElementById('umsToastContainer');
    if (!container) {
      container = document.createElement('div');
      container.id = 'umsToastContainer';
      container.className = 'ums-toast-container';
      document.body.appendChild(container);
    }
    return container;
  },

  show(message, type = 'info', title = null, duration = 4200) {
    const container = this.getContainer();
    const toast = document.createElement('div');
    toast.className = `ums-toast toast-${type}`;

    const iconMap = {
      success: 'fa-circle-check',
      danger: 'fa-circle-xmark',
      warning: 'fa-triangle-exclamation',
      info: 'fa-circle-info'
    };
    const iconClass = iconMap[type] || 'fa-bell';
    const defaultTitle = type.charAt(0).toUpperCase() + type.slice(1);

    toast.innerHTML = `
      <i class="fa-solid ${iconClass} ums-toast-icon"></i>
      <div class="ums-toast-body">
        <div class="ums-toast-title">${title || defaultTitle}</div>
        <div class="ums-toast-msg">${message}</div>
      </div>
      <button type="button" class="ums-toast-close" aria-label="Close">&times;</button>
      <div class="ums-toast-progress"></div>
    `;

    const closeBtn = toast.querySelector('.ums-toast-close');
    const dismiss = () => {
      toast.classList.add('hiding');
      setTimeout(() => toast.remove(), 200);
    };

    closeBtn.addEventListener('click', dismiss);

    const progressBar = toast.querySelector('.ums-toast-progress');
    if (duration > 0) {
      progressBar.style.transition = `width ${duration}ms linear`;
      requestAnimationFrame(() => {
        progressBar.style.width = '0%';
      });
      setTimeout(dismiss, duration);
    } else {
      progressBar.remove();
    }

    container.appendChild(toast);
    return toast;
  }
};

// Global shorthand
window.umsToast = (msg, type, title, duration) => UmsToast.show(msg, type, title, duration);

// ---------- 2. INTERACTIVE CHARTS HELPER ----------
const UmsCharts = {
  exportPng(canvasOrId, filename = 'chart.png') {
    const canvas = typeof canvasOrId === 'string' ? document.getElementById(canvasOrId) : canvasOrId;
    if (!canvas) {
      UmsToast.show('Chart not found for export', 'warning');
      return;
    }
    try {
      const link = document.createElement('a');
      link.download = filename.endsWith('.png') ? filename : `${filename}.png`;
      link.href = canvas.toDataURL('image/png', 1.0);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      UmsToast.show('Chart downloaded as PNG image', 'success', 'Export Complete');
    } catch (e) {
      console.error('Chart export error:', e);
      UmsToast.show('Could not export chart image', 'danger');
    }
  },

  createGradient(ctx, colorHex, startAlpha = 0.35, endAlpha = 0.02) {
    if (!ctx) return colorHex;
    const gradient = ctx.createLinearGradient(0, 0, 0, 300);
    gradient.addColorStop(0, hexToRgba(colorHex, startAlpha));
    gradient.addColorStop(1, hexToRgba(colorHex, endAlpha));
    return gradient;
  },

  formatCurrency(val) {
    return 'KES ' + Number(val || 0).toLocaleString('en-KE');
  }
};

function hexToRgba(hex, alpha = 1) {
  const clean = hex.replace('#', '');
  const r = parseInt(clean.substring(0, 2), 16) || 108;
  const g = parseInt(clean.substring(2, 4), 16) || 92;
  const b = parseInt(clean.substring(4, 6), 16) || 231;
  return `rgba(${r},${g},${b},${alpha})`;
}

// ---------- 3. ADVANCED DATA TABLE SUITE ----------
const UmsTables = {
  initLiveFilter() {
    const searchInputs = document.querySelectorAll('[data-table-search]');
    searchInputs.forEach(input => {
      const targetSelector = input.getAttribute('data-table-search');
      const table = document.querySelector(targetSelector);
      if (!table) return;

      let debounceTimer = null;
      input.addEventListener('input', (e) => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
          const query = e.target.value.trim().toLowerCase();
          const rows = table.querySelectorAll('tbody tr');
          let visibleCount = 0;

          rows.forEach(row => {
            // Ignore empty rows or placeholder rows
            if (row.querySelector('td[colspan]')) return;
            const text = row.textContent.toLowerCase();
            const matches = !query || text.includes(query);
            row.style.display = matches ? '' : 'none';
            if (matches) visibleCount++;
          });

          const counter = document.querySelector(`[data-table-count="${targetSelector}"]`);
          if (counter) {
            counter.textContent = query ? `${visibleCount} found` : `${rows.length} total`;
          }
        }, 120);
      });
    });
  },

  initSortableHeaders() {
    document.querySelectorAll('.table-ums th.sortable').forEach(th => {
      if (!th.querySelector('.sort-caret')) {
        const caret = document.createElement('i');
        caret.className = 'fa-solid fa-sort sort-caret';
        th.appendChild(caret);
      }

      th.addEventListener('click', () => {
        const table = th.closest('table');
        const tbody = table?.querySelector('tbody');
        if (!tbody) return;

        const colIndex = Array.from(th.parentNode.children).indexOf(th);
        const currentAsc = th.classList.contains('asc');

        // Reset sibling headers
        th.parentNode.querySelectorAll('th.sortable').forEach(sibling => {
          sibling.classList.remove('asc', 'desc');
          const c = sibling.querySelector('.sort-caret');
          if (c) c.className = 'fa-solid fa-sort sort-caret';
        });

        const newAsc = !currentAsc;
        th.classList.toggle('asc', newAsc);
        th.classList.toggle('desc', !newAsc);

        const caret = th.querySelector('.sort-caret');
        if (caret) {
          caret.className = `fa-solid ${newAsc ? 'fa-sort-up' : 'fa-sort-down'} sort-caret`;
        }

        const rows = Array.from(tbody.querySelectorAll('tr')).filter(r => !r.querySelector('td[colspan]'));
        rows.sort((a, b) => {
          const valA = a.children[colIndex]?.textContent.trim().replace(/[KES,\s]/g, '') || '';
          const valB = b.children[colIndex]?.textContent.trim().replace(/[KES,\s]/g, '') || '';

          const numA = parseFloat(valA);
          const numB = parseFloat(valB);

          if (!isNaN(numA) && !isNaN(numB)) {
            return newAsc ? numA - numB : numB - numA;
          }
          return newAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
        });

        rows.forEach(r => tbody.appendChild(r));
      });
    });
  },

  exportCsv(tableOrSelector, filename = 'table_export.csv') {
    const table = typeof tableOrSelector === 'string' ? document.querySelector(tableOrSelector) : tableOrSelector;
    if (!table) {
      UmsToast.show('Table not found for export', 'warning');
      return;
    }

    const rows = Array.from(table.querySelectorAll('tr')).filter(r => r.style.display !== 'none');
    const csvLines = [];

    rows.forEach(row => {
      const cells = Array.from(row.querySelectorAll('th, td'));
      // skip checkbox-only or action columns if empty text
      const line = cells.map(cell => {
        // avoid checkbox content
        if (cell.querySelector('input[type="checkbox"]')) return null;
        let text = cell.innerText.trim().replace(/(\r\n|\n|\r)/gm, ' ').replace(/"/g, '""');
        return `"${text}"`;
      }).filter(v => v !== null).join(',');

      if (line) csvLines.push(line);
    });

    const blob = new Blob([csvLines.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename.endsWith('.csv') ? filename : `${filename}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(link.href);
    UmsToast.show(`Exported ${rows.length - 1} rows to CSV`, 'success', 'Export Complete');
  }
};

// ---------- 4. BULK OPERATIONS TOOLBAR ----------
const UmsBulkActions = {
  init() {
    const selectAllBoxes = document.querySelectorAll('input[type="checkbox"].bulk-select-all');
    selectAllBoxes.forEach(selectAll => {
      const table = selectAll.closest('table');
      if (!table) return;

      selectAll.addEventListener('change', () => {
        const rowBoxes = table.querySelectorAll('tbody tr:not([style*="display: none"]) input[type="checkbox"].bulk-select-row');
        rowBoxes.forEach(cb => {
          cb.checked = selectAll.checked;
          const tr = cb.closest('tr');
          if (tr) tr.classList.toggle('is-selected', selectAll.checked);
        });
        UmsBulkActions.updateToolbar(table);
      });
    });

    document.addEventListener('change', (e) => {
      if (e.target.matches('input[type="checkbox"].bulk-select-row')) {
        const tr = e.target.closest('tr');
        if (tr) tr.classList.toggle('is-selected', e.target.checked);
        const table = e.target.closest('table');
        if (table) UmsBulkActions.updateToolbar(table);
      }
    });

    // Bulk bar close button
    document.getElementById('bulkCloseBtn')?.addEventListener('click', () => {
      UmsBulkActions.clearSelection();
    });

    // Bulk Export CSV button
    document.getElementById('bulkExportCsvBtn')?.addEventListener('click', () => {
      const activeTable = document.querySelector('table.has-bulk-selection') || document.querySelector('.table-ums');
      if (activeTable) {
        UmsTables.exportCsv(activeTable, 'selected_records.csv');
      }
    });

    // Bulk Print button
    document.getElementById('bulkPrintBtn')?.addEventListener('click', () => {
      window.print();
    });
  },

  updateToolbar(table) {
    const selectedBoxes = document.querySelectorAll('input[type="checkbox"].bulk-select-row:checked');
    const bar = document.getElementById('bulkActionsBar');
    const countBadge = document.getElementById('bulkCountBadge');

    if (!bar) return;

    if (selectedBoxes.length > 0) {
      if (table) table.classList.add('has-bulk-selection');
      if (countBadge) countBadge.textContent = `${selectedBoxes.length} Selected`;
      bar.classList.add('visible');
    } else {
      if (table) table.classList.remove('has-bulk-selection');
      bar.classList.remove('visible');
    }
  },

  clearSelection() {
    document.querySelectorAll('input[type="checkbox"].bulk-select-all, input[type="checkbox"].bulk-select-row').forEach(cb => {
      cb.checked = false;
      const tr = cb.closest('tr');
      if (tr) tr.classList.remove('is-selected');
    });
    document.querySelectorAll('table.has-bulk-selection').forEach(t => t.classList.remove('has-bulk-selection'));
    document.getElementById('bulkActionsBar')?.classList.remove('visible');
  }
};

// ---------- 5. GLOBAL COMMAND PALETTE (CMD+K) ----------
const UmsCommandPalette = {
  isOpen: false,
  items: [],
  activeIndex: 0,

  init() {
    this.backdrop = document.getElementById('cmdPaletteBackdrop');
    this.input = document.getElementById('cmdPaletteInput');
    this.list = document.getElementById('cmdPaletteList');
    if (!this.backdrop || !this.input || !this.list) return;

    this.collectIndexableItems();

    // Trigger on Cmd+K or Ctrl+K
    document.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        this.open();
      } else if (e.key === 'Escape' && this.isOpen) {
        this.close();
      }
    });

    // Trigger when clicking search input or shortcut badge
    document.querySelector('.search-shortcut-badge')?.addEventListener('click', () => this.open());
    document.querySelector('.search input')?.addEventListener('focus', (e) => {
      // open command palette on topbar search click
      e.target.blur();
      this.open();
    });

    // Close on backdrop click
    this.backdrop.addEventListener('click', (e) => {
      if (e.target === this.backdrop) this.close();
    });

    // Input filtering
    this.input.addEventListener('input', (e) => {
      this.renderFilteredList(e.target.value);
    });

    // Arrow navigation & Enter execution
    this.input.addEventListener('keydown', (e) => {
      const renderedItems = Array.from(this.list.querySelectorAll('.cmd-item'));
      if (!renderedItems.length) return;

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        this.activeIndex = (this.activeIndex + 1) % renderedItems.length;
        this.updateActiveItem(renderedItems);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        this.activeIndex = (this.activeIndex - 1 + renderedItems.length) % renderedItems.length;
        this.updateActiveItem(renderedItems);
      } else if (e.key === 'Enter') {
        e.preventDefault();
        const active = renderedItems[this.activeIndex];
        if (active) active.click();
      }
    });
  },

  collectIndexableItems() {
    this.items = [];

    // Harvest links from sidebar
    document.querySelectorAll('#appSidebar a.item').forEach(link => {
      const text = link.querySelector('.item-label')?.textContent.trim() || link.textContent.trim();
      const href = link.getAttribute('href');
      const icon = link.querySelector('i')?.className || 'fa-solid fa-arrow-right';
      const group = link.closest('.nav-group')?.querySelector('.item-toggle .item-label')?.textContent.trim() || 'Navigation';

      if (href && href !== '#' && text) {
        this.items.push({
          title: text,
          category: group,
          url: href,
          icon: icon,
          sub: href
        });
      }
    });

    // Add quick administrative actions
    this.items.push(
      { title: 'New Student Registration', category: 'Quick Action', url: '/manage/admissions/', icon: 'fa-solid fa-user-plus', sub: 'Admissions Desk' },
      { title: 'Fee Invoices & Receipts', category: 'Quick Action', url: '/manage/fees/', icon: 'fa-solid fa-file-invoice-dollar', sub: 'Finance Desk' },
      { title: 'Examination Nominal Rolls', category: 'Quick Action', url: '/manage/academics/nominal-rolls/', icon: 'fa-solid fa-users-viewfinder', sub: 'Examinations' },
      { title: 'Provisional Transcripts', category: 'Quick Action', url: '/manage/academics/transcripts/provisional/', icon: 'fa-solid fa-file-invoice', sub: 'Academic Records' },
      { title: 'Course Quality Evaluations', category: 'Quick Action', url: '/manage/evaluation/', icon: 'fa-solid fa-star-half-stroke', sub: 'Quality Assurance' },
      { title: 'Recycle Bin & Data Recovery', category: 'System', url: '/manage/recycle-bin/', icon: 'fa-solid fa-trash-can-arrow-up', sub: 'System Admin' },
      { title: 'Audit Trails & Security Log', category: 'System', url: '/manage/audit-trails/', icon: 'fa-solid fa-shield-halved', sub: 'Security & Compliance' },
      { title: 'Ask AI Assistant', category: 'Intelligence', url: '/ai-assistant/', icon: 'fa-solid fa-robot', sub: 'UMS Intelligence' }
    );
  },

  open() {
    this.isOpen = true;
    this.backdrop.classList.add('open');
    this.input.value = '';
    this.activeIndex = 0;
    this.renderFilteredList('');
    setTimeout(() => this.input.focus(), 50);
  },

  close() {
    this.isOpen = false;
    this.backdrop.classList.remove('open');
  },

  renderFilteredList(query) {
    const q = query.trim().toLowerCase();
    const filtered = q
      ? this.items.filter(item => item.title.toLowerCase().includes(q) || item.category.toLowerCase().includes(q) || item.sub.toLowerCase().includes(q))
      : this.items.slice(0, 12);

    if (!filtered.length) {
      this.list.innerHTML = `
        <div class="text-center py-4 text-muted small">
          <i class="fa-solid fa-face-meh fa-2x mb-2 d-block opacity-50"></i>
          No matching pages or actions found for "${query}"
        </div>`;
      return;
    }

    // Group by category
    const groups = {};
    filtered.forEach(item => {
      groups[item.category] = groups[item.category] || [];
      groups[item.category].push(item);
    });

    let html = '';
    let globalIndex = 0;
    for (const [category, catItems] of Object.entries(groups)) {
      html += `<div class="cmd-group-label">${category}</div>`;
      catItems.forEach(item => {
        const isActive = globalIndex === this.activeIndex ? 'active' : '';
        html += `
          <a class="cmd-item ${isActive}" href="${item.url}" data-index="${globalIndex}">
            <div class="cmd-item-icon"><i class="${item.icon}"></i></div>
            <div class="cmd-item-body">
              <div class="cmd-item-title">${item.title}</div>
              <div class="cmd-item-sub">${item.sub}</div>
            </div>
            <i class="fa-solid fa-chevron-right text-muted small opacity-50"></i>
          </a>`;
        globalIndex++;
      });
    }

    this.list.innerHTML = html;
    this.activeIndex = 0;
  },

  updateActiveItem(renderedItems) {
    renderedItems.forEach((el, idx) => {
      el.classList.toggle('active', idx === this.activeIndex);
      if (idx === this.activeIndex) {
        el.scrollIntoView({ block: 'nearest' });
      }
    });
  }
};

// ---------- 6. INITIALIZATION HOOKS ----------
document.addEventListener('DOMContentLoaded', () => {
  UmsTables.initLiveFilter();
  UmsTables.initSortableHeaders();
  UmsBulkActions.init();
  UmsCommandPalette.init();

  // Mobile sidebar auto-close on link click
  if (window.innerWidth < 992) {
    document.querySelectorAll('#appSidebar a.item').forEach(link => {
      link.addEventListener('click', () => closeMobileSidebar());
    });
  }
});
