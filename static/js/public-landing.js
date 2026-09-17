/**
 * Wigot School of Hospitality — Public Landing Page Animations & Interactivity
 * Built adhering to UMS web-animations and performance patterns.
 */
document.addEventListener('DOMContentLoaded', () => {
  // 0. Hero Background Image Progressive Loader & Cinematic Reveal
  const heroBackdrop = document.getElementById('pubHeroBackdrop');
  const heroImg = document.getElementById('pubHeroImg');

  if (heroBackdrop && heroImg) {
    function revealHeroImage() {
      heroBackdrop.classList.add('hero-loaded');
    }

    if (heroImg.complete && heroImg.naturalWidth > 0) {
      revealHeroImage();
    } else {
      heroImg.addEventListener('load', revealHeroImage, { once: true });
      heroImg.addEventListener('error', revealHeroImage, { once: true });
    }
  }

  // 1. Scroll-triggered animations via IntersectionObserver
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  
  if (!prefersReducedMotion) {
    const scrollObserver = new IntersectionObserver((entries, observer) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('visible');
          observer.unobserve(entry.target);
        }
      });
    }, {
      threshold: 0.1,
      rootMargin: '0px 0px -40px 0px'
    });

    document.querySelectorAll('.fade-up, .fade-left, .fade-right').forEach(el => {
      scrollObserver.observe(el);
    });
  } else {
    // If user prefers reduced motion, make all elements immediately visible
    document.querySelectorAll('.fade-up, .fade-left, .fade-right').forEach(el => {
      el.classList.add('visible');
    });
  }

  // 2. Animated Number Counters (requestAnimationFrame)
  function animateCounter(el, target, duration = 1600) {
    const start = performance.now();
    const from = 0;
    
    function step(now) {
      const progress = Math.min((now - start) / duration, 1);
      // ease-out cubic
      const ease = 1 - Math.pow(1 - progress, 3);
      const current = Math.floor(from + (target - from) * ease);
      el.textContent = current.toLocaleString();
      
      if (progress < 1) {
        requestAnimationFrame(step);
      } else {
        el.textContent = target.toLocaleString();
      }
    }
    requestAnimationFrame(step);
  }

  // Observe counters to trigger when scrolled into view
  const counterElements = document.querySelectorAll('[data-counter-target]');
  if (counterElements.length > 0) {
    const counterObserver = new IntersectionObserver((entries, observer) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          const el = entry.target;
          const target = parseInt(el.getAttribute('data-counter-target'), 10) || 0;
          animateCounter(el, target);
          observer.unobserve(el);
        }
      });
    }, { threshold: 0.2 });

    counterElements.forEach(el => counterObserver.observe(el));
  }

  // 3. Course Card Progressive Image Loading & Staggered Filter Animations
  const courseImgs = document.querySelectorAll('.course-card-img-wrap img');
  courseImgs.forEach(img => {
    const wrap = img.closest('.course-card-img-wrap');
    if (!wrap) return;

    function markImgLoaded() {
      wrap.classList.add('img-loaded');
    }

    if (img.complete && img.naturalWidth > 0) {
      markImgLoaded();
    } else {
      img.addEventListener('load', markImgLoaded, { once: true });
      img.addEventListener('error', () => {
        img.src = 'https://www.wigotschoolofhospitality.com/assets/images/items/MN-24261646379768.jpg';
        markImgLoaded();
      }, { once: true });
    }
  });

  // Populate category count chips dynamically
  const categoryCounts = {
    all: 0,
    diploma: 0,
    certificate: 0,
    part_time: 0,
    short_course: 0
  };

  const courseColumns = document.querySelectorAll('#courseCardsGrid .course-col');
  courseColumns.forEach(item => {
    const cat = item.getAttribute('data-course-category');
    categoryCounts.all++;
    if (cat && Object.prototype.hasOwnProperty.call(categoryCounts, cat)) {
      categoryCounts[cat]++;
    }
  });

  document.querySelectorAll('[data-count-cat]').forEach(chip => {
    const cat = chip.getAttribute('data-count-cat');
    if (cat && Object.prototype.hasOwnProperty.call(categoryCounts, cat)) {
      chip.textContent = categoryCounts[cat];
    }
  });

  // Staggered Cascade Course Filtering
  const filterBtns = document.querySelectorAll('.course-filter-btn');

  filterBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const selectedCat = btn.getAttribute('data-filter');

      // Toggle active state
      filterBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      // Animate matching cards with staggered timing
      let visibleIdx = 0;
      courseColumns.forEach(cardCol => {
        const itemCat = cardCol.getAttribute('data-course-category');
        const isMatch = (selectedCat === 'all' || itemCat === selectedCat);

        if (isMatch) {
          cardCol.style.display = '';
          cardCol.classList.remove('course-entering');
          cardCol.style.setProperty('--stagger-index', visibleIdx);
          // Trigger reflow to restart CSS animation
          void cardCol.offsetWidth;
          cardCol.classList.add('course-entering');
          visibleIdx++;
        } else {
          cardCol.classList.remove('course-entering');
          cardCol.style.display = 'none';
        }
      });
    });
  });

  // 4. Subtle Button Ripple Effect
  document.querySelectorAll('.btn-ripple').forEach(btn => {
    btn.addEventListener('click', e => {
      const ripple = document.createElement('span');
      const rect = btn.getBoundingClientRect();
      const size = Math.max(rect.width, rect.height);
      ripple.className = 'ripple';
      ripple.style.cssText = `
        position: absolute;
        border-radius: 50%;
        background: rgba(255,255,255,0.35);
        pointer-events: none;
        width: ${size}px;
        height: ${size}px;
        left: ${e.clientX - rect.left - size / 2}px;
        top: ${e.clientY - rect.top - size / 2}px;
        transform: scale(0);
        animation: rippleAnim .45s linear;
      `;
      btn.style.position = 'relative';
      btn.style.overflow = 'hidden';
      btn.appendChild(ripple);
      ripple.addEventListener('animationend', () => ripple.remove());
    });
  });

  // 5. Interactive Student Welfare Tabs
  const welfareTabBtns = document.querySelectorAll('.welfare-tab-btn');
  const welfarePanes = document.querySelectorAll('.welfare-pane');

  welfareTabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const targetId = btn.getAttribute('data-target');
      
      welfareTabBtns.forEach(b => {
        b.classList.remove('btn-primary', 'active');
        b.classList.add('btn-outline-secondary');
      });
      btn.classList.remove('btn-outline-secondary');
      btn.classList.add('btn-primary', 'active');

      welfarePanes.forEach(pane => {
        if (pane.id === `pane-${targetId}`) {
          pane.style.display = 'block';
          pane.style.opacity = '0';
          pane.style.transform = 'translateY(8px)';
          setTimeout(() => {
            pane.style.transition = 'opacity .3s ease, transform .3s ease';
            pane.style.opacity = '1';
            pane.style.transform = 'none';
          }, 20);
        } else {
          pane.style.display = 'none';
        }
      });
    });
  });

  // 6. Interactive Career Pathfinder Links
  document.querySelectorAll('[data-path-filter]').forEach(pathBtn => {
    pathBtn.addEventListener('click', (e) => {
      e.preventDefault();
      const filterTarget = pathBtn.getAttribute('data-path-filter');
      const coursesSection = document.getElementById('courses-explorer');
      
      if (coursesSection) {
        coursesSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }

      // Find matching filter button
      const matchingBtn = document.querySelector(`.course-filter-btn[data-filter="${filterTarget}"]`);
      if (matchingBtn) {
        matchingBtn.click();
      }
    });
  });

  // 7. Interactive Tuition & Installment Estimator
  const programSelect = document.getElementById('calcProgramSelect');
  const planButtons = document.querySelectorAll('.calc-plan-btn');
  const termTotalEl = document.getElementById('calcTermTotal');
  const installment1El = document.getElementById('calcInstallment1');
  const installment2El = document.getElementById('calcInstallment2');
  const installment3El = document.getElementById('calcInstallment3');
  const applyBtn = document.getElementById('calcApplyBtn');

  if (programSelect && termTotalEl) {
    let currentPlan = 'installments'; // 'full' or 'installments'

    function updateEstimator() {
      const selectedOption = programSelect.options[programSelect.selectedIndex];
      const baseFee = parseInt(selectedOption.getAttribute('data-fee'), 10) || 35000;
      const programCode = selectedOption.getAttribute('data-code') || 'DCA';

      if (currentPlan === 'full') {
        const discounted = Math.round(baseFee * 0.95);
        termTotalEl.textContent = `KES ${discounted.toLocaleString()}`;
        if (installment1El) installment1El.textContent = `KES ${discounted.toLocaleString()}`;
        if (installment2El) installment2El.textContent = `KES 0 (Settled in Full)`;
        if (installment3El) installment3El.textContent = `KES 0 (Settled in Full)`;
      } else {
        const inst1 = Math.round(baseFee * 0.40);
        const inst2 = Math.round(baseFee * 0.30);
        const inst3 = baseFee - inst1 - inst2;
        termTotalEl.textContent = `KES ${baseFee.toLocaleString()}`;
        if (installment1El) installment1El.textContent = `KES ${inst1.toLocaleString()}`;
        if (installment2El) installment2El.textContent = `KES ${inst2.toLocaleString()}`;
        if (installment3El) installment3El.textContent = `KES ${inst3.toLocaleString()}`;
      }

      if (applyBtn) {
        applyBtn.href = `/admissions/apply/?program=${encodeURIComponent(programCode)}`;
      }
    }

    programSelect.addEventListener('change', updateEstimator);

    planButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        planButtons.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentPlan = btn.getAttribute('data-plan');
        updateEstimator();
      });
    });

    updateEstimator();
  }

  // 8. Vanilla Lightbox Modal for Campus Photos & Facilities
  const lightboxOverlay = document.getElementById('pubLightbox');
  const lightboxImg = document.getElementById('pubLightboxImg');
  const lightboxCaption = document.getElementById('pubLightboxCaption');
  const lightboxClose = document.getElementById('pubLightboxClose');

  if (lightboxOverlay && lightboxImg) {
    document.querySelectorAll('[data-lightbox-src]').forEach(item => {
      item.addEventListener('click', (e) => {
        e.preventDefault();
        const src = item.getAttribute('data-lightbox-src');
        const title = item.getAttribute('data-lightbox-title') || 'Campus Facility';
        const tag = item.getAttribute('data-lightbox-tag') || 'Wigot School of Hospitality';

        lightboxImg.src = src;
        if (lightboxCaption) {
          lightboxCaption.innerHTML = `<strong>${title}</strong> <span class="badge bg-warning text-dark ms-2">${tag}</span>`;
        }
        lightboxOverlay.classList.add('active');
        document.body.style.overflow = 'hidden';
      });
    });

    function closeLightbox() {
      lightboxOverlay.classList.remove('active');
      document.body.style.overflow = '';
    }

    if (lightboxClose) {
      lightboxClose.addEventListener('click', closeLightbox);
    }

    lightboxOverlay.addEventListener('click', (e) => {
      if (e.target === lightboxOverlay) {
        closeLightbox();
      }
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && lightboxOverlay.classList.contains('active')) {
        closeLightbox();
      }
    });
  }

  // 9. Live Intake Countdown
  const countdownContainer = document.getElementById('heroCountdown');
  if (countdownContainer) {
    // Target next semester start (e.g. standard semester cycle)
    const now = new Date();
    let targetMonth = 0; // Jan
    let targetYear = now.getFullYear();

    if (now.getMonth() < 4) {
      targetMonth = 4; // May
    } else if (now.getMonth() < 8) {
      targetMonth = 8; // September
    } else {
      targetMonth = 0; // January next year
      targetYear += 1;
    }

    const targetDate = new Date(targetYear, targetMonth, 15, 8, 0, 0);

    function updateCountdown() {
      const diff = targetDate.getTime() - new Date().getTime();
      if (diff <= 0) {
        countdownContainer.innerHTML = '<span>Admissions In Session</span>';
        return;
      }
      const days = Math.floor(diff / (1000 * 60 * 60 * 24));
      const hours = Math.floor((diff / (1000 * 60 * 60)) % 24);
      const minutes = Math.floor((diff / 1000 / 60) % 60);

      countdownContainer.innerHTML = `Next Intake: <span class="countdown-unit">${days}d</span> : <span class="countdown-unit">${hours}h</span> : <span class="countdown-unit">${minutes}m</span>`;
    }

    updateCountdown();
    setInterval(updateCountdown, 60000);
  }

  // 10. 3D Card Tilt & Interactive Cursor Spotlight
  if (!prefersReducedMotion) {
    const tiltCards = document.querySelectorAll('.tilt-card');
    tiltCards.forEach(card => {
      card.addEventListener('mousemove', (e) => {
        const rect = card.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        const centerX = rect.width / 2;
        const centerY = rect.height / 2;
        const rotateX = ((y - centerY) / centerY) * -6;
        const rotateY = ((x - centerX) / centerX) * 6;

        card.style.transform = `perspective(1000px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) scale3d(1.015, 1.015, 1.015)`;
        card.style.setProperty('--mouse-x', `${x}px`);
        card.style.setProperty('--mouse-y', `${y}px`);
      });

      card.addEventListener('mouseleave', () => {
        card.style.transform = 'perspective(1000px) rotateX(0deg) rotateY(0deg) scale3d(1, 1, 1)';
      });
    });
  }

  // 11. Quick Curriculum Offcanvas Drawer
  const drawer = document.getElementById('courseDrawer');
  const drawerBackdrop = document.getElementById('courseDrawerBackdrop');
  const drawerClose = document.getElementById('courseDrawerClose');
  const drawerTitle = document.getElementById('drawerTitle');
  const drawerCategory = document.getElementById('drawerCategory');
  const drawerExam = document.getElementById('drawerExam');
  const drawerImg = document.getElementById('drawerImg');
  const drawerCode = document.getElementById('drawerCode');
  const drawerFee = document.getElementById('drawerFee');
  const drawerDuration = document.getElementById('drawerDuration');
  const drawerModules = document.getElementById('drawerModules');
  const drawerApplyBtn = document.getElementById('drawerApplyBtn');

  const moduleCatalogs = {
    culinary: [
      "Commercial Knife Skills & Precision Cuts (Julienne, Brunoise)",
      "Mother Sauces, Classic Stocks & French Culinary Reductions",
      "Garde Manger, Charcuterie, Terrines & Cold Kitchen Salad Bars",
      "Commercial Hot Line Cooking: Saute, Braise, Grill & Roast Stations",
      "Food Hygiene, HACCP Food Safety Compliance & Kitchen Sanitization",
      "Menu Engineering, Recipe Costing & Kitchen Inventory Control"
    ],
    pastry: [
      "Artisan Sourdough & Yeast Fermentation Masterclass",
      "French Viennoiserie: Laminated Croissants, Danishes & Brioche",
      "Modern Entremets, Mirror Glazes & Plated Restaurant Desserts",
      "Chocolate Tempering, Artisan Pralines & Truffles",
      "Tiered Celebration & Wedding Cake Design & Piping Techniques",
      "Pastry Lab Sanitation, Temperature Safety & Ingredient Science"
    ],
    hospitality: [
      "Opera Property Management System (PMS) Front Office Mastery",
      "Luxury Guest Relations, VIP Protocol & Concierge Operations",
      "Housekeeping Management & 5-Star Room Inspection Protocols",
      "Hospitality Law, Ethics & Guest Safety Administration",
      "Revenue Management, Room Night Forecasting & Auditing",
      "Supervisory Leadership & Human Resource Management"
    ],
    fb: [
      "Contemporary Restaurant Floor Management & Service Sequences",
      "Professional Barista Craft, Espresso Calibration & Latte Art",
      "Mixology, Cocktail Balancing & Bar Inventory Controls",
      "International Wine Regions, Cellar Storing & Table Pairing",
      "Banquet & Catering Logistics for Large-Scale Events",
      "Beverage Costing, Portion Standards & POS Terminal Operations"
    ],
    general: [
      "100% Practical Daily Commercial Kitchen / Service Stations",
      "Dual Credentials Examined by ICM (UK) & KNEC",
      "Guaranteed 5-Star Hotel Attachment Placement",
      "Professional Chef Whites & Grooming Standards",
      "Food Safety, Sanitation & Personal Kitchen Hygiene",
      "Direct Entrepreneurship & Restaurant Operations Mastery"
    ]
  };

  function openCourseDrawer(cardBtn) {
    if (!drawer) return;

    const code = cardBtn.getAttribute('data-peek-code') || 'DCA';
    const title = cardBtn.getAttribute('data-peek-title') || 'Hospitality Programme';
    const category = cardBtn.getAttribute('data-peek-category') || 'Diploma';
    const exam = cardBtn.getAttribute('data-peek-exam') || 'ICM (UK) & KNEC';
    const fee = cardBtn.getAttribute('data-peek-fee') || 'KES 35,000 / Term';
    const img = cardBtn.getAttribute('data-peek-img') || 'https://www.wigotschoolofhospitality.com/assets/images/items/MN-24261646379768.jpg';
    const credits = cardBtn.getAttribute('data-peek-credits') || '120';

    if (drawerTitle) drawerTitle.textContent = title;
    if (drawerCategory) drawerCategory.textContent = category;
    if (drawerExam) drawerExam.textContent = exam;
    if (drawerImg) drawerImg.src = img;
    if (drawerCode) drawerCode.textContent = code;
    if (drawerFee) drawerFee.textContent = fee;
    if (drawerDuration) drawerDuration.textContent = `${credits} Credits · 100% Practical`;
    if (drawerApplyBtn) {
      drawerApplyBtn.href = `/admissions/apply/?program=${encodeURIComponent(code)}`;
    }

    // Populate module list
    if (drawerModules) {
      drawerModules.innerHTML = '';
      let modules = moduleCatalogs.general;
      const lower = (title + ' ' + code).toLowerCase();

      if (lower.includes('pastry') || lower.includes('bakery') || lower.includes('cake')) {
        modules = moduleCatalogs.pastry;
      } else if (lower.includes('culinary') || lower.includes('cook') || lower.includes('chef')) {
        modules = moduleCatalogs.culinary;
      } else if (lower.includes('food & beverage') || lower.includes('barista') || lower.includes('beverage') || lower.includes('f&b')) {
        modules = moduleCatalogs.fb;
      } else if (lower.includes('hospitality') || lower.includes('hotel') || lower.includes('front office')) {
        modules = moduleCatalogs.hospitality;
      }

      modules.forEach(modText => {
        const li = document.createElement('li');
        li.className = 'module-item';
        li.innerHTML = `<i class="fa-solid fa-circle-check module-icon"></i><span>${modText}</span>`;
        drawerModules.appendChild(li);
      });
    }

    drawer.classList.add('active');
    if (drawerBackdrop) drawerBackdrop.classList.add('active');
    document.body.style.overflow = 'hidden';
  }

  function closeCourseDrawer() {
    if (drawer) drawer.classList.remove('active');
    if (drawerBackdrop) drawerBackdrop.classList.remove('active');
    document.body.style.overflow = '';
  }

  document.querySelectorAll('.course-peek-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      openCourseDrawer(btn);
    });
  });

  if (drawerClose) drawerClose.addEventListener('click', closeCourseDrawer);
  if (drawerBackdrop) drawerBackdrop.addEventListener('click', closeCourseDrawer);

  // 12. Magnetic Button Micro-Interactions
  if (!prefersReducedMotion) {
    const magneticBtns = document.querySelectorAll('.btn-magnetic, .pub-apply, .play-pulse-btn');
    magneticBtns.forEach(btn => {
      btn.addEventListener('mousemove', (e) => {
        const rect = btn.getBoundingClientRect();
        const x = e.clientX - rect.left - rect.width / 2;
        const y = e.clientY - rect.top - rect.height / 2;
        btn.style.transform = `translate(${x * 0.22}px, ${y * 0.22}px)`;
      });

      btn.addEventListener('mouseleave', () => {
        btn.style.transform = 'translate(0px, 0px)';
      });
    });
  }

  // 13. Interactive Day at Wigot Timeline Switcher
  const dayTimeBtns = document.querySelectorAll('.day-time-btn');
  const dayStepPanels = document.querySelectorAll('.day-step-panel');

  dayTimeBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const stepNum = btn.getAttribute('data-day-step');
      dayTimeBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      dayStepPanels.forEach(panel => {
        if (panel.id === `dayStep${stepNum}`) {
          panel.style.display = 'block';
          panel.style.opacity = '0';
          panel.style.transform = 'translateY(10px)';
          setTimeout(() => {
            panel.style.transition = 'opacity 0.35s ease, transform 0.35s ease';
            panel.style.opacity = '1';
            panel.style.transform = 'none';
          }, 20);
        } else {
          panel.style.display = 'none';
        }
      });
    });
  });

  // 14. Golden Ember / Stardust Particle Canvas Animation
  const emberCanvas = document.getElementById('heroEmberCanvas');
  if (emberCanvas && !prefersReducedMotion) {
    const ctx = emberCanvas.getContext('2d');
    let animationFrameId = null;
    let width = 0;
    let height = 0;
    let mouse = { x: -1000, y: -1000, radius: 100 };

    function resizeCanvas() {
      const rect = emberCanvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = rect.width;
      height = rect.height;
      emberCanvas.width = width * dpr;
      emberCanvas.height = height * dpr;
      ctx.scale(dpr, dpr);
    }

    resizeCanvas();
    window.addEventListener('resize', resizeCanvas, { passive: true });

    emberCanvas.addEventListener('mousemove', (e) => {
      const rect = emberCanvas.getBoundingClientRect();
      mouse.x = e.clientX - rect.left;
      mouse.y = e.clientY - rect.top;
    });

    emberCanvas.addEventListener('mouseleave', () => {
      mouse.x = -1000;
      mouse.y = -1000;
    });

    const particleCount = 38;
    const particles = [];
    const colors = [
      'rgba(255, 230, 167, ', // warm gold light
      'rgba(214, 161, 58, ',  // royal gold
      'rgba(243, 200, 122, ', // golden amber
      'rgba(255, 255, 255, '  // stardust glimmer
    ];

    for (let i = 0; i < particleCount; i++) {
      particles.push({
        x: Math.random() * (width || 800),
        y: Math.random() * (height || 500),
        size: Math.random() * 2.2 + 0.8,
        speedY: -(Math.random() * 0.45 + 0.15),
        speedX: (Math.random() - 0.5) * 0.3,
        swayPhase: Math.random() * Math.PI * 2,
        swaySpeed: Math.random() * 0.015 + 0.005,
        alpha: Math.random() * 0.65 + 0.25,
        alphaSpeed: (Math.random() * 0.008 + 0.003) * (Math.random() > 0.5 ? 1 : -1),
        color: colors[Math.floor(Math.random() * colors.length)]
      });
    }

    let isVisible = true;
    const heroSection = document.querySelector('.pub-hero');
    if (heroSection) {
      const heroObserver = new IntersectionObserver((entries) => {
        isVisible = entries[0].isIntersecting;
        if (isVisible && !animationFrameId) {
          loop();
        }
      }, { threshold: 0.05 });
      heroObserver.observe(heroSection);
    }

    function loop() {
      if (!isVisible) {
        animationFrameId = null;
        return;
      }

      ctx.clearRect(0, 0, width, height);

      for (let i = 0; i < particles.length; i++) {
        const p = particles[i];

        // Animate position
        p.swayPhase += p.swaySpeed;
        p.x += Math.sin(p.swayPhase) * 0.4 + p.speedX;
        p.y += p.speedY;

        // Animate alpha pulse
        p.alpha += p.alphaSpeed;
        if (p.alpha > 0.85 || p.alpha < 0.15) {
          p.alphaSpeed = -p.alphaSpeed;
        }

        // Mouse avoidance nudge
        const dx = p.x - mouse.x;
        const dy = p.y - mouse.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < mouse.radius) {
          const force = (mouse.radius - dist) / mouse.radius;
          p.x += (dx / dist) * force * 1.5;
          p.y += (dy / dist) * force * 1.5;
        }

        // Reset if drifted off screen
        if (p.y < -10) {
          p.y = height + 10;
          p.x = Math.random() * width;
        }
        if (p.x < -10) p.x = width + 10;
        if (p.x > width + 10) p.x = -10;

        // Draw particle
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx.fillStyle = `${p.color}${Math.max(0, Math.min(1, p.alpha))})`;
        ctx.shadowBlur = p.size * 3;
        ctx.shadowColor = 'rgba(214, 161, 58, 0.4)';
        ctx.fill();
        ctx.shadowBlur = 0;
      }

      animationFrameId = requestAnimationFrame(loop);
    }

    loop();
  }

  // 15. Interactive Culinary Studio & Equipment Explorer
  const studioTabBtns = document.querySelectorAll('.studio-tab-btn');
  const studioPanels = document.querySelectorAll('.studio-panel');

  studioTabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const targetStation = btn.getAttribute('data-studio');
      studioTabBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      studioPanels.forEach(panel => {
        if (panel.id === `studio-${targetStation}`) {
          panel.style.display = 'block';
          panel.style.opacity = '0';
          panel.style.transform = 'translateY(12px)';

          // Animate meter bars inside this panel
          panel.querySelectorAll('.studio-meter-fill').forEach(fill => {
            const targetWidth = fill.getAttribute('data-fill-target') || '90%';
            fill.style.width = '0%';
            setTimeout(() => {
              fill.style.width = targetWidth;
            }, 60);
          });

          setTimeout(() => {
            panel.style.transition = 'opacity 0.35s ease, transform 0.35s ease';
            panel.style.opacity = '1';
            panel.style.transform = 'none';
          }, 20);
        } else {
          panel.style.display = 'none';
        }
      });
    });
  });

  // Initial trigger for any visible meter fills
  document.querySelectorAll('.studio-panel:not([style*="display: none"]) .studio-meter-fill').forEach(fill => {
    const targetWidth = fill.getAttribute('data-fill-target') || '90%';
    setTimeout(() => {
      fill.style.width = targetWidth;
    }, 200);
  });

  // 16. Interactive Course & Career Pathfinder Wizard
  const wizardProgress = document.getElementById('wizardProgressFill');
  const wizardSteps = document.querySelectorAll('.wizard-step');
  const wizardChoices = document.querySelectorAll('.wizard-choice-btn');
  const wizardResetBtn = document.getElementById('wizardResetBtn');

  if (wizardSteps.length > 0) {
    let wizardData = { passion: null, qualification: null };

    const programProfiles = {
      dca: {
        code: 'DCA',
        title: 'Diploma in Culinary Arts (ICM UK)',
        duration: '1 Year · 100% Practical Daily Kitchen',
        fee: 'KES 200,000 (Payable in Installments)',
        exam: 'Institute of Commercial Management (ICM UK)',
        image: 'https://www.wigotschoolofhospitality.com/assets/images/items/MN-24261646379768.jpg',
        why: 'Perfect match for culinary leadership. Hands-on classical French sauces, hot lines, and guaranteed 5-star hotel attachment.'
      },
      cfbps: {
        code: 'CFBPS',
        title: 'Certificate, Food and Beverage Production & Service (KNEC)',
        duration: '1.5 Years · Comprehensive Culinary & Service',
        fee: 'KES 229,000 (Payable in Installments)',
        exam: 'KNEC & TVETA Kenya Licensed',
        image: 'https://www.wigotschoolofhospitality.com/assets/images/items/YW-68711646379797.jpg',
        why: 'Ideal national foundation qualification leading directly to commis chef and restaurant floor supervisor roles.'
      },
      hdca: {
        code: 'HDCA',
        title: 'Higher Diploma in Culinary Arts (ICM UK)',
        duration: '2 Years · Executive Chef & Kitchen Operations',
        fee: 'KES 320,000 (Payable in Installments)',
        exam: 'Institute of Commercial Management (ICM UK)',
        image: 'https://www.wigotschoolofhospitality.com/assets/images/items/MN-24261646379768.jpg',
        why: 'Executive tier for aspiring sous-chefs, executive head chefs, and fine dining restaurateurs.'
      },
      pb: {
        code: 'PB',
        title: 'Pastry and Bakery (Basic, Intermediate, Advanced)',
        duration: '1 Month Intensive Modular Masterclass',
        fee: 'KES 38,000 per level',
        exam: 'Wigot Culinary Academy Executive Certificate',
        image: 'https://www.wigotschoolofhospitality.com/assets/images/items/AS-56101645008020.jpg',
        why: 'Artisan sourdough, French viennoiserie, chocolate tempering, and wedding cake artistry with instant commercial founder skills.'
      },
      dhm: {
        code: 'DHM',
        title: 'Diploma in Hospitality Management',
        duration: '2 Years · 5-Star Hotel Systems & Operations',
        fee: 'KES 249,000 (Payable in Installments)',
        exam: 'ICM (UK) & KNEC Approved',
        image: 'https://www.wigotschoolofhospitality.com/assets/images/items/LZ-33781646917154.jpg',
        why: 'Comprehensive hotel leadership: Opera PMS, luxury guest concierge, front office, and revenue management.'
      },
      chm: {
        code: 'CHM',
        title: 'Certificate in Hospitality Management',
        duration: '1 Year · Front Office & Rooms Division',
        fee: 'KES 135,000 (Payable in Installments)',
        exam: 'KNEC & TVETA Kenya Licensed',
        image: 'https://www.wigotschoolofhospitality.com/assets/images/items/YW-68711646379797.jpg',
        why: 'Fast-track practical entry into guest services, housekeeping administration, and hotel reception.'
      },
      dfbs: {
        code: 'DFBS',
        title: 'Diploma in Food and Beverage Service (ICM UK)',
        duration: '1 Year · Sommelier, Barista & Restaurant Service',
        fee: 'KES 165,000 (Payable in Installments)',
        exam: 'Institute of Commercial Management (ICM UK)',
        image: 'https://www.wigotschoolofhospitality.com/assets/images/items/VF-06321646379773.jpg',
        why: 'Specialty coffee extraction, mixology, wine pairing, banquet management, and high-end restaurant floor leadership.'
      }
    };

    function showWizardStep(stepNum) {
      wizardSteps.forEach(s => s.classList.remove('active'));
      const activeStep = document.getElementById(`wizardStep${stepNum}`);
      if (activeStep) activeStep.classList.add('active');

      if (wizardProgress) {
        const percent = stepNum === 1 ? '33.3%' : stepNum === 2 ? '66.6%' : '100%';
        wizardProgress.style.width = percent;
      }
    }

    function calculateMatch() {
      const p = wizardData.passion || 'culinary';
      const q = wizardData.qualification || 'c_minus';

      let matchedKey = 'dca';

      if (p === 'pastry') {
        matchedKey = 'pb';
      } else if (p === 'hospitality') {
        matchedKey = (q === 'c_minus' || q === 'professional') ? 'dhm' : 'chm';
      } else if (p === 'beverage') {
        matchedKey = 'dfbs';
      } else {
        if (q === 'professional') {
          matchedKey = 'hdca';
        } else if (q === 'd_plain' || q === 'kcpe') {
          matchedKey = 'cfbps';
        } else {
          matchedKey = 'dca';
        }
      }

      const match = programProfiles[matchedKey];
      if (match) {
        const titleEl = document.getElementById('wizardResultTitle');
        const codeEl = document.getElementById('wizardResultCode');
        const durEl = document.getElementById('wizardResultDuration');
        const feeEl = document.getElementById('wizardResultFee');
        const examEl = document.getElementById('wizardResultExam');
        const whyEl = document.getElementById('wizardResultWhy');
        const imgEl = document.getElementById('wizardResultImg');
        const applyBtn = document.getElementById('wizardResultApplyBtn');

        if (titleEl) titleEl.textContent = match.title;
        if (codeEl) codeEl.textContent = `Code: ${match.code}`;
        if (durEl) durEl.textContent = match.duration;
        if (feeEl) feeEl.textContent = match.fee;
        if (examEl) examEl.textContent = match.exam;
        if (whyEl) whyEl.textContent = match.why;
        if (imgEl) imgEl.src = match.image;
        if (applyBtn) {
          applyBtn.href = `/admissions/apply/?program=${encodeURIComponent(match.code)}`;
        }
      }

      showWizardStep(3);
    }

    wizardChoices.forEach(btn => {
      btn.addEventListener('click', () => {
        const step = parseInt(btn.getAttribute('data-step'), 10);
        const group = btn.closest('.wizard-choice-grid');
        group.querySelectorAll('.wizard-choice-btn').forEach(b => b.classList.remove('selected'));
        btn.classList.add('selected');

        if (step === 1) {
          wizardData.passion = btn.getAttribute('data-val');
          setTimeout(() => showWizardStep(2), 220);
        } else if (step === 2) {
          wizardData.qualification = btn.getAttribute('data-val');
          setTimeout(() => calculateMatch(), 220);
        }
      });
    });

    if (wizardResetBtn) {
      wizardResetBtn.addEventListener('click', () => {
        wizardData = { passion: null, qualification: null };
        wizardChoices.forEach(b => b.classList.remove('selected'));
        showWizardStep(1);
      });
    }
  }

  // 17. Virtual Campus Facility Tour Viewport
  const tourNavBtns = document.querySelectorAll('.tour-nav-btn');
  const tourStageImg = document.getElementById('tourStageImg');
  const tourStageTitle = document.getElementById('tourStageTitle');
  const tourStageDesc = document.getElementById('tourStageDesc');
  const tourChip1 = document.getElementById('tourChip1');
  const tourChip2 = document.getElementById('tourChip2');
  const tourChip3 = document.getElementById('tourChip3');

  const tourStops = {
    kitchen: {
      title: 'Commercial Production Kitchen & Hot Line',
      desc: 'European-standard heavy duty gas ranges, salamanders, extraction canopies, and 24 individual working stations in Mamboleo.',
      chip1: '24 Individual Bays',
      chip2: 'HACCP Level 5 Safety',
      chip3: 'Daily Mise en Place',
      img: 'https://www.wigotschoolofhospitality.com/assets/images/items/MN-24261646379768.jpg'
    },
    pastry: {
      title: 'Artisan Pastry, Viennoiserie & Chocolate Lab',
      desc: 'Air-conditioned bakery lab with Italian marble benches, stone deck convection ovens, and confectionery sprayers.',
      chip1: 'Marble Tempering Benches',
      chip2: 'Stone Deck Ovens',
      chip3: 'Sourdough Culture Station',
      img: 'https://www.wigotschoolofhospitality.com/assets/images/items/AS-56101645008020.jpg'
    },
    reception: {
      title: 'Executive Front Desk & Opera PMS Simulation',
      desc: 'Live hospitality management terminal suite where trainees master reservations, VIP protocols, and night auditing.',
      chip1: 'Oracle Opera PMS',
      chip2: 'RFID Keycard Terminal',
      chip3: 'Simulated Guest Folio',
      img: 'https://www.wigotschoolofhospitality.com/assets/images/items/VF-06321646379773.jpg'
    },
    lounge: {
      title: 'Barista Academy & Trainee Restaurant Lounge',
      desc: 'Espresso calibration bar, dual boilers, mixology station, and fine dining table silver service floor.',
      chip1: 'Dual Boiler Espresso',
      chip2: 'Sommelier Wine Station',
      chip3: 'Table Silver Service',
      img: 'https://www.wigotschoolofhospitality.com/assets/images/gallery/1647589563-IMG_9912.jpg'
    },
    gardens: {
      title: 'Hostels & Serene Tropical Gardens',
      desc: 'Secure residential campus living in Mamboleo with high-speed Wi-Fi, study lounges, and landscaped botanical gardens.',
      chip1: '24/7 Gated Security',
      chip2: 'High-Speed Wi-Fi',
      chip3: 'Walking Distance to Kitchens',
      img: 'https://www.wigotschoolofhospitality.com/assets/images/gallery/1647589570-IMG_8787.jpg'
    }
  };

  tourNavBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const stopKey = btn.getAttribute('data-tour');
      const stop = tourStops[stopKey];
      if (!stop) return;

      tourNavBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      if (tourStageImg) {
        tourStageImg.style.opacity = '0.3';
        tourStageImg.style.transform = 'scale(1.02)';
        setTimeout(() => {
          tourStageImg.src = stop.img;
          tourStageImg.style.opacity = '1';
          tourStageImg.style.transform = 'scale(1)';
        }, 180);
      }

      if (tourStageTitle) tourStageTitle.textContent = stop.title;
      if (tourStageDesc) tourStageDesc.textContent = stop.desc;
      if (tourChip1) tourChip1.innerHTML = `<i class="fa-solid fa-check text-warning"></i> ${stop.chip1}`;
      if (tourChip2) tourChip2.innerHTML = `<i class="fa-solid fa-shield-check text-success"></i> ${stop.chip2}`;
      if (tourChip3) tourChip3.innerHTML = `<i class="fa-solid fa-sparkles text-info"></i> ${stop.chip3}`;
    });
  });
});




