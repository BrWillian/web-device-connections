/**
 * The browser's only two conversations with the server that are not a plain
 * page load.
 *
 * Everything over HTTP is same-origin against this app, authenticated by an
 * HttpOnly session cookie the browser attaches on its own — there is no token in
 * JavaScript reach. The only cross-origin traffic is the WebSocket to the relay,
 * and each upgrade carries a fresh one-shot grant fetched just before it opens.
 */
(function () {
  'use strict';

  var cfg = window.APP_CONFIG || {};
  var WS_BASE = (cfg.relayWsBase || '').replace(/\/+$/, '');

  function goToLogin() {
    if (window.location.pathname !== '/login') window.location.href = '/login';
  }

  /** Same-origin fetch that sends the session cookie and handles expiry once. */
  async function call(path, options) {
    var res = await fetch(path, Object.assign({ credentials: 'same-origin' }, options || {}));
    if (res.status === 401) {
      goToLogin();
      throw new Error('unauthorized');
    }
    return res;
  }

  /**
   * Open an authorized WebSocket to a device on the relay.
   * @param {string} path     e.g. '/terminal/device-01'
   * @param {string} deviceId
   * @param {string} scope    'terminal' | 'upload' | 'download'
   */
  async function openDeviceWS(path, deviceId, scope) {
    var body = new URLSearchParams({ device_id: deviceId, scope: scope });
    var res = await call('/ws-grant', { method: 'POST', body: body });
    if (!res.ok) throw new Error('Não foi possível autorizar a conexão.');

    var grant = (await res.json()).grant;
    return new WebSocket(WS_BASE + path + '?grant=' + encodeURIComponent(grant));
  }

  window.WDC = {
    call: call,
    openDeviceWS: openDeviceWS,
    wsBase: WS_BASE
  };
})();
