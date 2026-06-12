const linkText = document.getElementById('linkText');
const pasteBtn = document.getElementById('pasteBtn');
const clearBtn = document.getElementById('clearBtn');
const startBtn = document.getElementById('startBtn');
const statusCard = document.getElementById('statusCard');
const statusText = document.getElementById('statusText');
const percent = document.getElementById('percent');
const barFill = document.getElementById('barFill');
const meta = document.getElementById('meta');
const logs = document.getElementById('logs');
const files = document.getElementById('files');

let timer = null;
let currentJob = null;

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/static/sw.js').catch(() => {});
}

function setBusy(busy){
  startBtn.disabled = busy;
  startBtn.textContent = busy ? '다운로드 중...' : '다운로드 시작';
}

function getMode(){
  return document.querySelector('input[name="mode"]:checked')?.value || 'video';
}

pasteBtn.addEventListener('click', async () => {
  try {
    const text = await navigator.clipboard.readText();
    linkText.value = text;
  } catch(e) {
    alert('브라우저가 자동 붙여넣기를 막았습니다. 길게 눌러 직접 붙여넣기 하세요.');
  }
});

clearBtn.addEventListener('click', () => {
  linkText.value = '';
  linkText.focus();
});

startBtn.addEventListener('click', async () => {
  const text = linkText.value.trim();
  if(!text){
    alert('링크를 먼저 붙여넣으세요.');
    return;
  }
  statusCard.classList.remove('hidden');
  statusText.textContent = '작업 등록중';
  percent.textContent = '0%';
  barFill.style.width = '0%';
  meta.textContent = '';
  logs.innerHTML = '';
  files.innerHTML = '';
  setBusy(true);

  try {
    const res = await fetch('/api/start', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({text, mode:getMode()})
    });
    const data = await res.json();
    if(!data.ok) throw new Error(data.error || '시작 실패');
    currentJob = data.job_id;
    poll(currentJob);
  } catch(e) {
    setBusy(false);
    statusText.textContent = '오류';
    meta.textContent = e.message;
  }
});

async function poll(jobId){
  clearTimeout(timer);
  try {
    const res = await fetch(`/api/status/${jobId}`);
    const data = await res.json();
    if(!data.ok) throw new Error(data.error || '상태 확인 실패');
    renderJob(data.job);
    if(['done','error'].includes(data.job.status)) {
      setBusy(false);
      return;
    }
    timer = setTimeout(() => poll(jobId), 1200);
  } catch(e) {
    setBusy(false);
    statusText.textContent = '오류';
    meta.textContent = e.message;
  }
}

function labelStatus(s){
  const map = {
    queued:'대기중', starting:'시작중', downloading:'다운로드 중', processing:'파일 정리 중', done:'완료', error:'오류'
  };
  return map[s] || s;
}

function renderJob(job){
  const p = Number(job.progress || 0);
  statusText.textContent = labelStatus(job.status);
  percent.textContent = `${p}%`;
  barFill.style.width = `${Math.max(0, Math.min(100, p))}%`;

  const metaParts = [];
  if(job.filename) metaParts.push(job.filename);
  if(job.speed) metaParts.push(job.speed);
  if(job.eta) metaParts.push(`남은 시간 ${job.eta}초`);
  if(job.error) metaParts.push(job.error);
  meta.textContent = metaParts.join(' · ');

  logs.innerHTML = (job.logs || []).map(x => `<div class="log-line">${escapeHtml(x)}</div>`).join('');

  if(job.files && job.files.length){
    files.innerHTML = job.files.map(f => `<a class="file-btn" href="${f.url}">휴대폰에 저장: ${escapeHtml(f.name)} ${f.size ? '('+f.size+')' : ''}</a>`).join('');
  }
}

function escapeHtml(str){
  return String(str).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}
