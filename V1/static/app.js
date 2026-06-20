const video = document.getElementById('video');
const canvas = document.getElementById('output');
const ctx = canvas.getContext('2d');

let displayWidth = 0;
let displayHeight = 0;
const emptyOverlay = document.getElementById('emptyOverlay');
const cameraState = document.getElementById('cameraState');
const statHands = document.getElementById('statHands');
const statGesture = document.getElementById('statGesture');
const statStatus = document.getElementById('statStatus');
const eventFeed = document.getElementById('eventFeed');
const bindingsList = document.getElementById('bindingsList');
const viewerStage = document.getElementById('viewerStage');

const startBtn = document.getElementById('startBtn');
const startBtn2 = document.getElementById('startBtn2');
const saveBtn = document.getElementById('saveBtn');
const resetBtn = document.getElementById('resetBtn');

let config = null;
let camera = null;
let hands = null;
let running = false;
let handHistory = new Map();
let frameCount = 0;
const lastLocalTriggerAt = new Map();
const lastStableGesture = new Map();
const stableCount = new Map();

const gestureLabels = [
  'thumb_up',
  'thumb_down',
  'fist',
  'open_palm',
  'swipe_up',
  'swipe_down',
  'swipe_left',
  'swipe_right',
  'pinch'
];

function formatTime(ts) {
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function setStatus(text) {
  statStatus.textContent = text;
}

function defaultConfig() {
  return {
    mode: 'preview',
    cooldownMs: 700,
    hands: {
      maxNumHands: 2,
      modelComplexity: 1,
      minDetectionConfidence: 0.8,
      minTrackingConfidence: 0.72
    },
    bindings: {
      thumb_up: { enabled: true, type: 'key', value: 'space' },
      thumb_down: { enabled: false, type: 'key', value: 'backspace' },
      fist: { enabled: true, type: 'key', value: 'f' },
      open_palm: { enabled: false, type: 'key', value: 'p' },
      swipe_up: { enabled: true, type: 'key', value: 'up' },
      swipe_down: { enabled: true, type: 'key', value: 'down' },
      swipe_left: { enabled: false, type: 'key', value: 'left' },
      swipe_right: { enabled: false, type: 'key', value: 'right' },
      pinch: { enabled: false, type: 'key', value: 'enter' }
    }
  };
}

function escapeHtml(text) {
  return text
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

async function loadConfig() {
  try {
    const res = await fetch('/api/config');
    config = await res.json();
  } catch {
    config = defaultConfig();
  }

  if (!config || !config.bindings) config = defaultConfig();
  if (!config.hands) config.hands = defaultConfig().hands;
  if (typeof config.cooldownMs !== 'number') config.cooldownMs = 700;

  renderBindings();
}

function renderBindings() {
  bindingsList.innerHTML = '';
  gestureLabels.forEach((gesture) => {
    const b = config.bindings?.[gesture] || { enabled: false, type: 'key', value: '' };
    const row = document.createElement('div');
    row.className = 'binding-row';
    row.innerHTML = `
      <div class="top">
        <div class="gesture">${gesture.replaceAll('_', ' ')}</div>
        <label class="toggle"><input type="checkbox" data-field="enabled" ${b.enabled ? 'checked' : ''}/> Enabled</label>
      </div>
      <div class="grid2">
        <div>
          <label>Action Type</label>
          <select data-field="type">
            <option value="key" ${b.type === 'key' ? 'selected' : ''}>Key Press</option>
            <option value="hotkey" ${b.type === 'hotkey' ? 'selected' : ''}>Hotkey</option>
            <option value="scroll" ${b.type === 'scroll' ? 'selected' : ''}>Scroll</option>
          </select>
        </div>
        <div>
          <label>Value</label>
          <input data-field="value" value="${escapeHtml(String(b.value ?? ''))}" placeholder="space / ctrl,shift,z / 600" />
        </div>
      </div>
    `;

    row.querySelectorAll('[data-field]').forEach((el) => {
      el.addEventListener('change', (e) => {
        const field = e.target.getAttribute('data-field');
        const value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
        config.bindings[gesture] = { ...(config.bindings[gesture] || {}), [field]: value };
      });
    });

    bindingsList.appendChild(row);
  });
}

function pushEvent({ gesture, hand, status, action, error }) {
  const el = document.createElement('div');
  el.className = 'event-item';
  const statusText = status === 'triggered' ? (action || 'triggered') : (error || status || 'ignored');
  el.innerHTML = `
    <div class="meta"><span>${formatTime(Date.now())}</span><span>${hand || 'unknown hand'}</span></div>
    <div class="gesture">${gesture.replaceAll('_', ' ')}</div>
    <div class="status">${statusText}</div>
  `;
  eventFeed.prepend(el);
  while (eventFeed.children.length > 20) eventFeed.removeChild(eventFeed.lastChild);
}

function dist(a, b) {
  const dx = a.x - b.x;
  const dy = a.y - b.y;
  return Math.sqrt(dx * dx + dy * dy);
}

function fingerExtended(landmarks, tip, pip) {
  return landmarks[tip].y < landmarks[pip].y;
}

function thumbExtended(landmarks, handedness) {
  const tip = landmarks[4];
  const ip = landmarks[3];
  const wrist = landmarks[0];
  const indexMcp = landmarks[5];
  const rightHand = handedness === 'Right';
  const horizontal = rightHand ? tip.x < ip.x : tip.x > ip.x;
  const spread = Math.abs(tip.y - wrist.y) < Math.abs(indexMcp.y - wrist.y) + 0.16;
  return horizontal && spread;
}

function classifyGesture(landmarks, handedness) {
  const fingers = {
    thumb: thumbExtended(landmarks, handedness),
    index: fingerExtended(landmarks, 8, 6),
    middle: fingerExtended(landmarks, 12, 10),
    ring: fingerExtended(landmarks, 16, 14),
    pinky: fingerExtended(landmarks, 20, 18)
  };

  const extendedCount = Object.values(fingers).filter(Boolean).length;
  const pinchDistance = dist(landmarks[4], landmarks[8]);
  const wrist = landmarks[0];
  const center = landmarks.reduce((acc, p) => ({ x: acc.x + p.x, y: acc.y + p.y }), { x: 0, y: 0 });
  center.x /= landmarks.length;
  center.y /= landmarks.length;
  const avgFingerLift = [8, 12, 16, 20].reduce((sum, tip) => sum + (wrist.y - landmarks[tip].y), 0) / 4;

  if (pinchDistance < 0.045 && extendedCount <= 2) return 'pinch';
  if (extendedCount <= 1) return 'fist';

  if (fingers.thumb && !fingers.index && !fingers.middle && !fingers.ring && !fingers.pinky) {
    const thumbTip = landmarks[4];
    const thumbIp = landmarks[3];
    if (thumbTip.y < thumbIp.y) return 'thumb_up';
    if (thumbTip.y > thumbIp.y) return 'thumb_down';
  }

  if (extendedCount >= 4 && avgFingerLift > 0.12) return 'open_palm';
  return null;
}

function updateMotionHistory(label, landmarks) {
  const center = landmarks.reduce((acc, p) => ({ x: acc.x + p.x, y: acc.y + p.y }), { x: 0, y: 0 });
  center.x /= landmarks.length;
  center.y /= landmarks.length;
  const now = performance.now();
  const arr = handHistory.get(label) || [];
  arr.push({ x: center.x, y: center.y, t: now });
  while (arr.length > 12) arr.shift();
  handHistory.set(label, arr);
  return arr;
}

function detectSwipe(label) {
  const arr = handHistory.get(label) || [];
  if (arr.length < 6) return null;
  const recent = arr[arr.length - 1];
  const older = arr[0];
  const dt = recent.t - older.t;
  if (dt < 170) return null;

  const dx = recent.x - older.x;
  const dy = recent.y - older.y;
  const absX = Math.abs(dx);
  const absY = Math.abs(dy);

  if (absY > 0.14 && absY > absX * 1.15) {
    if (dy < 0) return 'swipe_up';
    if (dy > 0) return 'swipe_down';
  }

  if (absX > 0.16 && absX > absY * 1.15) {
    if (dx < 0) return 'swipe_left';
    if (dx > 0) return 'swipe_right';
  }

  return null;
}

function throttleGesture(label, gesture) {
  const key = `${label}`;
  const prev = lastStableGesture.get(key);
  if (prev === gesture) {
    const count = (stableCount.get(key) || 0) + 1;
    stableCount.set(key, count);
    return count >= 2;
  }

  lastStableGesture.set(key, gesture);
  stableCount.set(key, 1);
  return false;
}

async function sendGesture(gesture, hand, confidence = 1) {
  const now = performance.now();
  const key = `${gesture}:${hand}`;
  const last = lastLocalTriggerAt.get(key) || 0;
  if (now - last < (config.cooldownMs || 700)) return;
  lastLocalTriggerAt.set(key, now);

  statGesture.textContent = gesture.replaceAll('_', ' ');

  try {
    const response = await fetch('/api/trigger', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ gesture, hand, confidence })
    });
    const data = await response.json();
    if (data.ok && data.status === 'triggered') {
      pushEvent({ gesture, hand, status: 'triggered', action: data.action });
    } else if (data.ok && data.status === 'ignored') {
      pushEvent({ gesture, hand, status: 'ignored', action: data.reason });
    } else {
      pushEvent({ gesture, hand, status: 'error', error: data.error || 'failed' });
    }
  } catch (error) {
    pushEvent({ gesture, hand, status: 'error', error: String(error) });
  }
}

function drawPreviewFrame(image) {
  const w = displayWidth || canvas.width;
  const h = displayHeight || canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.save();
  ctx.scale(-1, 1);
  ctx.drawImage(image, -w, 0, w, h);
  ctx.restore();
}

function drawFallbackBackground() {
  const w = displayWidth || canvas.width;
  const h = displayHeight || canvas.height;
  const gradient = ctx.createLinearGradient(0, 0, w, h);
  gradient.addColorStop(0, '#07111f');
  gradient.addColorStop(1, '#02070c');
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, w, h);
}

function drawLandmarks(results) {
  const w = displayWidth || canvas.width;
  const h = displayHeight || canvas.height;
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';

  if (results.image) drawPreviewFrame(results.image);
  else drawFallbackBackground();

  const handsList = results.multiHandLandmarks || [];
  const handednesses = results.multiHandedness || [];
  statHands.textContent = String(handsList.length);

  handsList.forEach((landmarks, idx) => {
    const label = handednesses[idx]?.label || `Hand ${idx + 1}`;
    const gesture = classifyGesture(landmarks, label);
    updateMotionHistory(label, landmarks);
    const swipeGesture = detectSwipe(label);
    const finalGesture = swipeGesture || gesture;

    drawConnectors(ctx, landmarks, HAND_CONNECTIONS, {
      color: '#22c55e',
      lineWidth: 4
    });
    drawLandmarks(ctx, landmarks, {
      color: '#86efac',
      lineWidth: 2,
      radius: 3
    });

    const palm = landmarks[0];
    const x = palm.x * w;
    const y = palm.y * h;

    if (finalGesture) {
      ctx.save();
      const text = `${label}: ${finalGesture.replaceAll('_', ' ')}`;
      ctx.font = '600 18px Inter, sans-serif';
      const pad = 12;
      const tw = ctx.measureText(text).width;
      const boxW = tw + pad * 2;
      const boxH = 32;
      ctx.fillStyle = 'rgba(2, 7, 12, 0.82)';
      ctx.strokeStyle = 'rgba(34, 197, 94, 0.55)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.roundRect(x + 10, Math.max(12, y - 42), boxW, boxH, 12);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = '#ecfdf5';
      ctx.fillText(text, x + 22, Math.max(34, y - 19));
      ctx.restore();
    }

    const confident = finalGesture ? 1 : 0.4;
    if (swipeGesture) {
      if (throttleGesture(label, swipeGesture)) sendGesture(swipeGesture, label, confident);
    } else if (gesture) {
      if (throttleGesture(label, gesture)) sendGesture(gesture, label, confident);
    } else {
      lastStableGesture.set(label, null);
      stableCount.set(label, 0);
    }
  });
}

async function initHands() {
  hands = new Hands({
    locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/hands/${file}`
  });

  hands.setOptions({
    selfieMode: true,
    maxNumHands: config.hands?.maxNumHands ?? 2,
    modelComplexity: config.hands?.modelComplexity ?? 1,
    minDetectionConfidence: config.hands?.minDetectionConfidence ?? 0.8,
    minTrackingConfidence: config.hands?.minTrackingConfidence ?? 0.72
  });

  hands.onResults((results) => {
    const start = performance.now();
    drawLandmarks(results);
    const elapsed = performance.now() - start;
    if (elapsed > 18) setStatus('Tracking live • high load');
    else setStatus('Tracking live');
  });
}

async function startCamera() {
  if (running) return;
  if (!hands) await initHands();

  emptyOverlay.style.display = 'none';
  cameraState.textContent = 'Requesting camera permission...';

  const cam = new Camera(video, {
    onFrame: async () => {
      frameCount += 1;
      if (frameCount % 2 !== 0) return;
      await hands.send({ image: video });
    },
    width: 1280,
    height: 720
  });

  camera = cam;
  await camera.start();
  running = true;
  cameraState.textContent = 'Camera running';
  setStatus('Tracking live');
}

async function saveConfig() {
  const payload = {
    mode: 'preview',
    bindings: config.bindings,
    cooldownMs: config.cooldownMs,
    hands: config.hands
  };

  await fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });

  cameraState.textContent = 'Bindings saved';
  setTimeout(() => cameraState.textContent = running ? 'Camera running' : 'Camera not started', 1200);
}

async function resetConfig() {
  await fetch('/api/reset', { method: 'POST' });
  config = defaultConfig();
  renderBindings();
  eventFeed.innerHTML = '';
  handHistory.clear();
  lastStableGesture.clear();
  stableCount.clear();
  cameraState.textContent = 'Defaults restored';
}

function resizeCanvas() {
  const rect = viewerStage.getBoundingClientRect();
  displayWidth = rect.width;
  displayHeight = rect.height;
  canvas.width = Math.floor(rect.width * devicePixelRatio);
  canvas.height = Math.floor(rect.height * devicePixelRatio);
  ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
}

window.addEventListener('resize', resizeCanvas);

startBtn.addEventListener('click', startCamera);
startBtn2.addEventListener('click', startCamera);
saveBtn.addEventListener('click', saveConfig);
resetBtn.addEventListener('click', async () => {
  await resetConfig();
  await loadConfig();
});

(async function main() {
  resizeCanvas();
  await loadConfig();
  cameraState.textContent = 'Ready';
  setStatus('Idle');
})();
