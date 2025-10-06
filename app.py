#!/usr/bin/env python3
"""
Flask app with background IMAP polling worker to auto-confirm Pinterest emails.
Deploy on a VPS or in Docker. Uses environment variables from .env for credentials.
"""
import os, time, imaplib, email, threading, logging
from urllib.parse import urlparse, parse_qs, unquote
import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify
from dotenv import load_dotenv

load_dotenv()

IMAP_HOST = os.getenv("IMAP_HOST", "imap.question-solver.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER = os.getenv("IMAP_USER", "pin1@question-solver.com")
IMAP_PASS = os.getenv("IMAP_PASS", "")
MAILBOX = os.getenv("MAILBOX", "INBOX")
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "1"))
PROCESSED_FOLDER = os.getenv("PROCESSED_FOLDER", "Processed")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("auto_confirm")

app = Flask(__name__)
_worker_thread = None
_worker_stop_event = threading.Event()
_worker_status = {"running": False, "processed_count": 0, "last_result": None}

@app.route("/health")
def health():
    return "OK", 200

def connect_imap():
    imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=30)
    imap.login(IMAP_USER, IMAP_PASS)
    return imap

def ensure_folder(imap, folder):
    try:
        imap.list()
        imap.create(folder)
    except Exception:
        pass

def get_unseen_uids(imap):
    imap.select(MAILBOX)
    typ, data = imap.search(None, '(UNSEEN)')
    if typ != 'OK' or not data or not data[0]:
        return []
    return data[0].split()

def fetch_message_html(imap, uid):
    typ, msg_data = imap.fetch(uid, '(RFC822)')
    if typ != 'OK':
        return None
    raw = msg_data[0][1]
    msg = email.message_from_bytes(raw)
    html = None
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            cdisp = str(part.get('Content-Disposition') or '')
            if ctype == 'text/html' and 'attachment' not in cdisp:
                charset = part.get_content_charset() or 'utf-8'
                html = part.get_payload(decode=True).decode(charset, errors='replace')
                break
        if html is None:
            for part in msg.walk():
                if part.get_content_type() == 'text/plain':
                    charset = part.get_content_charset() or 'utf-8'
                    html = part.get_payload(decode=True).decode(charset, errors='replace')
                    break
    else:
        charset = msg.get_content_charset() or 'utf-8'
        html = msg.get_payload(decode=True).decode(charset, errors='replace')
    return html

def extract_confirm_links(html):
    if not html:
        return []
    soup = BeautifulSoup(html, 'html.parser')
    links = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        if '/email/click/' in href.lower() or any(k in href.lower() for k in ('confirm', 'verify', 'autologin', 'activate')):
            links.append(href)
    if not links:
        import re
        for m in re.finditer(r'https?://[^\s"\'>]+', html):
            url = m.group(0)
            if any(x in url.lower() for x in ('/email/click/', 'confirm', 'verify', 'autologin')):
                links.append(url)
    return links

def decode_target_from_href(href):
    try:
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        if 'target' in qs and qs['target']:
            target_enc = qs['target'][0]
            target = target_enc
            for _ in range(4):
                new = unquote(target)
                if new == target:
                    break
                target = new
            return target
    except Exception as e:
        logger.debug('decode error %s', e)
    return href

def call_url(url_to_call, timeout=20):
    headers = {'User-Agent': 'AutoConfirm/1.0 (+contact@example.com)'}
    try:
        r = requests.get(url_to_call, headers=headers, timeout=timeout, allow_redirects=True)
        logger.info('GET %s -> %s (final: %s)', url_to_call, r.status_code, r.url)
        return True, r.status_code, r.url
    except Exception as e:
        logger.exception('call_url error: %s', e)
        return False, None, str(e)

def mark_message_processed(imap, uid):
    try:
        imap.store(uid, '+FLAGS', '\\Seen')
        imap.store(uid, '+FLAGS', '\\Answered')
        if PROCESSED_FOLDER:
            try:
                ensure_folder(imap, PROCESSED_FOLDER)
                typ, _ = imap.uid('COPY', uid.decode(), PROCESSED_FOLDER)
                if typ == 'OK':
                    imap.uid('STORE', uid.decode(), '+FLAGS', '(\\Deleted)')
                    imap.expunge()
            except Exception:
                pass
    except Exception:
        pass

def process_one_message(imap, uid):
    html = fetch_message_html(imap, uid)
    if not html:
        mark_message_processed(imap, uid)
        return False, 'no-body'
    links = extract_confirm_links(html)
    if not links:
        mark_message_processed(imap, uid)
        return False, 'no-link'
    chosen = None
    for l in links:
        if '/email/click/' in l.lower():
            chosen = l
            break
    if not chosen:
        chosen = links[0]
    target = decode_target_from_href(chosen)
    ok, status, final = call_url(target)
    mark_message_processed(imap, uid)
    return ok, f'status={status},final={final}'

def worker_loop():
    logger.info('worker starting (poll %s s)', POLL_INTERVAL)
    _worker_status['running'] = True
    try:
        imap = connect_imap()
    except Exception as e:
        logger.exception('imap connect failed: %s', e)
        _worker_status['running'] = False
        return
    try:
        while not _worker_stop_event.is_set():
            try:
                uids = get_unseen_uids(imap)
            except Exception as e:
                logger.exception('search error: %s', e)
                try:
                    imap.logout()
                except Exception:
                    pass
                time.sleep(5)
                try:
                    imap = connect_imap()
                except Exception:
                    time.sleep(POLL_INTERVAL)
                continue
            if uids:
                for uid in uids:
                    if _worker_stop_event.is_set():
                        break
                    try:
                        ok, info = process_one_message(imap, uid)
                        _worker_status['processed_count'] += 1
                        _worker_status['last_result'] = {'uid': uid.decode(), 'ok': ok, 'info': info, 'ts': time.time()}
                        logger.info('processed %s -> %s', uid.decode(), info)
                        time.sleep(0.5)
                    except Exception as ex:
                        logger.exception('processing error: %s', ex)
            time.sleep(POLL_INTERVAL)
    finally:
        try:
            imap.logout()
        except Exception:
            pass
        _worker_status['running'] = False
        logger.info('worker stopped')

def start_worker():
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _worker_stop_event.clear()
    _worker_thread = threading.Thread(target=worker_loop, daemon=True)
    _worker_thread.start()

def stop_worker():
    _worker_stop_event.set()
    if _worker_thread:
        _worker_thread.join(timeout=10)

@app.route('/')
def index():
    return jsonify({'status':'ok','worker_running': _worker_status['running'],'processed_count': _worker_status['processed_count']})

@app.route('/status')
def status():
    return jsonify(_worker_status)

@app.route('/trigger_once', methods=['POST'])
def trigger_once():
    try:
        imap = connect_imap()
        uids = get_unseen_uids(imap)
        results = []
        for uid in uids:
            ok, info = process_one_message(imap, uid)
            results.append({'uid': uid.decode(), 'ok': ok, 'info': info})
        imap.logout()
        return jsonify({'results': results})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    start_worker()
    app.run(host="0.0.0.0", port=5000)