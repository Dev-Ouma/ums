---
name: web-animations
description: >-
  Web animations skill for the UMS project. Covers all animation types —
  CSS transitions, keyframes, scroll-triggered, text effects, card/image
  animations, micro-interactions, minimalist motion, glassmorphism, and
  JS-driven animation patterns. Includes the full UMS animation inventory
  from ums.css and how to extend it correctly. Activate when building or
  enhancing any animated UI element, page entry effect, or interactive motion.
---

# Web Animations Skill — UMS Project

---

## 1. Animation Philosophy (UMS Design Language)

The UMS design language uses **purposeful, minimal motion** — animation should:
- **Guide attention** to state changes (not distract)
- **Confirm interaction** (hover lift, toast slide-in)
- **Communicate hierarchy** (staggered entry sequences)
- **Never block** the user (transitions ≤ 300ms for responsive actions)
- **Respect `prefers-reduced-motion`** — always provide a no-animation fallback

**Standard easing functions used in UMS:**
```css
/* Snappy interactive feedback */
transition: all .15s ease;                           /* Nav links, buttons */
transition: all .22s cubic-bezier(.2,.8,.2,1);       /* Cards — smooth lift */
transition: all .3s cubic-bezier(.175,.885,.32,1.275); /* Spring — bulk bar */
transition: background .18s, border-color .18s;      /* Input focus states */
```

---

## 2. Existing UMS Keyframe Animations

All named `@keyframes` already defined in `static/css/ums.css`:

```css
/* 1. Ticker — infinite horizontal scroll (system announcements) */
@keyframes umsTicker {
  from { transform: translateX(100%); }
  to   { transform: translateX(-100%); }
}
@keyframes umsTickerReverse {
  from { transform: translateX(-100%); }
  to   { transform: translateX(100%); }
}

/* 2. Account menu drop-in */
@keyframes acctIn {
  from { opacity: 0; transform: translateY(-6px); }
  to   { opacity: 1; transform: none; }
}

/* 3. Toast notification slide-in from right */
@keyframes umsToastSlideIn {
  from { transform: translateX(50px); opacity: 0; }
  to   { transform: translateX(0);    opacity: 1; }
}
```

---

## 3. Existing UMS Animation Classes (ums.css inventory)

### Card Animations:
```css
/* All major card types get lift + shadow */
.feature-card, .stat-tile, .course-card, .dash-card {
  transition: transform .25s, box-shadow .25s;
}
.feature-card:hover, .course-card:hover {
  transform: translateY(-6px);
  box-shadow: var(--shadow);
}

/* Reusable card lift utility */
.card-ums {
  transition: transform .22s cubic-bezier(.2,.8,.2,1),
              box-shadow .22s cubic-bezier(.2,.8,.2,1),
              border-color .2s;
}
.card-ums:hover { box-shadow: 0 12px 32px rgba(26,26,64,.1); }
.card-lift:hover { transform: translateY(-3px); }

/* HOD action cards — stronger lift */
.hod-actions .card {
  transition: transform .22s ease, box-shadow .22s ease;
}
.hod-actions .card:hover {
  transform: translateY(-4px);
  box-shadow: 0 14px 30px rgba(26,26,64,.14);
}
```

### Glassmorphism Panel:
```css
/* Frosted glass surface — use on floating panels, modals, sidebars */
.glass-panel {
  background: rgba(255,255,255,.85);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border: 1px solid rgba(255,255,255,.6);
  box-shadow: 0 8px 32px rgba(31,38,135,.07);
}
```

### Sidebar Animations:
```css
/* Sidebar collapse/expand */
.sidebar { transition: transform .3s, width .22s ease; }
.main    { transition: margin-left .22s ease; }

/* Chevron rotation on submenu toggle */
.sidebar .item-toggle .chevron { transition: transform .2s ease; }
/* Add via JS: .chevron.open { transform: rotate(90deg); } */
```

### Toast Notification (JS-driven):
```css
.ums-toast { animation: umsToastSlideIn .25s cubic-bezier(.2,.8,.2,1); }
.ums-toast.hiding { opacity: 0; transform: translateX(50px); transition: transform .2s, opacity .2s; }
```

### Bulk Action Bar (spring-up):
```css
.bulk-actions-bar {
  transform: translateX(-50%) translateY(150%);
  opacity: 0;
  transition: transform .3s cubic-bezier(.175,.885,.32,1.275), opacity .2s;
}
.bulk-actions-bar.visible {
  transform: translateX(-50%) translateY(0);
  opacity: 1;
}
```

### Sortable Table Headers:
```css
.table-ums th.sortable { transition: opacity .15s, transform .15s; }
```

---

## 4. Adding New Animations — The Standard Process

### Step 1: Choose the right tool for the job

| Use case | Best approach |
|---|---|
| Hover state | CSS `transition` |
| Page entry / reveal | CSS `@keyframes` + `animation` or `IntersectionObserver` |
| Sequence / stagger | CSS `animation-delay` or `IntersectionObserver` + JS |
| Complex path / morph | `anime.js` (CDN: `https://unpkg.com/animejs@3/lib/anime.min.js`) |
| Scroll-triggered | `IntersectionObserver` + CSS class toggle |
| Loading skeleton | CSS `@keyframes` shimmer |
| Counter animation | Vanilla JS `requestAnimationFrame` |
| Lottie/SVG | Inline SVG + CSS `animation` |

### Step 2: Define in `ums.css` (never inline)
```css
/* ✓ Always add to ums.css — section comment first */
/* ---------- MY FEATURE ANIMATIONS ---------- */
@keyframes myFadeUp {
  from { opacity: 0; transform: translateY(20px); }
  to   { opacity: 1; transform: none; }
}
.my-element-enter { animation: myFadeUp .4s cubic-bezier(.2,.8,.2,1) both; }
```

### Step 3: Add CSP for any external animation library
If using `anime.js`, `gsap`, or `lottie` from CDN — update `config/settings.py`
before the template change (see `security-csp` skill).

---

## 5. CSS Text Animations

### Gradient Shimmer Text (already in ums.css — `brand-gradient`):
```css
.brand-gradient {
  font-weight: 700;
  background: linear-gradient(90deg, #6C5CE7, #e84393, #00b894);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
```

### Typewriter Effect (CSS only):
```css
@keyframes typewriter {
  from { width: 0; }
  to   { width: 100%; }
}
@keyframes blink {
  50% { border-color: transparent; }
}
.typewriter {
  overflow: hidden;
  white-space: nowrap;
  border-right: 3px solid var(--primary);
  width: 0;
  animation:
    typewriter 2.5s steps(40) .5s forwards,
    blink .75s step-end infinite;
}
```

### Fade-in Word by Word (CSS + JS stagger):
```html
<!-- Wrap each word in a span with staggered delay -->
<h1 class="stagger-words">Welcome to UMS</h1>
<script>
  document.querySelectorAll('.stagger-words').forEach(el => {
    el.innerHTML = el.textContent.split(' ')
      .map((w, i) => `<span style="animation-delay:${i * .1}s" class="word-reveal">${w}&nbsp;</span>`)
      .join('');
  });
</script>
```
```css
@keyframes wordReveal {
  from { opacity: 0; transform: translateY(12px); }
  to   { opacity: 1; transform: none; }
}
.word-reveal {
  display: inline-block;
  opacity: 0;
  animation: wordReveal .5s cubic-bezier(.2,.8,.2,1) forwards;
}
```

### Gradient Text Animate (moving gradient):
```css
@keyframes gradientShift {
  0%   { background-position: 0% 50%; }
  50%  { background-position: 100% 50%; }
  100% { background-position: 0% 50%; }
}
.animated-gradient-text {
  background: linear-gradient(270deg, #6C5CE7, #e84393, #00b894, #6C5CE7);
  background-size: 300% 300%;
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  animation: gradientShift 4s ease infinite;
}
```

### Number Counter Animate (JS):
```javascript
function animateCounter(el, target, duration = 1500) {
  const start = performance.now();
  const from = parseInt(el.textContent) || 0;
  requestAnimationFrame(function step(now) {
    const progress = Math.min((now - start) / duration, 1);
    const ease = 1 - Math.pow(1 - progress, 3); // ease-out cubic
    el.textContent = Math.floor(from + (target - from) * ease).toLocaleString();
    if (progress < 1) requestAnimationFrame(step);
  });
}
// Usage: animateCounter(document.querySelector('.stat-number'), 1482);
```

---

## 6. Card & Image Animations

### Tilt on hover (vanilla JS — no library):
```javascript
document.querySelectorAll('.card-tilt').forEach(card => {
  card.addEventListener('mousemove', e => {
    const rect = card.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width  - .5;
    const y = (e.clientY - rect.top)  / rect.height - .5;
    card.style.transform = `perspective(600px) rotateY(${x * 12}deg) rotateX(${-y * 12}deg) translateZ(8px)`;
  });
  card.addEventListener('mouseleave', () => {
    card.style.transform = '';
    card.style.transition = 'transform .4s ease';
  });
});
```

### Image reveal on scroll (IntersectionObserver):
```css
.img-reveal {
  opacity: 0;
  transform: scale(.96) translateY(16px);
  transition: opacity .5s ease, transform .5s cubic-bezier(.2,.8,.2,1);
}
.img-reveal.in-view {
  opacity: 1;
  transform: none;
}
```
```javascript
const observer = new IntersectionObserver(entries => {
  entries.forEach(e => { if (e.isIntersecting) e.target.classList.add('in-view'); });
}, { threshold: 0.15 });
document.querySelectorAll('.img-reveal').forEach(el => observer.observe(el));
```

### Skeleton loading shimmer:
```css
@keyframes shimmer {
  0%   { background-position: -200% 0; }
  100% { background-position:  200% 0; }
}
.skeleton {
  background: linear-gradient(90deg, #f0f0f8 25%, #e4e4f0 50%, #f0f0f8 75%);
  background-size: 200% 100%;
  animation: shimmer 1.4s infinite;
  border-radius: 6px;
}
.skeleton-text  { height: 1em;  width: 80%; margin-bottom: .5em; }
.skeleton-title { height: 1.5em; width: 50%; }
.skeleton-card  { height: 140px; width: 100%; }
```

---

## 7. Scroll-Triggered Animations (IntersectionObserver — No Library)

### Universal fade-up on scroll:
```css
.fade-up {
  opacity: 0;
  transform: translateY(28px);
  transition: opacity .55s ease, transform .55s cubic-bezier(.2,.8,.2,1);
}
.fade-up.visible { opacity: 1; transform: none; }

.fade-left  { opacity: 0; transform: translateX(-28px); transition: opacity .5s ease, transform .5s cubic-bezier(.2,.8,.2,1); }
.fade-right { opacity: 0; transform: translateX(28px);  transition: opacity .5s ease, transform .5s cubic-bezier(.2,.8,.2,1); }
.fade-left.visible, .fade-right.visible { opacity: 1; transform: none; }

/* Stagger via delay attribute */
[data-delay="1"] { transition-delay: .1s; }
[data-delay="2"] { transition-delay: .2s; }
[data-delay="3"] { transition-delay: .3s; }
[data-delay="4"] { transition-delay: .4s; }
```

```javascript
// Attach to any element with .fade-up, .fade-left, .fade-right
const scrollObserver = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      e.target.classList.add('visible');
      scrollObserver.unobserve(e.target); // fire once
    }
  });
}, { threshold: 0.12, rootMargin: '0px 0px -40px 0px' });

document.querySelectorAll('.fade-up, .fade-left, .fade-right')
        .forEach(el => scrollObserver.observe(el));
```

### Usage in Django templates:
```html
<div class="fade-up" data-delay="1">First card</div>
<div class="fade-up" data-delay="2">Second card</div>
<div class="fade-up" data-delay="3">Third card</div>
```

---

## 8. Minimalist Micro-Interaction Patterns

### Button press effect:
```css
.btn-press:active { transform: scale(.96); }
```

### Input focus glow:
```css
.input-glow:focus {
  outline: none;
  box-shadow: 0 0 0 3px rgba(108,92,231,.2);
  border-color: var(--primary);
  transition: box-shadow .18s, border-color .18s;
}
```

### Success checkmark draw (SVG):
```html
<svg class="checkmark" viewBox="0 0 52 52">
  <circle class="checkmark-circle" cx="26" cy="26" r="25" fill="none"/>
  <path class="checkmark-check" fill="none" d="M14 27l7 7 17-17"/>
</svg>
```
```css
@keyframes strokeDraw {
  100% { stroke-dashoffset: 0; }
}
.checkmark-circle {
  stroke: #00b894;
  stroke-width: 2;
  stroke-dasharray: 166;
  stroke-dashoffset: 166;
  animation: strokeDraw .6s cubic-bezier(.65,0,.45,1) forwards;
}
.checkmark-check {
  stroke: #00b894;
  stroke-width: 3;
  stroke-dasharray: 48;
  stroke-dashoffset: 48;
  animation: strokeDraw .3s cubic-bezier(.65,0,.45,1) .6s forwards;
}
```

### Ripple click effect:
```css
.btn-ripple { position: relative; overflow: hidden; }
.btn-ripple .ripple {
  position: absolute;
  border-radius: 50%;
  transform: scale(0);
  animation: ripple .4s linear;
  background: rgba(255,255,255,.35);
}
@keyframes ripple {
  to { transform: scale(4); opacity: 0; }
}
```
```javascript
document.querySelectorAll('.btn-ripple').forEach(btn => {
  btn.addEventListener('click', e => {
    const r = document.createElement('span');
    const rect = btn.getBoundingClientRect();
    const size = Math.max(rect.width, rect.height);
    r.className = 'ripple';
    r.style.cssText = `width:${size}px;height:${size}px;left:${e.clientX - rect.left - size/2}px;top:${e.clientY - rect.top - size/2}px`;
    btn.appendChild(r);
    r.addEventListener('animationend', () => r.remove());
  });
});
```

---

## 9. Anime.js Integration (when needed)

For complex multi-step, sequenced, or path animations, use `anime.js`.

### CSP Setup (must do first — see `security-csp` skill):
```python
# config/settings.py — add to script-src
"script-src 'self' https://unpkg.com",
```

### CDN include (in template block):
```html
{% block extra_js %}
<script src="https://unpkg.com/animejs@3/lib/anime.min.js"></script>
{% endblock %}
```

### Common Anime.js Patterns:

**Staggered card entry:**
```javascript
anime({
  targets: '.card-ums',
  translateY: [30, 0],
  opacity: [0, 1],
  delay: anime.stagger(80),         // 80ms between each
  duration: 500,
  easing: 'cubicBezier(.2,.8,.2,1)'
});
```

**Stat counter (anime.js version):**
```javascript
anime({
  targets: { value: 0 },
  value: 1482,
  round: 1,
  duration: 1500,
  easing: 'easeOutCubic',
  update: function(anim) {
    document.querySelector('.stat-value').textContent =
      Math.round(anim.animations[0].currentValue).toLocaleString();
  }
});
```

**SVG path draw:**
```javascript
anime({
  targets: '.path-draw',
  strokeDashoffset: [anime.setDashoffset, 0],
  easing: 'easeInOutSine',
  duration: 1500,
  delay: (el, i) => i * 250
});
```

**Pulse / heartbeat:**
```javascript
anime({
  targets: '.pulse-icon',
  scale: [1, 1.15, 1],
  duration: 800,
  loop: true,
  easing: 'easeInOutSine'
});
```

---

## 10. Reduced Motion — Accessibility Rule

**Always wrap animated styles with a no-motion fallback:**

```css
/* Default: animate */
.fade-up { opacity: 0; transform: translateY(28px); transition: opacity .55s ease, transform .55s ease; }
.fade-up.visible { opacity: 1; transform: none; }

/* Accessibility: skip animation if user prefers reduced motion */
@media (prefers-reduced-motion: reduce) {
  .fade-up { opacity: 1; transform: none; transition: none; }
  .animated-gradient-text { animation: none; }
  .skeleton { animation: none; background: #f0f0f8; }
  .system-ticker-track { animation: none !important; }
}
```

---

## 11. UMS Page Entry Sequence Template

Use this pattern for any new dashboard page or portal landing:

```html
<!-- Stat tiles — stagger in from bottom -->
<div class="row g-3 mb-4">
  <div class="col-md-3"><div class="card-ums card-lift p-3 fade-up" data-delay="1">...</div></div>
  <div class="col-md-3"><div class="card-ums card-lift p-3 fade-up" data-delay="2">...</div></div>
  <div class="col-md-3"><div class="card-ums card-lift p-3 fade-up" data-delay="3">...</div></div>
  <div class="col-md-3"><div class="card-ums card-lift p-3 fade-up" data-delay="4">...</div></div>
</div>

<!-- Main content — fade from left -->
<div class="card-ums fade-left" data-delay="1">...</div>

<!-- Sidebar content — fade from right -->
<div class="card-ums fade-right" data-delay="2">...</div>

<script>
// Include the IntersectionObserver scroll trigger from Section 7
</script>
```

---

## 12. Quick Reference: Animation Decision Tree

```
Is it a hover state?
  → Yes: use CSS transition (max 250ms)
  → No ↓

Does it happen on page load?
  → Yes: use CSS @keyframes + animation (entry animations)
  → No ↓

Does it trigger on scroll?
  → Yes: IntersectionObserver + CSS class toggle (Section 7)
  → No ↓

Is it a complex sequence, path, or morph?
  → Yes: anime.js (Section 9) — update CSP first
  → No: requestAnimationFrame counter or CSS transition
```
