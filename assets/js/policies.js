/* Fills {data-cfg} placeholders on the policies page from config.js */
(function () {
  'use strict';
  var C = window.LUMO_CONFIG, missing = [];
  Array.prototype.forEach.call(document.querySelectorAll('[data-cfg]'), function (el) {
    var k = el.getAttribute('data-cfg'), v = C[k];
    if (v) { el.textContent = v; }
    else { el.textContent = '[add ' + k + ' in config.js]'; el.classList.add('todo'); if (missing.indexOf(k) < 0) missing.push(k); }
  });
  var b = document.getElementById('draftBanner');
  if (b && missing.length) { b.hidden = false; document.getElementById('draftList').textContent = missing.join(', '); }
})();
