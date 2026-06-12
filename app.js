const $ = (id) => document.getElementById(id);
const linkText = $("linkText");
const pasteBtn = $("pasteBtn");
const clearBtn = $("clearBtn");
const startBtn = $("startBtn");
const statusCard = $("statusCard");
const statusText = $("statusText");
const percentText = $("percentText");
const barFill = $("barFill");
const resultCard = $("resultCard");
let mode = "video";
let timer = null;

document.querySelectorAll(".mode").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".mode").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    mode = btn.dataset.mode;
  });
});

pasteBtn.addEventListener("click", async () => {
  try {
    const text = await navigator.clipboard.readText();
    if (text) linkText.value = linkText.value ? `${linkText.value}\n${text}` : text;
  } catch (e) {
    linkText.focus();
    status("붙여넣기 권한을 허용하거나 길게 눌러 붙여넣으세요.", 0);
  }
});

clearBtn.addEventListener("click", () => {
  linkText.value = "";
  resultCard.innerHTML = "";
  statusCard.classList.add("hidden");
});

function status(text, percent){
  statusCard.classList.remove("hidden");
  statusText.textContent = text;
  percentText.textContent = `${percent}%`;
  barFill.style.width = `${percent}%`;
}

function fileSize(bytes){
  if (!bytes) return "";
  const units = ["B","KB","MB","GB"];
  let n = bytes, i = 0;
  while(n >= 1024 && i < units.length-1){ n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}

function renderFiles(files){
  resultCard.innerHTML = "";
  files.forEach(f => {
    const div = document.createElement("div");
    div.className = f.error ? "file fail" : "file";
    if (f.error) {
      div.innerHTML = `<div><strong>실패</strong><small>${escapeHtml(f.error)}</small></div>`;
    } else {
      div.innerHTML = `<div><strong>${escapeHtml(f.name)}</strong><small>${fileSize(f.size)}</small></div><a href="${f.url}" download rel="noopener">저장</a>`;
    }
    resultCard.appendChild(div);
  });
}

function escapeHtml(s){
  return String(s).replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;","\"":"&quot;"}[c]));
}

startBtn.addEventListener("click", async () => {
  const text = linkText.value.trim();
  if (!text) {
    status("링크를 넣으세요.", 0);
    return;
  }
  startBtn.disabled = true;
  resultCard.innerHTML = "";
  status("시작", 1);
  try {
    const res = await fetch("/api/download", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({text, mode})
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "실패");
    watch(data.job_id);
  } catch (e) {
    startBtn.disabled = false;
    status(e.message || "실패", 0);
  }
});

function watch(jobId){
  clearInterval(timer);
  timer = setInterval(async () => {
    try {
      const res = await fetch(`/api/job/${jobId}`);
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "상태 확인 실패");
      const job = data.job;
      status(job.message || job.status, job.progress || 0);
      renderFiles(job.files || []);
      if (job.status === "done") {
        clearInterval(timer);
        startBtn.disabled = false;
        status("완료", 100);
      }
    } catch (e) {
      clearInterval(timer);
      startBtn.disabled = false;
      status(e.message || "오류", 0);
    }
  }, 1000);
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
