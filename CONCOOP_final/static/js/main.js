/* CONCOOP — main.js */

document.addEventListener('DOMContentLoaded', () => {

  /* ─── Flash messages: auto-dismiss após 5s ─── */
  document.querySelectorAll('.flash').forEach(el => {
    setTimeout(() => {
      el.style.transition = 'opacity .4s, max-height .4s, padding .4s, margin .4s';
      el.style.opacity = '0';
      el.style.maxHeight = '0';
      el.style.overflow = 'hidden';
      el.style.padding = '0';
      el.style.margin = '0';
    }, 5000);
  });

  /* ─── Nav link ativo ─── */
  const path = window.location.pathname;
  document.querySelectorAll('.nav-link, .nav-drawer .nav-link').forEach(link => {
    if (link.getAttribute('href') === path) {
      link.classList.add('active');
    }
  });

  /* ─── Hamburger menu ─── */
  const toggle = document.getElementById('nav-toggle');
  const drawer = document.getElementById('nav-drawer');

  if (toggle && drawer) {
    toggle.addEventListener('click', () => {
      const isOpen = drawer.classList.toggle('open');
      toggle.classList.toggle('open', isOpen);
      toggle.setAttribute('aria-expanded', isOpen);
      drawer.setAttribute('aria-hidden', !isOpen);
    });

    drawer.querySelectorAll('.nav-link').forEach(link => {
      link.addEventListener('click', () => {
        drawer.classList.remove('open');
        toggle.classList.remove('open');
        toggle.setAttribute('aria-expanded', 'false');
        drawer.setAttribute('aria-hidden', 'true');
      });
    });

    document.addEventListener('click', (e) => {
      if (!toggle.contains(e.target) && !drawer.contains(e.target)) {
        drawer.classList.remove('open');
        toggle.classList.remove('open');
        toggle.setAttribute('aria-expanded', 'false');
        drawer.setAttribute('aria-hidden', 'true');
      }
    });
  }

  /* ─── Mercado: filtro, ordenação e chips de cidade ─── */
  const filterQ        = document.getElementById('filter-q');
  const filterCidade   = document.getElementById('filter-cidade');
  const productGrid    = document.getElementById('product-grid');
  const productCards   = productGrid ? productGrid.querySelectorAll('.product-card') : document.querySelectorAll('.product-card');
  const marketSort     = document.getElementById('market-sort');
  const marketCountNum = document.getElementById('market-count-num');
  const cityChips      = document.querySelectorAll('.city-chip');
  let activeCityChip   = '';

  function parsePrice(raw) {
    if (!raw) return null;
    const n = parseFloat(String(raw).replace(/[^\d,.-]/g, '').replace(',', '.'));
    return isNaN(n) ? null : n;
  }

  function getVisibleCards() {
    return [...productCards].filter(card => !card.classList.contains('is-hidden'));
  }

  function updateMarketCount() {
    if (!marketCountNum) return;
    const visible = getVisibleCards().length;
    marketCountNum.textContent = visible;
  }

  function toggleEmptyState(visible) {
    let empty = document.getElementById('market-empty');
    if (productCards.length === 0) return;

    if (visible === 0) {
      if (!empty) {
        empty = document.createElement('div');
        empty.id = 'market-empty';
        empty.className = 'empty market-empty';
        empty.innerHTML = '<p>Nenhum resultado encontrado para o filtro aplicado.</p>';
        productGrid.appendChild(empty);
      }
      empty.style.display = '';
    } else if (empty) {
      empty.style.display = 'none';
    }
  }

  function applyFilter() {
    const q      = (filterQ      ? filterQ.value.toLowerCase().trim()      : '');
    const cidade = (filterCidade ? filterCidade.value.toLowerCase().trim() : '');
    const chip   = activeCityChip;

    let visible = 0;
    productCards.forEach(card => {
      const title    = card.dataset.title    || '';
      const desc     = card.dataset.desc     || '';
      const producer = card.dataset.producer || '';
      const city     = card.dataset.city     || '';

      const matchQ      = !q      || title.includes(q) || desc.includes(q) || producer.includes(q);
      const matchCidade = !cidade || city.includes(cidade);
      const matchChip   = !chip   || city.includes(chip);

      const show = matchQ && matchCidade && matchChip;
      card.classList.toggle('is-hidden', !show);
      if (show) visible++;
    });

    updateMarketCount();
    toggleEmptyState(visible);
    applySort();
  }

  function applySort() {
    if (!productGrid || !marketSort || productCards.length === 0) return;

    const mode = marketSort.value;
    const visible = getVisibleCards();
    const hidden  = [...productCards].filter(c => c.classList.contains('is-hidden'));

    visible.sort((a, b) => {
      switch (mode) {
        case 'oldest':
          return (a.dataset.date || '').localeCompare(b.dataset.date || '');
        case 'name-asc':
          return (a.dataset.title || '').localeCompare(b.dataset.title || '', 'pt');
        case 'name-desc':
          return (b.dataset.title || '').localeCompare(a.dataset.title || '', 'pt');
        case 'price-asc': {
          const pa = parsePrice(a.dataset.price);
          const pb = parsePrice(b.dataset.price);
          if (pa === null && pb === null) return 0;
          if (pa === null) return 1;
          if (pb === null) return -1;
          return pa - pb;
        }
        case 'price-desc': {
          const pa = parsePrice(a.dataset.price);
          const pb = parsePrice(b.dataset.price);
          if (pa === null && pb === null) return 0;
          if (pa === null) return 1;
          if (pb === null) return -1;
          return pb - pa;
        }
        default:
          return (b.dataset.date || '').localeCompare(a.dataset.date || '');
      }
    });

    [...visible, ...hidden].forEach(card => productGrid.appendChild(card));
  }

  if (filterQ || filterCidade) {
    let debounce;
    [filterQ, filterCidade].forEach(input => {
      if (!input) return;
      input.addEventListener('input', () => {
        clearTimeout(debounce);
        debounce = setTimeout(() => {
          applyFilter();
          if (categoryRows) applyCategoryRowFilter();
        }, 200);
      });
    });

    const filterForm = document.getElementById('filter-form');
    if (filterForm) {
      filterForm.addEventListener('submit', (e) => {
        e.preventDefault();
        applyFilter();
      });
    }
  }

  cityChips.forEach(chip => {
    chip.addEventListener('click', () => {
      cityChips.forEach(c => c.classList.remove('is-active'));
      chip.classList.add('is-active');
      activeCityChip = chip.dataset.city || '';
      if (filterCidade) filterCidade.value = activeCityChip ? chip.textContent.trim() : '';
      applyFilter();
      if (categoryRows) applyCategoryRowFilter();
    });
  });

  if (marketSort) {
    marketSort.addEventListener('change', () => applySort());
  }

  if (productCards.length > 0) {
    const params = new URLSearchParams(window.location.search);
    if (params.get('q') && filterQ) filterQ.value = params.get('q');
    if (params.get('cidade') && filterCidade) {
      filterCidade.value = params.get('cidade');
      const matchChip = [...cityChips].find(c => c.dataset.city === params.get('cidade').toLowerCase());
      if (matchChip) {
        cityChips.forEach(c => c.classList.remove('is-active'));
        matchChip.classList.add('is-active');
        activeCityChip = matchChip.dataset.city || '';
      }
    }
    applyFilter();
  }

  /* ─── Filtro legado para cards .market-card (serviços) ─── */
  const marketCards = document.querySelectorAll('.market-card');

  /* ─── Confirmação antes de deletar / ações destrutivas ─── */
  document.querySelectorAll('[data-confirm]').forEach(el => {
    el.addEventListener('click', (e) => {
      if (!confirm(el.dataset.confirm || 'Tem certeza?')) {
        e.preventDefault();
      }
    });
  });

  /* ─── Scroll suave para âncoras ─── */
  document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', (e) => {
      const target = document.querySelector(anchor.getAttribute('href'));
      if (target) {
        e.preventDefault();
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
  });

  /* ─── Category tabs + product row carousels ─── */
  const categoryTabs = document.querySelectorAll('.category-tab');
  const categoryRows = document.getElementById('category-rows');
  const allSection   = document.querySelector('.product-section[data-section="all"]');
  const dynamicRows  = document.getElementById('dynamic-category-rows');
  const categoryEmpty = document.getElementById('category-empty');
  let activeCategory = 'all';

  // Keyword maps for auto-categorisation
  const CATEGORY_MAP = {
    graos:     { label: '🌾 Grãos',       keywords: ['soja','milho','trigo','arroz','feijão','sorgo','aveia','cevada','girassol','canola','grão','cereal'] },
    hortalicas:{ label: '🥬 Hortaliças',  keywords: ['alface','couve','espinafre','cenoura','beterraba','cebola','alho','brócolis','repolho','pepino','tomate','abobrinha','pimentão','hortaliça','horta','vegetal','verdura'] },
    frutas:    { label: '🍎 Frutas',       keywords: ['maçã','uva','laranja','banana','morango','melão','melancia','pêssego','ameixa','pera','kiwi','abacaxi','manga','mamão','fruta','limão','bergamota'] },
    animais:   { label: '🐄 Animais',      keywords: ['boi','vaca','porco','frango','galinha','ovelha','cabra','peixe','ave','gado','suíno','bovino','equino','animal'] },
    derivados: { label: '🥛 Derivados',    keywords: ['leite','queijo','mel','ovo','manteiga','nata','iogurte','creme','banha','linguiça','salame','embutido','derivado','laticínio'] },
  };

  function detectCategory(title, desc) {
    const text = (title + ' ' + (desc || '')).toLowerCase();
    for (const [key, cfg] of Object.entries(CATEGORY_MAP)) {
      if (cfg.keywords.some(kw => text.includes(kw))) return key;
    }
    return 'outros';
  }

  function buildProductRowHTML(cards, rowId) {
    if (!dynamicRows) return;
    const wrap = document.createElement('div');
    wrap.className = 'product-row-wrap';
    const prev = document.createElement('button');
    prev.className = 'product-row-prev hidden';
    prev.setAttribute('aria-label', 'Anterior');
    prev.innerHTML = '<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path d="M15 18l-6-6 6-6"/></svg>';
    const row = document.createElement('div');
    row.className = 'product-row';
    row.id = 'row-' + rowId;
    const next = document.createElement('button');
    next.className = 'product-row-next';
    next.setAttribute('aria-label', 'Próximo');
    next.innerHTML = '<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path d="M9 18l6-6-6-6"/></svg>';
    cards.forEach(c => row.appendChild(c.cloneNode(true)));
    wrap.appendChild(prev);
    wrap.appendChild(row);
    wrap.appendChild(next);
    initRowScroll(wrap);
    return wrap;
  }

  function initRowScroll(wrap) {
    const row  = wrap.querySelector('.product-row');
    const prev = wrap.querySelector('.product-row-prev');
    const next = wrap.querySelector('.product-row-next');
    if (!row) return;
    function updateBtns() {
      if (!prev || !next) return;
      prev.classList.toggle('hidden', row.scrollLeft <= 4);
      next.classList.toggle('hidden', row.scrollLeft + row.clientWidth >= row.scrollWidth - 4);
    }
    if (prev) prev.addEventListener('click', () => { row.scrollBy({ left: -300, behavior: 'smooth' }); setTimeout(updateBtns, 350); });
    if (next) next.addEventListener('click', () => { row.scrollBy({ left:  300, behavior: 'smooth' }); setTimeout(updateBtns, 350); });
    row.addEventListener('scroll', updateBtns, { passive: true });
    updateBtns();
  }

  function buildSectionHeader(label, count) {
    return `
      <div class="product-section-title">
        ${label}
        <span class="product-section-count">${count}</span>
      </div>
      <button type="button" class="product-section-see-all">Ver mais</button>`;
  }

  // Build dynamic category sections from products already in #row-all
  function buildCategoryRows() {
    if (!dynamicRows) return;
    dynamicRows.innerHTML = '';
    const allCards = productGrid ? [...productGrid.querySelectorAll('.product-card')] : [...productCards];
    const allRow = document.getElementById('row-all');
    const grouped = {};

    if (allRow) {
      allRow.innerHTML = '';
      allCards.forEach(card => allRow.appendChild(card.cloneNode(true)));
    }

    allCards.forEach(card => {
      const cat = detectCategory(card.dataset.title || '', card.dataset.desc || '');
      if (!grouped[cat]) grouped[cat] = [];
      grouped[cat].push(card);
    });

    for (const [catKey, cfg] of Object.entries(CATEGORY_MAP)) {
      const cards = grouped[catKey];
      if (!cards || cards.length === 0) continue;
      const section = document.createElement('div');
      section.className = 'product-section';
      section.dataset.section = catKey;

      const header = document.createElement('div');
      header.className = 'product-section-header';
      header.innerHTML = buildSectionHeader(cfg.label, cards.length);
      const rowWrap = buildProductRowHTML(cards, catKey);

      section.appendChild(header);
      section.appendChild(rowWrap);
      const seeAll = header.querySelector('.product-section-see-all');
      const row = rowWrap ? rowWrap.querySelector('.product-row') : null;
      if (seeAll && row) {
        seeAll.addEventListener('click', () => {
          row.scrollBy({ left: row.clientWidth * 0.9, behavior: 'smooth' });
        });
      }
      dynamicRows.appendChild(section);
    }

    // "outros" section
    const outros = grouped['outros'];
    if (outros && outros.length > 0) {
      const section = document.createElement('div');
      section.className = 'product-section';
      section.dataset.section = 'outros';
      const header = document.createElement('div');
      header.className = 'product-section-header';
      header.innerHTML = buildSectionHeader('📦 Outros', outros.length);
      const rowWrap = buildProductRowHTML(outros, 'outros');
      section.appendChild(header);
      section.appendChild(rowWrap);
      const seeAll = header.querySelector('.product-section-see-all');
      const row = rowWrap ? rowWrap.querySelector('.product-row') : null;
      if (seeAll && row) {
        seeAll.addEventListener('click', () => {
          row.scrollBy({ left: row.clientWidth * 0.9, behavior: 'smooth' });
        });
      }
      dynamicRows.appendChild(section);
    }
  }

  // Filter category rows by active tab
  function filterCategoryRows(activeKey) {
    activeCategory = activeKey || 'all';
    applyCategoryRowFilter();
  }

  // Apply text/city filter to category row cards
  function applyCategoryRowFilter() {
    const q      = filterQ      ? filterQ.value.toLowerCase().trim()      : '';
    const cidade = filterCidade ? filterCidade.value.toLowerCase().trim() : '';
    const chip   = activeCityChip;

    const allCards = document.querySelectorAll('#category-rows .product-card');
    allCards.forEach(card => {
      const title    = card.dataset.title    || '';
      const desc     = card.dataset.desc     || '';
      const producer = card.dataset.producer || '';
      const city     = card.dataset.city     || '';
      const matchQ      = !q      || title.includes(q) || desc.includes(q) || producer.includes(q);
      const matchCidade = !cidade || city.includes(cidade);
      const matchChip   = !chip   || city.includes(chip);
      card.classList.toggle('is-row-hidden', !(matchQ && matchCidade && matchChip));
    });

    const sections = document.querySelectorAll('.product-section:not(.product-section--source)');
    let anyVisibleSection = false;
    sections.forEach(sec => {
      const visible = sec.querySelectorAll('.product-card:not(.is-row-hidden)').length;
      const countEl = sec.querySelector('.product-section-count');
      if (countEl) countEl.textContent = visible;

      const showByTab = activeCategory === 'all' || sec.dataset.section === activeCategory;
      const show = showByTab && visible > 0;
      sec.style.display = show ? '' : 'none';
      if (show) anyVisibleSection = true;
    });

    if (categoryEmpty) categoryEmpty.style.display = anyVisibleSection ? 'none' : '';

    const allVisible = document.querySelectorAll('#row-all .product-card:not(.is-row-hidden)').length;
    if (marketCountNum) marketCountNum.textContent = allVisible;
  }

  // Init
  if (categoryRows && allSection) {
    buildCategoryRows();
    applyCategoryRowFilter();

    // Category tab clicks
    categoryTabs.forEach(tab => {
      tab.addEventListener('click', () => {
        categoryTabs.forEach(t => t.classList.remove('is-active'));
        tab.classList.add('is-active');
        filterCategoryRows(tab.dataset.category);
      });
    });

    if (marketSort) {
      marketSort.addEventListener('change', () => {
        buildCategoryRows();
        applyCategoryRowFilter();
      });
    }
  }

  /* ─── Carrosséis ─── */
  document.querySelectorAll('.hero-carousel').forEach(carousel => {
    const track   = carousel.querySelector('.carousel-track');
    const slides  = carousel.querySelectorAll('.carousel-slide');
    const dots    = carousel.querySelectorAll('.carousel-dot');
    const prevBtn = carousel.querySelector('.carousel-prev');
    const nextBtn = carousel.querySelector('.carousel-next');
    if (!track || slides.length === 0) return;

    let current  = 0;
    let autoplay = null;
    const INTERVAL = 6000;

    function goTo(index) {
      current = (index + slides.length) % slides.length;
      track.style.transform = `translateX(-${current * 100}%)`;
      slides.forEach((slide, i) => {
        const active = i === current;
        slide.classList.toggle('is-active', active);
        slide.setAttribute('aria-hidden', !active);
      });
      dots.forEach((dot, i) => {
        const active = i === current;
        dot.classList.toggle('is-active', active);
        dot.setAttribute('aria-selected', active);
      });
    }

    function next() { goTo(current + 1); }
    function prev() { goTo(current - 1); }

    function startAutoplay() {
      stopAutoplay();
      autoplay = setInterval(next, INTERVAL);
    }
    function stopAutoplay() {
      if (autoplay) { clearInterval(autoplay); autoplay = null; }
    }

    if (prevBtn) prevBtn.addEventListener('click', () => { prev(); startAutoplay(); });
    if (nextBtn) nextBtn.addEventListener('click', () => { next(); startAutoplay(); });
    dots.forEach((dot, i) => {
      dot.addEventListener('click', () => { goTo(i); startAutoplay(); });
    });

    carousel.addEventListener('mouseenter', stopAutoplay);
    carousel.addEventListener('mouseleave', startAutoplay);
    carousel.addEventListener('focusin', stopAutoplay);
    carousel.addEventListener('focusout', startAutoplay);

    carousel.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowLeft')  { e.preventDefault(); prev(); startAutoplay(); }
      if (e.key === 'ArrowRight') { e.preventDefault(); next(); startAutoplay(); }
    });

    startAutoplay();
  });

});
