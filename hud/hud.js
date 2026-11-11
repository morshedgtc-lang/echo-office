/* EPU HUD — chat, voice, face, and live status. No build step, no dependencies. */
"use strict";

const $ = (id) => document.getElementById(id);
const log = $("log");

/* ------------------------------------------------------------------ chat */
function add(who, text, meta, kind) {
  const div = document.createElement("div");
  div.className = "msg " + (kind || who);
  div.innerHTML = '<div class="who"></div><div class="body"></div>' +
                  (meta ? '<div class="meta"></div>' : "");
  div.querySelector(".who").textContent = who || "EPU";
  div.querySelector(".body").textContent = text || "";
  if (meta) div.querySelector(".meta").textContent = meta;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

function meta(result) {
  return [result.kind, result.provider, result.model, result.ms + "ms"]
    .filter(Boolean).join(" // ").toUpperCase();
}

let speakEnabled = false;
let busy = false;

async function send(text) {
  const message = (text || "").trim();
  if (!message || busy) return;
  busy = true;
  add("YOU", message);
  try {
    const r = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, chat_id: "hud", scope: "owner" }),
    });
    const d = await r.json().catch(() => ({}));
    if (d.error) add("SYSTEM", d.error, "", "sys");
    else {
      add("EPU", d.reply, meta(d));
      if (speakEnabled) await fetch("/api/say", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: d.reply, agent: "you" }),
      });
    }
  } catch (err) {
    add("SYSTEM", String(err && err.message || err), "", "sys");
  }
  busy = false;
}

$("form").addEventListener("submit", (e) => {
  e.preventDefault();
  const value = $("input").value;
  $("input").value = "";
  send(value);
});

/* ------------------------------------------------------------------ speak */
$("btn-speak").addEventListener("click", () => {
  speakEnabled = !speakEnabled;
  $("btn-speak").textContent = "SPEAK: " + (speakEnabled ? "ON" : "OFF");
  $("btn-speak").classList.toggle("on", speakEnabled);
});

/* ------------------------------------------------------------------ recorder */
let media = null, rec = null, chunks = [], recording = false;

async function startRecording() {
  if (recording) return stopRecording();
  if (!window.MediaRecorder || !navigator.mediaDevices) {
    return add("SYSTEM", "this browser cannot record - use Chrome or Edge on http", "", "sys");
  }
  try {
    media = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    return add("SYSTEM", "mic denied: " + err.message, "", "sys");
  }
  const type = ["audio/webm;codecs=opus", "audio/webm", "audio/wav"].find((t) =>
    window.MediaRecorder.isTypeSupported(t)) || "";
  chunks = [];
  rec = new MediaRecorder(media, type ? { mimeType: type } : undefined);
  rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
  rec.onstop = async () => {
    media.getTracks().forEach((t) => t.stop());
    media = null;
    const blob = new Blob(chunks, { type: type || "audio/webm" });
    const base64 = await blobToBase64(blob);
    add("SYSTEM", "transcribing…", "", "sys");
    try {
      const r = await fetch("/api/transcribe", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ audio: base64, language: "" }),
      });
      const d = await r.json().catch(() => ({}));
      if (d.text) send(d.text);
      else add("SYSTEM", d.error || "could not hear you", "", "sys");
    } catch (err) { add("SYSTEM", String(err.message || err), "", "sys"); }
  };
  rec.start(250);
  recording = true;
  $("btn-record").textContent = "■ STOP";
  $("btn-record").classList.add("rec");
}

function stopRecording() {
  recording = false;
  if (rec) rec.stop();
  $("btn-record").textContent = "RECORD";
  $("btn-record").classList.remove("rec");
}

$("btn-record").addEventListener("click", () => {
  recording ? stopRecording() : startRecording();
});

function blobToBase64(blob) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.readAsDataURL(blob);
  });
}

/* ------------------------------------------------------------------ face + voice */
const video = $("cam");
let camStream = null;

async function openCamera() {
  if (camStream) return camStream;
  camStream = await navigator.mediaDevices.getUserMedia({ video: { width: 480, height: 360 } });
  video.srcObject = camStream;
  video.style.display = "block";
  await video.play().catch(() => null);
  return camStream;
}

function capture() {
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth || 480;
  canvas.height = video.videoHeight || 360;
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.85);
}

async function post(path, body) {
  const r = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json().catch(() => ({}));
}

$("btn-face-enrol").addEventListener("click", async () => {
  add("SYSTEM", "opening the camera - look at it, sir", "", "sys");
  try {
    await openCamera();
    const shot = capture();
    const d = await post("/api/face/enrol", { image: shot });
    $("bio-hint").textContent = d.ok
      ? `face enrolled: ${d.samples || 0} photo(s) on this PC only`
      : (d.error || "enrolment failed");
    add("SYSTEM", $("bio-hint").textContent, "", "sys");
  } catch (err) { add("SYSTEM", "camera: " + err.message, "", "sys"); }
});

$("btn-face-verify").addEventListener("click", async () => {
  try {
    await openCamera();
    const d = await post("/api/face/verify", { image: capture() });
    const say = d.ok ? "FACE: match" : "FACE: no match";
    $("face-state").textContent = d.ok ? "matched" : "not matched";
    add("SYSTEM", `${say} (distance ${d.distance}, limit ${d.threshold})`, "", "sys");
  } catch (err) { add("SYSTEM", "camera: " + err.message, "", "sys"); }
});

async function recordFor(path, seconds) {
  media = await navigator.mediaDevices.getUserMedia({ audio: true });
  const type = ["audio/webm;codecs=opus", "audio/webm", "audio/wav"].find((t) =>
    window.MediaRecorder.isTypeSupported(t)) || "";
  chunks = [];
  rec = new MediaRecorder(media, type ? { mimeType: type } : undefined);
  rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
  rec.start();
  await new Promise((resolve) => setTimeout(resolve, seconds * 1000));
  await new Promise((resolve) => { rec.onstop = resolve; rec.stop(); });
  media.getTracks().forEach((t) => t.stop());
  media = null;
  const blob = new Blob(chunks, { type: type || "audio/webm" });
  const d = await post(path, { audio: await blobToBase64(blob), seconds });
  return d;
}

$("btn-voice-enrol").addEventListener("click", async () => {
  add("SYSTEM", "speak for 3 seconds, sir - reading this line is enough", "", "sys");
  try {
    const d = await recordFor("/api/voice/enrol", 3);
    $("voice-state").textContent = d.ok ? "enrolled" : "not enrolled";
    $("bio-hint").textContent = d.ok
      ? "voice print stored on this PC only (13 numbers, no audio kept)"
      : (d.error || "enrolment failed");
  } catch (err) { add("SYSTEM", "mic: " + err.message, "", "sys"); }
});

$("btn-voice-verify").addEventListener("click", async () => {
  try {
    const d = await recordFor("/api/voice/verify", 3);
    $("bio-hint").textContent = d.ok
      ? `voice matched (score ${d.score}, limit ${d.threshold})`
      : `voice did not match (${d.error || d.score})`;
    add("SYSTEM", $("bio-hint").textContent, "", "sys");
  } catch (err) { add("SYSTEM", "mic: " + err.message, "", "sys"); }
});

/* ------------------------------------------------------------------ status */
async function refresh() {
  try {
    const r = await fetch("/api/status", { cache: "no-store" });
    const s = await r.json();
    $("stat-memory").textContent = `${s.memory.facts} facts`;
    $("stat-tasks").textContent = `${s.memory.open_tasks} tasks`;
    const ready = (s.cloud.providers || []).filter((p) => p.key).length;
    $("stat-cloud").textContent = `cloud ${ready} ready`;
    $("cloud-state").textContent = ready + " provider(s) with keys";
    $("local-state").textContent = s.local_model ? "on" : "off";
    $("face-state").textContent = s.face.enrolled ? "enrolled" : "not enrolled";
    $("voice-state").textContent = s.voice.enrolled ? "enrolled" : "not enrolled";
  } catch (err) { /* the office is starting up */ }
}

setInterval(refresh, 5000);
setInterval(() => {
  $("clock").textContent = new Date().toLocaleTimeString();
}, 1000);
refresh();
add("EPU", "EPU is online, sir. Your name is remembered and your orders are final. "
           "Ask 'who am I', or order work like 'make a folder called reports in "
           "projects/testing'.", "office // cloud-ready", "sys");
