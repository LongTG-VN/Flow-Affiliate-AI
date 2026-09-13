const form = document.querySelector('#create-form');
const planButton = document.querySelector('#plan-button');
const progress = document.querySelector('#progress');
const statusBox = document.querySelector('#job-status');
const planSection = document.querySelector('#plan');
const planSummary = document.querySelector('#plan-summary');
const shotsBox = document.querySelector('#shots');
const approveButton = document.querySelector('#approve-button');
const retryButton = document.querySelector('#retry-button');
const resultSection = document.querySelector('#result');
const finalVideo = document.querySelector('#final-video');

let currentJobId = null;
let pollTimer = null;

function esc(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

async function jsonOrError(response) {
  let payload = {};
  try { payload = await response.json(); } catch (_) {}
  if (!response.ok) {
    const detail = payload.detail || `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return payload;
}

function showStatus(job) {
  progress.classList.remove('hidden');
  const completed = Object.values(job.shot_results || {}).filter(x => x.status === 'COMPLETED').length;
  const total = job.shot_plan?.total_shots || job.metadata?.total_shots || 0;
  const rows = [
    `job: ${job.job_id || currentJobId}`,
    `status: ${job.status || 'QUEUED'}`,
    `running: ${Boolean(job.running)}`,
  ];
  if (total) rows.push(`shots: ${completed}/${total}`);
  if (job.metadata?.estimated_total_credits !== undefined) {
    rows.push(`estimated credits: ${job.metadata.estimated_total_credits}`);
  }
  if (job.error_stage) rows.push(`error stage: ${job.error_stage}`);
  if (job.error_message) rows.push(`error: ${job.error_message}`);
  if (job.runner_error && job.runner_error !== job.error_message) rows.push(`runner: ${job.runner_error}`);
  statusBox.textContent = rows.join('\n');
}

function showPlan(job) {
  const plan = job.shot_plan;
  if (!plan?.shots?.length) return;
  planSection.classList.remove('hidden');
  planSummary.innerHTML = `
    <p>${esc(plan.creative_summary || '')}</p>
    <p><strong>${plan.total_shots} shots</strong> · estimated <strong>${esc(job.metadata?.estimated_total_credits ?? '?')} credits</strong></p>
  `;
  const estimates = job.metadata?.shot_credit_estimates || {};
  shotsBox.innerHTML = plan.shots.map(shot => {
    const runtime = job.shot_results?.[shot.shot_id] || {};
    return `
      <div class="shot">
        <div>
          <span class="pill">${esc(shot.shot_id)}</span>
          <span class="pill">${esc(shot.purpose)}</span>
          <span class="pill">${esc(shot.source_type)}</span>
          <span class="pill">${esc(shot.duration_seconds)}s</span>
          <span class="pill">${esc(estimates[shot.shot_id] ?? '?')} cr</span>
          ${runtime.status ? `<span class="pill">${esc(runtime.status)}</span>` : ''}
        </div>
        <div class="muted" style="margin-top:8px">${esc(shot.flow_prompt)}</div>
      </div>`;
  }).join('');

  const isRunning = Boolean(job.running);
  const complete = job.status === 'COMPLETED';
  approveButton.disabled = isRunning || complete;
  approveButton.textContent = isRunning ? 'Generating…' : (complete ? 'Completed' : 'Approve & Generate');

  const failedShot = Object.values(job.shot_results || {}).some(x => x.status === 'FAILED');
  retryButton.classList.toggle('hidden', !failedShot || isRunning || complete);
}

function showResult(job) {
  const url = job.asset_urls?.final_video;
  if (!url) return;
  resultSection.classList.remove('hidden');
  const next = `${url}?t=${Date.now()}`;
  if (finalVideo.src !== new URL(next, location.href).href) finalVideo.src = next;
}

async function refresh() {
  if (!currentJobId) return;
  try {
    const job = await jsonOrError(await fetch(`/api/jobs/${encodeURIComponent(currentJobId)}`));
    showStatus(job);
    showPlan(job);
    showResult(job);
    if (job.status === 'COMPLETED' && !job.running) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  } catch (error) {
    statusBox.textContent = String(error);
  }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(refresh, 1500);
  refresh();
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  planButton.disabled = true;
  planButton.textContent = 'Planning…';
  planSection.classList.add('hidden');
  resultSection.classList.add('hidden');
  try {
    const data = new FormData(form);
    if (!data.get('job_id')) data.delete('job_id');
    const payload = await jsonOrError(await fetch('/api/jobs', { method: 'POST', body: data }));
    currentJobId = payload.job_id;
    startPolling();
  } catch (error) {
    progress.classList.remove('hidden');
    statusBox.textContent = String(error);
  } finally {
    planButton.disabled = false;
    planButton.textContent = 'Create Plan';
  }
});

approveButton.addEventListener('click', async () => {
  if (!currentJobId) return;
  approveButton.disabled = true;
  try {
    await jsonOrError(await fetch(`/api/jobs/${encodeURIComponent(currentJobId)}/approve`, {
      method: 'POST',
      body: new FormData(),
    }));
    startPolling();
  } catch (error) {
    statusBox.textContent += `\n${String(error)}`;
    approveButton.disabled = false;
  }
});

retryButton.addEventListener('click', async () => {
  if (!currentJobId) return;
  retryButton.disabled = true;
  try {
    const data = new FormData();
    data.set('approve_paid_retry', 'true');
    await jsonOrError(await fetch(`/api/jobs/${encodeURIComponent(currentJobId)}/approve`, {
      method: 'POST',
      body: data,
    }));
    startPolling();
  } catch (error) {
    statusBox.textContent += `\n${String(error)}`;
    retryButton.disabled = false;
  }
});
