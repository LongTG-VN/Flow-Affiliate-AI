const form = document.getElementById('jobForm');
const generateButton = document.getElementById('generateButton');
const retryButton = document.getElementById('retryButton');
const errorBox = document.getElementById('errorBox');
const progressGrid = document.getElementById('progressGrid');
const assetGrid = document.getElementById('assetGrid');
const finalWrap = document.getElementById('finalWrap');
const finalEmpty = document.getElementById('finalEmpty');
const finalVideo = document.getElementById('finalVideo');
const downloadFinal = document.getElementById('downloadFinal');
const jobLabel = document.getElementById('jobLabel');
const healthBadge = document.getElementById('healthBadge');
const healthButton = document.getElementById('healthButton');

let currentJobId = localStorage.getItem('flowAffiliateJobId');
let pollTimer = null;

const stepDefs = [
  ['isolated_product', 'Tách sản phẩm', 'Flow image i2i'],
  ['character_wear', 'Mặc sản phẩm', 'Flow image i2i'],
  ['character_video', 'Video nhân vật', 'Flow video'],
  ['product_video', 'Video sản phẩm', 'Flow video'],
  ['voice_audio', 'Tạo voice', 'TTS'],
  ['final_video', 'Render final', 'FFmpeg'],
];

function previewFile(input, image) {
  const file = input.files?.[0];
  if (!file) return;
  image.src = URL.createObjectURL(file);
  image.classList.add('visible');
}

document.getElementById('character').addEventListener('change', (e) => {
  previewFile(e.target, document.getElementById('characterPreview'));
});
document.getElementById('product').addEventListener('change', (e) => {
  previewFile(e.target, document.getElementById('productPreview'));
});

function setError(message) {
  if (!message) {
    errorBox.textContent = '';
    errorBox.classList.add('hidden');
    return;
  }
  errorBox.textContent = message;
  errorBox.classList.remove('hidden');
}

function statusClass(done, active, errored) {
  if (errored) return 'error';
  if (done) return 'done';
  if (active) return 'active';
  return '';
}

function renderProgress(job) {
  const assets = job.assets || {};
  let firstMissing = stepDefs.findIndex(([key]) => !assets[key]);
  if (firstMissing < 0) firstMissing = stepDefs.length;
  progressGrid.innerHTML = stepDefs.map(([key, label, detail], index) => {
    const done = Boolean(assets[key]);
    const active = job.running && index === firstMissing;
    const errored = Boolean(job.web_error || job.error_message) && !job.running && index === firstMissing;
    const status = done ? 'Done' : active ? 'Đang chạy' : errored ? 'Lỗi' : 'Chờ';
    return `<div class="progress-item ${statusClass(done, active, errored)}">
      <span class="dot"></span>
      <strong>${label}</strong>
      <small>${detail} · ${status}</small>
    </div>`;
  }).join('');
}

function renderAssets(job) {
  const assets = job.assets || {};
  const entries = Object.entries(assets).filter(([key]) => key !== 'voice_audio' && key !== 'final_video');
  if (!entries.length) {
    assetGrid.className = 'asset-grid empty-state';
    assetGrid.textContent = 'Chưa có asset được tạo.';
    return;
  }
  assetGrid.className = 'asset-grid';
  const labels = {
    isolated_product: 'Sản phẩm đã tách',
    character_wear: 'Nhân vật mặc sản phẩm',
    character_video: 'Video nhân vật',
    product_video: 'Video sản phẩm',
  };
  assetGrid.innerHTML = entries.map(([key, url]) => {
    const media = key.includes('video')
      ? `<video src="${url}?v=${Date.now()}" controls muted playsinline></video>`
      : `<img src="${url}?v=${Date.now()}" alt="${labels[key] || key}">`;
    return `<article class="asset-card">${media}<div class="meta"><strong>${labels[key] || key}</strong><a href="${url}?download=true">Tải asset</a></div></article>`;
  }).join('');
}

function renderFinal(job) {
  const url = job.assets?.final_video;
  if (!url) {
    finalWrap.classList.add('hidden');
    finalEmpty.classList.remove('hidden');
    finalVideo.removeAttribute('src');
    return;
  }
  finalEmpty.classList.add('hidden');
  finalWrap.classList.remove('hidden');
  finalVideo.src = `${url}?v=${Date.now()}`;
  downloadFinal.href = `${url}?download=true`;
}

function renderJob(job) {
  jobLabel.textContent = job.job_id || 'Chưa có job';
  renderProgress(job);
  renderAssets(job);
  renderFinal(job);
  const message = job.web_error || job.error_message || '';
  setError(message);
  const canRetry = Boolean(message) && !job.running && (job.character_video_level || 3) < 3 && !job.assets?.character_video;
  retryButton.classList.toggle('hidden', !canRetry);
  generateButton.disabled = Boolean(job.running);
}

async function pollJob() {
  if (!currentJobId) return;
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(currentJobId)}`, { cache: 'no-store' });
    if (!response.ok) {
      if (response.status === 404) localStorage.removeItem('flowAffiliateJobId');
      return;
    }
    const job = await response.json();
    renderJob(job);
    if (job.running) {
      pollTimer = setTimeout(pollJob, 2000);
    } else if (!job.assets?.final_video && !job.web_error && !job.error_message) {
      pollTimer = setTimeout(pollJob, 2000);
    }
  } catch (error) {
    setError(`Không đọc được trạng thái job: ${error.message}`);
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  clearTimeout(pollTimer);
  setError('');
  generateButton.disabled = true;
  generateButton.textContent = 'Đang tạo job...';
  const data = new FormData(form);
  try {
    const response = await fetch('/api/jobs', { method: 'POST', body: data });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || 'Không tạo được job');
    currentJobId = payload.job_id;
    localStorage.setItem('flowAffiliateJobId', currentJobId);
    jobLabel.textContent = currentJobId;
    await pollJob();
  } catch (error) {
    setError(error.message);
    generateButton.disabled = false;
  } finally {
    generateButton.textContent = 'Generate video';
  }
});

retryButton.addEventListener('click', async () => {
  if (!currentJobId) return;
  retryButton.disabled = true;
  setError('');
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(currentJobId)}/retry`, { method: 'POST' });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || 'Không retry được job');
    await pollJob();
  } catch (error) {
    setError(error.message);
  } finally {
    retryButton.disabled = false;
  }
});

healthButton.addEventListener('click', async () => {
  healthBadge.className = 'health neutral';
  healthBadge.textContent = 'Đang kiểm tra...';
  try {
    const tts = document.getElementById('ttsProvider').value;
    const response = await fetch(`/api/health?tts_provider=${encodeURIComponent(tts)}`, { cache: 'no-store' });
    const health = await response.json();
    if (!response.ok) throw new Error(health.detail || 'Health check failed');
    const ok = health.flow.healthy && health.tts.healthy && health.ffmpeg.healthy;
    healthBadge.className = `health ${ok ? 'ok' : 'bad'}`;
    healthBadge.textContent = ok ? 'Core sẵn sàng' : 'Có core chưa sẵn sàng';
    if (!ok) {
      const details = [
        `Flow: ${health.flow.message || health.flow.healthy}`,
        `TTS: ${health.tts.message || health.tts.healthy}`,
        `FFmpeg: ${health.ffmpeg.healthy}`,
      ].join('\n');
      setError(details);
    } else {
      setError('');
    }
  } catch (error) {
    healthBadge.className = 'health bad';
    healthBadge.textContent = 'Health check lỗi';
    setError(error.message);
  }
});

renderProgress({ assets: {}, running: false });
if (currentJobId) {
  jobLabel.textContent = currentJobId;
  pollJob();
}
