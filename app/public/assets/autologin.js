(function() {
  try {
    var params = new URLSearchParams(window.location.search);
    var tok = params.get('token') || params.get('t') || (window.location.hash ? window.location.hash.replace(/^[#&]token=/, '') : null);
    if (tok) {
      tok = tok.trim();
      localStorage.setItem('hlab_token', tok);
      sessionStorage.setItem('hlab_token', tok);
      window.__HLAB_TOKEN = tok;
      if (params.has('token') || params.has('t')) {
        params.delete('token');
        params.delete('t');
        var q = params.toString();
        var cleanUrl = window.location.origin + window.location.pathname + (q ? '?' + q : '') + (window.location.hash || '');
        window.history.replaceState({}, document.title, cleanUrl);
      }
    }
  } catch(e) {}
})();
