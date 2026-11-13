/* LUMO SHELL storefront — no dependencies */
(function () {
  'use strict';
  var C = window.LUMO_CONFIG;
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  /* ---------- helpers ---------- */
  var fmt = new Intl.NumberFormat(C.locale, { style: 'currency', currency: C.currency, maximumFractionDigits: 0 });
  var money = function (n) { return fmt.format(n); };
  var TIERS = C.priceByUpgradeCount;
  var PRICE = { base: TIERS[0], max: TIERS[TIERS.length - 1] };
  var esc = function (s) { return String(s).replace(/[&<>"']/g, function (c) { return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]; }); };

  function store(get, key, val) {
    try { return get ? JSON.parse(localStorage.getItem(key)) : localStorage.setItem(key, JSON.stringify(val)); }
    catch (e) { return null; }
  }

  /* ---------- static bindings ---------- */
  $$('[data-price]').forEach(function (el) { el.textContent = money(PRICE[el.getAttribute('data-price')]); });
  $('#yr').textContent = new Date().getFullYear();
  $('#gstNote').textContent = C.pricesIncludeGST ? ', GST inclusive' : '';
  $('#priceNote').textContent = 'All prices in ' + C.currency + (C.pricesIncludeGST ? ' and include GST.' : '.') +
    ' Delivery is additional unless stated at checkout.';

  var missing = [];
  $$('[data-cfg]').forEach(function (el) {
    var k = el.getAttribute('data-cfg'), v = C[k];
    if (v) { el.textContent = v; }
    else { el.textContent = '[add ' + k + ' in config.js]'; el.classList.add('todo'); if (missing.indexOf(k) < 0) missing.push(k); }
  });
  var banner = $('#draftBanner');
  if (banner && missing.length) { banner.hidden = false; $('#draftList', banner).textContent = missing.join(', '); }

  /* ---------- nav ---------- */
  var menuBtn = $('#menuBtn'), nav = $('#nav');
  menuBtn.addEventListener('click', function () {
    var open = nav.classList.toggle('open');
    menuBtn.setAttribute('aria-expanded', open);
  });
  $$('a', nav).forEach(function (a) { a.addEventListener('click', function () { nav.classList.remove('open'); menuBtn.setAttribute('aria-expanded', 'false'); }); });

  /* ---------- brake demo ---------- */
  var stage = $('#demoStage'), brakeBtn = $('#brakeBtn'), demoText = $('#demoText');
  var revertTimer = null, tick = null;
  function setBrake(on) { stage.classList.toggle('braking', on); }
  function press() {
    clearTimeout(revertTimer); clearInterval(tick);
    setBrake(true);
    demoText.textContent = 'Deceleration detected: brake light ON';
  }
  function release() {
    if (!stage.classList.contains('braking')) return;
    var left = 2;
    demoText.textContent = 'No deceleration: returning to normal in ' + left + 's';
    clearInterval(tick);
    tick = setInterval(function () {
      left -= 1;
      if (left > 0) demoText.textContent = 'No deceleration: returning to normal in ' + left + 's';
    }, 1000);
    revertTimer = setTimeout(function () {
      clearInterval(tick); setBrake(false); demoText.textContent = 'Riding normally';
    }, 2000);
  }
  brakeBtn.addEventListener('pointerdown', function (e) { e.preventDefault(); press(); });
  ['pointerup', 'pointerleave', 'pointercancel'].forEach(function (ev) { brakeBtn.addEventListener(ev, release); });
  brakeBtn.addEventListener('keydown', function (e) { if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) { e.preventDefault(); press(); } });
  brakeBtn.addEventListener('keyup', function (e) { if (e.key === ' ' || e.key === 'Enter') release(); });
  brakeBtn.addEventListener('contextmenu', function (e) { e.preventDefault(); });

  /* ---------- configurator ---------- */
  var state = { size: 'M', ups: {}, qty: 1 };
  var upList = $('#upgradeList'), sizeSel = $('#size'), qtyIn = $('#qty');

  upList.innerHTML = C.upgrades.map(function (u) {
    return '<label class="up"><input type="checkbox" value="' + esc(u.id) + '">' +
      '<span class="up-body"><strong>' + esc(u.name) + '</strong><small>' + esc(u.desc) + '</small></span>' +
      '</label>';
  }).join('');
  sizeSel.innerHTML = C.sizes.map(function (s) { return '<option' + (s === state.size ? ' selected' : '') + '>' + esc(s) + '</option>'; }).join('');
  if (C.sizes.indexOf(state.size) < 0) state.size = C.sizes[0];

  $('#ladder').textContent = 'Base ' + money(TIERS[0]) + ' · any 1 upgrade ' + money(TIERS[1]) + ' · any 2 upgrades ' + money(TIERS[2]) + ' · all 3 (MAX) ' + money(TIERS[3]);
  function chosen() { return C.upgrades.filter(function (u) { return state.ups[u.id]; }); }
  function unit() { return TIERS[chosen().length]; }
  function modelName() {
    var n = chosen().length;
    return n === C.upgrades.length ? C.base.name + ' MAX' : C.base.name;
  }
  function render() {
    var ups = chosen();
    $('#csName').textContent = modelName();
    $('#csMeta').textContent = 'Size ' + state.size + ' · ' + (ups.length ? ups.map(function (u) { return u.name; }).join(', ') : 'No upgrades');
    var total = unit() * state.qty;
    $('#csPrice').textContent = money(total);
    $('#csFine').textContent = state.qty > 1 ? money(unit()) + ' each' : (C.pricesIncludeGST ? 'GST inclusive' : '');
    $$('input', upList).forEach(function (i) { i.checked = !!state.ups[i.value]; });
    var all = ups.length === C.upgrades.length, none = ups.length === 0;
    $$('.preset').forEach(function (p) {
      var on = (p.dataset.preset === 'max' && all) || (p.dataset.preset === 'base' && none);
      p.setAttribute('aria-checked', on); p.classList.toggle('on', on);
    });
  }
  $$('.preset').forEach(function (p) {
    p.addEventListener('click', function () {
      state.ups = {};
      if (p.dataset.preset === 'max') C.upgrades.forEach(function (u) { state.ups[u.id] = true; });
      render();
    });
  });
  upList.addEventListener('change', function (e) { state.ups[e.target.value] = e.target.checked; render(); });
  sizeSel.addEventListener('change', function () { state.size = sizeSel.value; render(); });
  qtyIn.addEventListener('input', function () {
    var q = parseInt(qtyIn.value, 10);
    state.qty = isNaN(q) ? 1 : Math.max(1, Math.min(10, q));
    render();
  });
  qtyIn.addEventListener('blur', function () { qtyIn.value = state.qty; });

  /* ---------- cart ---------- */
  var KEY = 'lumo-cart-v1';
  var cart = (store(true, KEY) || []).filter(function (l) { return l && l.key && l.unit > 0 && l.qty > 0; });
  var drawer = $('#drawer'), scrim = $('#scrim'), lastFocus = null;

  function saveCart() { store(false, KEY, cart); }
  function count() { return cart.reduce(function (s, l) { return s + l.qty; }, 0); }
  function subtotal() { return cart.reduce(function (s, l) { return s + l.unit * l.qty; }, 0); }

  function drawCart() {
    $('#cartCount').textContent = count();
    $('#cartCount').classList.toggle('on', count() > 0);
    $('#cartSubtotal').textContent = money(subtotal());
    $('#checkoutBtn').disabled = cart.length === 0;
    $('#cartNote').textContent = 'Delivery ' + (C.shippingInfo ? 'is: ' + C.shippingInfo : 'is confirmed when we process your order') + '.' + (C.pricesIncludeGST ? ' Prices include GST.' : '');
    var body = $('#cartBody');
    if (!cart.length) { body.innerHTML = '<p class="empty">Your cart is empty.</p><a class="btn btn-ghost block" href="#models" data-close>Choose your LUMO SHELL</a>'; return; }
    body.innerHTML = cart.map(function (l, i) {
      return '<div class="line"><div class="line-info"><strong>' + esc(l.name) + '</strong>' +
        '<small>Size ' + esc(l.size) + (l.ups.length ? ' · ' + esc(l.ups.join(', ')) : ' · No upgrades') + '</small>' +
        '<div class="qty-ctl"><button type="button" data-act="dec" data-i="' + i + '" aria-label="Decrease quantity">−</button><span aria-label="Quantity">' + l.qty + '</span><button type="button" data-act="inc" data-i="' + i + '" aria-label="Increase quantity">+</button>' +
        '<button type="button" class="rm" data-act="rm" data-i="' + i + '">Remove</button></div></div>' +
        '<div class="line-price">' + money(l.unit * l.qty) + '</div></div>';
    }).join('');
  }
  $('#cartBody').addEventListener('click', function (e) {
    var t = e.target.closest('[data-act],[data-close]');
    if (!t) return;
    if (t.hasAttribute('data-close')) { closeCart(); return; }
    var i = +t.dataset.i, l = cart[i]; if (!l) return;
    if (t.dataset.act === 'inc') l.qty = Math.min(10, l.qty + 1);
    if (t.dataset.act === 'dec') l.qty = Math.max(1, l.qty - 1);
    if (t.dataset.act === 'rm') cart.splice(i, 1);
    saveCart(); drawCart();
  });

  function openCart() {
    lastFocus = document.activeElement;
    drawer.classList.add('open'); drawer.setAttribute('aria-hidden', 'false');
    scrim.hidden = false; document.body.classList.add('noscroll'); drawer.focus();
  }
  function closeCart() {
    drawer.classList.remove('open'); drawer.setAttribute('aria-hidden', 'true');
    scrim.hidden = true; document.body.classList.remove('noscroll');
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }
  $('#openCart').addEventListener('click', openCart);
  $('#closeCart').addEventListener('click', closeCart);
  scrim.addEventListener('click', closeCart);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && drawer.classList.contains('open')) closeCart(); });

  $('#addToCart').addEventListener('click', function () {
    var ups = chosen();
    var key = [modelName(), state.size, ups.map(function (u) { return u.id; }).join('+')].join('|');
    var found = cart.filter(function (l) { return l.key === key; })[0];
    if (found) found.qty = Math.min(10, found.qty + state.qty);
    else cart.push({ key: key, name: modelName(), size: state.size, ups: ups.map(function (u) { return u.name; }), unit: unit(), qty: state.qty });
    saveCart(); drawCart(); openCart();
  });

  /* ---------- checkout ---------- */
  var dlg = $('#checkoutDlg'), form = $('#orderForm');
  var formView = $('#dlgFormView'), doneView = $('#dlgDoneView'), foot = $('#dlgFoot'), err = $('#formError');

  function showErr(msg) { err.textContent = msg; err.hidden = !msg; }
  function openCheckout() {
    closeCart();
    formView.hidden = false; doneView.hidden = true; foot.hidden = false; showErr('');
    $('#dlgBlurb').textContent = C.checkoutBlurb;
    $('#orderLines').innerHTML = cart.map(function (l) {
      return '<div class="ol"><span>' + l.qty + ' × ' + esc(l.name) + ' <small>(' + esc(l.size) + (l.ups.length ? ', ' + esc(l.ups.join(', ')) : '') + ')</small></span><span>' + money(l.unit * l.qty) + '</span></div>';
    }).join('');
    $('#dlgTotal').textContent = money(subtotal()) + (C.pricesIncludeGST ? ' (GST incl.)' : '') + ' + delivery';
    if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
  }
  function closeDlg() { if (dlg.close) dlg.close(); else dlg.removeAttribute('open'); }
  $('#checkoutBtn').addEventListener('click', function () { if (cart.length) openCheckout(); });
  $('#closeDlg').addEventListener('click', closeDlg);
  dlg.addEventListener('click', function (e) { if (e.target === dlg) closeDlg(); });

  function done(html) { formView.hidden = true; foot.hidden = true; doneView.hidden = false; doneView.innerHTML = html; }

  form.addEventListener('submit', function (e) {
    e.preventDefault(); showErr('');
    var bad = $$('[required]', form).filter(function (f) { return f.type === 'checkbox' ? !f.checked : !f.value.trim(); })[0];
    if (bad) { showErr(bad.type === 'checkbox' ? 'Please accept the terms to continue.' : 'Please complete all required fields.'); bad.focus(); return; }
    var email = form.elements.email.value.trim();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) { showErr('Please enter a valid email address.'); form.elements.email.focus(); return; }

    var fd = {}; ['name', 'email', 'phone', 'address', 'suburb', 'state', 'postcode', 'country', 'notes'].forEach(function (k) { fd[k] = form.elements[k].value.trim(); });
    var order = {
      placedAt: new Date().toISOString(), currency: C.currency, pricesIncludeGST: C.pricesIncludeGST,
      customer: fd, subtotal: subtotal(),
      items: cart.map(function (l) { return { name: l.name, size: l.size, upgrades: l.ups, unitPrice: l.unit, qty: l.qty }; }),
      agreedToTerms: true
    };
    var summary = order.items.map(function (i) { return i.qty + ' x ' + i.name + ' (size ' + i.size + (i.upgrades.length ? ', ' + i.upgrades.join(', ') : '') + ') @ ' + money(i.unitPrice); }).join('\n');

    if (C.orderEndpoint) {
      var btn = $('#submitOrder'); btn.disabled = true; btn.textContent = 'Sending…';
      fetch(C.orderEndpoint, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' }, body: JSON.stringify(order) })
        .then(function (r) { if (!r.ok) throw new Error('bad status'); return r; })
        .then(function () {
          cart = []; saveCart(); drawCart();
          done('<h3>Order request received</h3><p>Thanks ' + esc(fd.name) + '. We will email <strong>' + esc(fd.email) + '</strong> to confirm your order and arrange payment. You have not been charged.</p><button class="btn btn-primary block" type="button" id="doneClose">Close</button>');
          $('#doneClose').addEventListener('click', closeDlg);
        })
        .catch(function () { showErr('Sorry, we could not send your order. Please try again.' + (C.contactEmail ? ' Or email ' + C.contactEmail + '.' : '')); })
        .then(function () { btn.disabled = false; btn.textContent = 'Submit order request'; });
    } else if (C.contactEmail) {
      var body = 'ORDER REQUEST\n\n' + summary + '\n\nSubtotal: ' + money(order.subtotal) + '\n\n' +
        'Name: ' + fd.name + '\nEmail: ' + fd.email + '\nPhone: ' + fd.phone + '\nAddress: ' + fd.address + ', ' + fd.suburb + ' ' + fd.state + ' ' + fd.postcode + ', ' + fd.country + '\nNotes: ' + fd.notes;
      window.location.href = 'mailto:' + encodeURIComponent(C.contactEmail) + '?subject=' + encodeURIComponent('LUMO SHELL order request') + '&body=' + encodeURIComponent(body);
      done('<h3>Almost done</h3><p>Your email app should have opened with your order request. <strong>Press send in that email to complete your request.</strong> You have not been charged.</p><button class="btn btn-primary block" type="button" id="doneClose">Close</button>');
      $('#doneClose').addEventListener('click', closeDlg);
    } else {
      showErr('Checkout is not connected yet. Store owner: set orderEndpoint or contactEmail in assets/js/config.js.');
    }
  });

  /* ---------- reveal on scroll ---------- */
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!reduce && 'IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (en) { if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target); } });
    }, { threshold: 0.12 });
    $$('.section-head, .steps li, .feature-grid article, .zone-key li, .safety article, .preset, .specs div').forEach(function (el) { el.classList.add('rv'); io.observe(el); });
  }

  render(); drawCart();
})();
