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

  /* ─── Filtro marketplace em tempo real ─── */
  const filterQ      = document.getElementById('filter-q');
  const filterCidade = document.getElementById('filter-cidade');
  const marketCards  = document.querySelectorAll('.market-card');

  function applyFilter() {
    const q      = (filterQ      ? filterQ.value.toLowerCase().trim()      : '');
    const cidade = (filterCidade ? filterCidade.value.toLowerCase().trim() : '');

    let visible = 0;
    marketCards.forEach(card => {
      const title    = (card.querySelector('.market-card-title')    || {}).textContent || '';
      const desc     = (card.querySelector('.market-card-desc')     || {}).textContent || '';
      const producer = (card.querySelector('.market-card-producer') || {}).textContent || '';
      const location = card.dataset.city || '';

      const matchQ      = !q      || title.toLowerCase().includes(q)      || desc.toLowerCase().includes(q) || producer.toLowerCase().includes(q);
      const matchCidade = !cidade || location.toLowerCase().includes(cidade);

      const show = matchQ && matchCidade;
      card.style.display = show ? '' : 'none';
      if (show) visible++;
    });

    // Mostra/esconde empty state
    let empty = document.getElementById('market-empty');
    if (marketCards.length > 0) {
      if (visible === 0) {
        if (!empty) {
          empty = document.createElement('div');
          empty.id = 'market-empty';
          empty.className = 'empty';
          empty.style.gridColumn = '1/-1';
          empty.innerHTML = '<p>Nenhum resultado encontrado para o filtro aplicado.</p>';
          marketCards[0].parentNode.appendChild(empty);
        }
        empty.style.display = '';
      } else if (empty) {
        empty.style.display = 'none';
      }
    }
  }

  if (filterQ || filterCidade) {
    // Filtro em tempo real com debounce de 200ms
    let debounce;
    [filterQ, filterCidade].forEach(input => {
      if (!input) return;
      input.addEventListener('input', () => {
        clearTimeout(debounce);
        debounce = setTimeout(applyFilter, 200);
      });
    });

    // Impede reload da página ao pressionar Enter no filtro
    const filterForm = document.getElementById('filter-form');
    if (filterForm) {
      filterForm.addEventListener('submit', (e) => {
        e.preventDefault();
        applyFilter();
      });
    }
  }

  /* ─── Adiciona data-city nos market cards para o filtro ─── */
  marketCards.forEach(card => {
    const cityEl = card.querySelector('[data-city]');
    if (!cityEl) {
      // Tenta pegar do span de cidade no header do card
      const spans = card.querySelectorAll('span');
      spans.forEach(span => {
        if (span.style && !span.classList.contains('card-tag') && !span.classList.contains('badge')) {
          if (!card.dataset.city) card.dataset.city = span.textContent;
        }
      });
    }
  });

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

});