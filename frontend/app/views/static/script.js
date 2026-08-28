/* Dashboard behaviour — only what a browser has to do itself.
 *
 * The device cards are rendered by the server (templates/_device_cards.html);
 * this file drives the two things HTML cannot: streaming a file up to a device
 * and pulling one back down over a WebSocket.
 *
 * Depends on /config.js, /static/api.js and /static/common.js. */

// ---------- Helpers ----------

/* Errors coming back from a device carry `error_code`, a stable slug, plus an
 * English sentence in `error`. The slug is what gets translated here; the
 * sentence is the fallback for an older agent, or for a code added on the
 * device before this table caught up. */
const DEVICE_ERRORS = {
  canceled: 'Transferência cancelada.',
  checksum_mismatch: 'O arquivo chegou corrompido (checksum não confere).',
  checksum_missing: 'O dispositivo não calculou o checksum da transferência.',
  internal: 'Erro interno no dispositivo.',
  not_a_file: 'O caminho informado não é um arquivo.',
  not_found: 'O arquivo não existe no dispositivo.',
  no_path: 'Nenhum caminho foi informado.',
  path_not_allowed: 'Caminho fora dos diretórios permitidos no dispositivo.',
  read_error: 'O dispositivo não conseguiu ler o arquivo.',
  session_limit: 'O dispositivo já está com o número máximo de terminais abertos.',
  too_large: 'O arquivo excede o limite de tamanho aceito pelo dispositivo.',
  write_error: 'O dispositivo não conseguiu gravar o arquivo.',
};

function deviceErrorText(msg, fallback) {
  return DEVICE_ERRORS[msg && msg.error_code] || (msg && msg.error) || fallback;
}

function updateProgress(progressBar, current, total) {
  if (!progressBar || !total) return;
  const pct = Math.min(100, Math.round((current / total) * 100));
  progressBar.style.width = pct + '%';
  progressBar.setAttribute('aria-valuenow', String(pct));
  progressBar.textContent = pct + '%';
}

function resetProgress(progressBar) {
  if (!progressBar) return;
  progressBar.style.width = '0%';
  progressBar.setAttribute('aria-valuenow', '0');
  progressBar.textContent = '0%';
}

// Bytes allowed in the socket's send queue before the upload loop pauses.
const UPLOAD_HIGH_WATER = 1024 * 1024;

/** Wait until the socket has flushed down to `limit` bytes, or has closed. */
async function waitForDrain(ws, limit) {
  while (ws.readyState === WebSocket.OPEN && ws.bufferedAmount > limit) {
    await new Promise((r) => setTimeout(r, 50));
  }
}

async function computeSha256FromChunks(chunks) {
  const totalLen = chunks.reduce((acc, arr) => acc + arr.byteLength, 0);
  const merged = new Uint8Array(totalLen);
  let off = 0;
  for (const ab of chunks) {
    merged.set(new Uint8Array(ab), off);
    off += ab.byteLength;
  }
  const digest = await crypto.subtle.digest('SHA-256', merged.buffer);
  return Array.from(new Uint8Array(digest))
    .map(b => b.toString(16).padStart(2, '0')).join('').toLowerCase();
}

let currentUploadWS = null;
let currentDownloadWS = null;

// ---------- Card grid ----------
//
// The server owns the markup. This only asks for a freshly rendered grid and
// swaps it in, so there is no second copy of what a card looks like living in
// JavaScript that could drift from the template.
const searchInput = document.querySelector('.search-bar input[name="q"]');
let searchTimer = null;
let refreshing = false;

function currentQuery() {
  return searchInput ? searchInput.value.trim() : '';
}

async function refreshCards() {
  // A slow relay must not let requests pile up on top of each other.
  if (refreshing) return;
  refreshing = true;
  try {
    const res = await WDC.call('/partials/devices?q=' + encodeURIComponent(currentQuery()));
    if (!res.ok) return;
    const cards = document.getElementById('cards');
    if (cards) cards.innerHTML = await res.text();
  } catch (err) {
    // Offline or session expired; api.js has already redirected if it was a 401.
  } finally {
    refreshing = false;
  }
}

// ---------- Download (device -> browser) ----------
let downloadCurrentDevice = null;
let downloadExpectedSize = null;
let downloadFilename = 'download.bin';

function resetDownloadModal() {
  downloadExpectedSize = null;
  downloadFilename = 'download.bin';
  document.getElementById('downloadDeviceId').textContent = '';
  document.getElementById('downloadPath').value = '';
  resetProgress(document.getElementById('downloadProgressBar'));
  document.getElementById('confirmDownloadBtn').disabled = false;
}

function openDownloadModal(deviceId) {
  downloadCurrentDevice = deviceId;
  resetDownloadModal();
  document.getElementById('downloadDeviceId').textContent = deviceId;
  if (window.downloadModal) window.downloadModal.show();
  setTimeout(() => document.getElementById('downloadPath').focus(), 150);
}

async function startDownload(deviceId, path, progressBar, button) {
  const ws = await WDC.openDeviceWS(`/download/${encodeURIComponent(deviceId)}`, deviceId, 'download');

  return new Promise((resolve, reject) => {
    currentDownloadWS = ws;
    ws.binaryType = 'arraybuffer';

    const chunks = [];
    let expectedChecksum = null;
    let received = 0;
    let inactivityTimer = null;
    let settled = false;

    const finish = () => {
      if (inactivityTimer) { clearTimeout(inactivityTimer); inactivityTimer = null; }
      if (button) button.disabled = false;
      if (window.downloadModal) window.downloadModal.hide();
      // Closing matters: the relay's handler parks on receive() for the lifetime of
      // this socket and holds the device's active_downloads slot while it does. A
      // download that only hid the modal leaked one socket per side, per transfer.
      try { ws.close(); } catch (e) { /* already closing */ }
    };

    const resetInactivity = () => {
      if (inactivityTimer) clearTimeout(inactivityTimer);
      inactivityTimer = setTimeout(() => {
        if (settled) return;
        settled = true;
        showToast('Download sem atividade. Encerrando a conexão.', 'warning');
        finish();
        resolve();
      }, 15000);
    };

    ws.onopen = () => {
      ws.send(JSON.stringify({ path }));
      resetInactivity();
    };

    /**
     * Verify and hand over the file. Kept out of the message handler because the
     * digest is async: awaiting it *inside* onmessage does not stop the socket from
     * dispatching the next frame, so file_pull_end used to be able to overtake a
     * still-pending checksum and save the file with the comparison unfinished.
     */
    const finalize = async () => {
      if (settled) return;
      settled = true;

      let checksumOk = null;
      if (expectedChecksum) {
        try {
          checksumOk = expectedChecksum === await computeSha256FromChunks(chunks);
        } catch (e) {
          checksumOk = false;
        }
      }

      if (checksumOk === false) {
        showToast('Checksum não confere. Download descartado.', 'error');
        finish();
        reject(new Error('checksum_mismatch'));
        return;
      }

      saveBlob(new Blob(chunks), downloadFilename);
      showToast(
        checksumOk ? 'Download concluído (checksum verificado).' : 'Download concluído.',
        checksumOk ? 'success' : 'info'
      );
      finish();
      resolve();
    };

    const abort = (message, reason) => {
      if (settled) return;
      settled = true;
      showToast(message, 'error');
      finish();
      reject(new Error(reason));
    };

    // Deliberately not async: every frame must be handled in arrival order.
    ws.onmessage = (ev) => {
      resetInactivity();

      if (ev.data instanceof ArrayBuffer) {
        chunks.push(ev.data);
        received += ev.data.byteLength;
        updateProgress(progressBar, received, downloadExpectedSize);
        return;
      }
      if (typeof ev.data !== 'string') return;

      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }

      if (msg.error) {
        abort(deviceErrorText(msg, 'Erro no download.'), msg.error_code || msg.error);
      } else if (msg.type === 'file_pull_info') {
        downloadFilename = msg.filename || downloadFilename;
        downloadExpectedSize = msg.size || null;
      } else if (msg.type === 'file_pull_checksum') {
        expectedChecksum = (msg.sha256 || '').toLowerCase();
      } else if (msg.type === 'file_pull_end') {
        finalize();
      } else if (msg.type === 'file_pull_error') {
        abort(deviceErrorText(msg, 'Erro no download.'), 'file_pull_error');
      }
    };

    // Guarded, because finish() now closes the socket: an error or close arriving
    // after a successful hand-over must not paint an error toast over it.
    ws.onerror = () => abort('Erro ao iniciar o download.', 'ws error');

    ws.onclose = () => {
      if (currentDownloadWS === ws) currentDownloadWS = null;
      if (button) button.disabled = false;
      if (inactivityTimer) { clearTimeout(inactivityTimer); inactivityTimer = null; }
      // Closed before the device ever signalled the end of the file.
      abort('Download interrompido antes de terminar.', 'closed_early');
    };
  });
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// ---------- Upload (browser -> device) ----------
let uploadSelectedFile = null;
let uploadCurrentDevice = null;

function resetUploadModal() {
  uploadSelectedFile = null;
  document.getElementById('uploadDeviceId').textContent = '';
  document.getElementById('uploadFileName').textContent = 'nenhum';
  document.getElementById('uploadDest').value = '';
  document.getElementById('uploadDest').disabled = false;
  document.getElementById('selectFileBtn').disabled = false;
  resetProgress(document.getElementById('uploadProgressBar'));
  const sendBtn = document.getElementById('confirmUploadBtn');
  sendBtn.disabled = true;
  sendBtn.textContent = 'Enviar';
}

function openUploadModal(deviceId) {
  uploadCurrentDevice = deviceId;
  resetUploadModal();
  document.getElementById('uploadDeviceId').textContent = deviceId;
  if (window.uploadModal) window.uploadModal.show();
}

async function sendFileViaWS(file, deviceId, targetPath, progressBar, sendButton) {
  const ws = await WDC.openDeviceWS(`/file/${encodeURIComponent(deviceId)}`, deviceId, 'upload');

  return new Promise((resolve, reject) => {
    currentUploadWS = ws;
    ws.binaryType = 'arraybuffer';
    let confirmTimer = null;

    const finish = () => {
      if (confirmTimer) { clearTimeout(confirmTimer); confirmTimer = null; }
      if (sendButton) sendButton.disabled = false;
      if (window.uploadModal) window.uploadModal.hide();
    };

    ws.onopen = async () => {
      const meta = { filename: file.name, size: file.size };
      if (targetPath) meta.target_path = targetPath;
      ws.send(JSON.stringify(meta));

      const chunkSize = 64 * 1024;
      let offset = 0;
      try {
        while (offset < file.size) {
          if (ws.readyState !== WebSocket.OPEN) {
            throw new Error('conexão encerrada durante o envio');
          }
          const slice = file.slice(offset, Math.min(offset + chunkSize, file.size));
          ws.send(await slice.arrayBuffer());
          offset += chunkSize;

          // ws.send() only queues; it does not wait for the bytes to leave. Without
          // this pause the loop pushes the whole file into bufferedAmount at disk
          // speed, so the bar hits 100% while the transfer is barely started and
          // then appears frozen — and a large file is held twice in memory.
          await waitForDrain(ws, UPLOAD_HIGH_WATER);
          updateProgress(progressBar, offset - ws.bufferedAmount, file.size);
        }

        // Drain fully before announcing completion, so the bar reaching 100% means
        // the bytes are actually out and the only thing left is the device's ack.
        await waitForDrain(ws, 0);
        updateProgress(progressBar, file.size, file.size);
        ws.send(JSON.stringify({ type: 'file_complete' }));

        // Only start waiting for the device's acknowledgement once every byte is
        // out; timing from the start would punish large files on slow links.
        confirmTimer = setTimeout(() => {
          showToast('Arquivo enviado, sem confirmação do dispositivo. Verifique o destino.', 'warning');
          try { ws.close(); } catch (e) { /* already closing */ }
          finish();
          resolve();
        }, 60000);
      } catch (err) {
        finish();
        reject(err);
      }
    };

    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }

      if (msg.error) {
        showToast(deviceErrorText(msg, 'Falha no upload.'), 'error');
        finish();
        reject(new Error(msg.error_code || msg.error));
        return;
      }
      if (msg.type === 'file_put_ok') {
        showToast('Upload concluído com sucesso.', 'success');
        finish();
        try { ws.close(); } catch (e) { /* already closing */ }
        resolve();
      } else if (msg.type === 'file_put_error') {
        showToast(deviceErrorText(msg, 'Falha no upload.'), 'error');
        finish();
        try { ws.close(); } catch (e) { /* already closing */ }
        reject(new Error('file_put_error'));
      }
    };

    ws.onerror = () => {
      showToast('Erro ao enviar o arquivo.', 'error');
      finish();
      reject(new Error('ws error'));
    };

    ws.onclose = () => {
      if (currentUploadWS === ws) currentUploadWS = null;
      if (sendButton) sendButton.disabled = false;
      if (confirmTimer) { clearTimeout(confirmTimer); confirmTimer = null; }
    };
  });
}

// ---------- Wiring ----------
window.addEventListener('load', () => {
  // No session check here: the page is only served to a signed-in browser, and
  // an expired cookie surfaces as a 401 that api.js turns into a redirect.
  setInterval(refreshCards, 5000);

  if (searchInput) {
    // The form still submits normally without JavaScript — Python does the
    // filtering either way. This only spares the full page load while typing.
    searchInput.form.addEventListener('submit', (e) => e.preventDefault());
    searchInput.addEventListener('input', () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        const q = currentQuery();
        // Keep the address bar honest, so a reload or a bookmark shows the same
        // list. replaceState rather than pushState: typing is not navigation.
        history.replaceState(null, '', q ? '/?q=' + encodeURIComponent(q) : '/');
        refreshCards();
      }, 200);
    });
  }

  window.uploadModal = makeModal(document.getElementById('uploadModal'));
  window.downloadModal = makeModal(document.getElementById('downloadModal'));

  document.addEventListener('click', (e) => {
    // Resolve to the button, not the clicked node: these buttons wrap an <i>
    // icon, and a click landing on the icon reports the icon as the target.
    const button = e.target && e.target.closest ? e.target.closest('button') : null;
    if (!button) return;

    // Card buttons carry their device on the element the server rendered, so
    // the grid can be replaced wholesale without rebinding anything.
    const cardAction = button.dataset.action;
    if (cardAction === 'upload') { openUploadModal(button.dataset.device); return; }
    if (cardAction === 'download') { openDownloadModal(button.dataset.device); return; }

    const id = button.id;

    if (id === 'selectFileBtn') {
      document.getElementById('uploadFileInput').click();
    }

    if (id === 'confirmUploadBtn') {
      if (!uploadSelectedFile || !uploadCurrentDevice) return;
      const sendBtn = document.getElementById('confirmUploadBtn');
      sendBtn.disabled = true;
      document.getElementById('uploadDest').disabled = true;
      document.getElementById('selectFileBtn').disabled = true;
      sendFileViaWS(
        uploadSelectedFile,
        uploadCurrentDevice,
        document.getElementById('uploadDest').value.trim(),
        document.getElementById('uploadProgressBar'),
        sendBtn
      ).catch((err) => {
        if (err && err.message !== 'unauthorized') {
          if (window.uploadModal) window.uploadModal.hide();
        }
      });
    }

    if (id === 'confirmDownloadBtn') {
      const path = document.getElementById('downloadPath').value.trim();
      if (!path || !downloadCurrentDevice) return;
      const btn = document.getElementById('confirmDownloadBtn');
      btn.disabled = true;
      startDownload(
        downloadCurrentDevice,
        path,
        document.getElementById('downloadProgressBar'),
        btn
      ).catch((err) => {
        if (err && err.message !== 'unauthorized') {
          if (window.downloadModal) window.downloadModal.hide();
        }
      });
    }

    if (id === 'cancelUploadBtn' && currentUploadWS) {
      try {
        if (currentUploadWS.readyState === WebSocket.OPEN) {
          currentUploadWS.send(JSON.stringify({ type: 'file_cancel' }));
          currentUploadWS.close();
        }
      } catch (err) { /* already gone */ }
    }

    if (id === 'cancelDownloadBtn' && currentDownloadWS) {
      // Closing is enough: the server forwards a cancel to the device on disconnect.
      try { currentDownloadWS.close(); } catch (err) { /* already gone */ }
    }
  });

  document.addEventListener('change', (e) => {
    if (e.target && e.target.id === 'uploadFileInput') {
      const file = e.target.files && e.target.files[0];
      uploadSelectedFile = file || null;
      document.getElementById('uploadFileName').textContent = file ? file.name : 'nenhum';
      document.getElementById('confirmUploadBtn').disabled = !file;
    }
  });
});
