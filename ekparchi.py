#!/usr/bin/env python3
"""Ek Parchi scan app (MVP).

One photo of a ticked Ek Parchi slip -> lab order in a local database,
tube labels, and a one-patient collection screen.

Commands
  make-slip   print-ready slip (PDF + PNG) with a real QR code
  scan        read a slip photo: straighten, read QR, read ticks, make the order
  tube        scan a tube label against the current patient (mix-up lock)
  selftest    run the reader on synthetic, distorted slip photos
"""
import argparse
import datetime as dt
import json
import os
import random
import sqlite3
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, 'config.json'), encoding='utf-8'))
TESTS = CFG['tests']
CUT = CFG['cutoffs']
DB_PATH = os.path.join(HERE, 'ekparchi_lis.sqlite')
OUT = os.path.join(HERE, 'out')

# ------------------------------------------------------------------ slip layout (mm, A5 landscape)
W_MM, H_MM = 210.0, 148.0
MARK = 8.0
MARK_CENTRES = [(10.0, 10.0), (200.0, 10.0), (200.0, 138.0), (10.0, 138.0)]  # tl, tr, br, bl
QR_BOX = (152.0, 16.0, 190.0, 54.0)
BOX = 7.0
COL_X = [24.0, 112.0]
ROW_Y0, ROW_PITCH = 61.0, 10.0
CONSENT_Y = 130.0
CONSENT = [('WhatsApp / SMS', COL_X[0]), ('Add report to my ABHA', COL_X[1])]
CANON = 10  # px per mm in the straightened image


def box_xy(i):
    """Top-left (mm) of tick-box i (0..11): two columns of six."""
    return COL_X[i // 6], ROW_Y0 + (i % 6) * ROW_PITCH


def cancel_xy(i):
    x, y = box_xy(i)
    return x - 8.0, y


def font(size_px, bold=False):
    names = (['Carlito-Bold.ttf', 'arialbd.ttf', 'DejaVuSans-Bold.ttf'] if bold
             else ['Carlito-Regular.ttf', 'arial.ttf', 'DejaVuSans.ttf'])
    dirs = ['', '/usr/share/fonts/truetype/crosextra/', '/usr/share/fonts/truetype/dejavu/']
    for n in names:
        for d in dirs:
            try:
                return ImageFont.truetype(d + n, size_px)
            except OSError:
                pass
    return ImageFont.load_default()


def qr_image(text, px):
    enc = cv2.QRCodeEncoder.create()
    q = enc.encode(text)  # 1 px per module, includes quiet zone
    q = cv2.resize(q, (px, px), interpolation=cv2.INTER_NEAREST)
    return Image.fromarray(q).convert('RGB')


def qr_payload(op, name='', age='', sex=''):
    return f'EP1|{op}|{name}|{age}|{sex}'


def parse_payload(s):
    p = (s or '').split('|')
    if len(p) < 2 or p[0] != 'EP1':
        return None
    p += [''] * (5 - len(p))
    return {'op': p[1], 'name': p[2], 'age': p[3], 'sex': p[4]}


def render_slip(patient, S=12, ticks=None):
    """Draw the slip at S px per mm. ticks: optional list of (box_index, style, colour, cancel)."""
    W, H = int(W_MM * S), int(H_MM * S)
    im = Image.new('RGB', (W, H), 'white')
    d = ImageDraw.Draw(im)
    P = lambda mm: int(round(mm * S))
    for cx, cy in MARK_CENTRES:
        d.rectangle([P(cx - MARK / 2), P(cy - MARK / 2), P(cx + MARK / 2), P(cy + MARK / 2)], fill='black')
    teal = (11, 74, 79)
    d.text((P(20), P(7)), f"{CFG['hospital_name']}  |  OP SLIP  |  Ek Parchi", fill=teal, font=font(P(5), True))
    f = font(P(4.2))
    d.text((P(20), P(17)), f"OP No.: {patient['op']}", fill='black', font=f)
    d.text((P(20), P(24)), f"Name: {patient.get('name') or '______________________'}", fill='black', font=f)
    d.text((P(20), P(31)), f"Age/Sex: {patient.get('age') or '___'} / {patient.get('sex') or '_'}     Date: {dt.date.today():%d/%m/%Y}",
           fill='black', font=f)
    d.text((P(20), P(38)), 'Doctor / Room: ______________', fill='black', font=f)
    q = qr_image(qr_payload(patient['op'], patient.get('name', ''), patient.get('age', ''), patient.get('sex', '')),
                 P(QR_BOX[2] - QR_BOX[0]))
    im.paste(q, (P(QR_BOX[0]), P(QR_BOX[1])))
    d.text((P(20), P(50)), 'LAB TESTS (doctor ticks). Use a dark pen. Wrong tick? Put an X just left of the box.',
           fill=(217, 130, 43), font=font(P(3.6), True))
    fl = font(P(4.0))
    for i, t in enumerate(TESTS):
        x, y = box_xy(i)
        d.rectangle([P(x), P(y), P(x + BOX), P(y + BOX)], outline='black', width=max(2, P(0.35)))
        d.text((P(x + BOX + 3), P(y + 1.2)), t['name'], fill='black', font=fl)
    d.text((P(20), P(121)), 'Other (write clearly): ________________________________', fill='black', font=font(P(4.0), True))
    for i, (lab, x) in enumerate(CONSENT):
        d.rectangle([P(x), P(CONSENT_Y), P(x + BOX), P(CONSENT_Y + BOX)], outline=(217, 130, 43), width=max(2, P(0.35)))
        d.text((P(x + BOX + 3), P(CONSENT_Y + 1.2)), ('CONSENT: ' if i == 0 else '') + lab, fill=(217, 130, 43), font=fl)
    if ticks:
        for (i, style, colour, cancel) in ticks:
            if i >= 100:  # consent boxes are 100, 101
                x, y = CONSENT[i - 100][1], CONSENT_Y
            else:
                x, y = box_xy(i)
            draw_mark(d, P, x, y, style, colour)
            if cancel:
                cx, cy = cancel_xy(i)
                draw_mark(d, P, cx, cy, 'cross', colour)
    return im


def draw_mark(d, P, x, y, style, colour, rnd=random):
    w = max(2, P(rnd.uniform(0.35, 0.6)))
    j = lambda: rnd.uniform(-0.6, 0.6)
    if style == 'check':
        pts = [(x + 1.2 + j(), y + 3.8 + j()), (x + 3.0 + j(), y + 5.8 + j()), (x + 6.4 + j(), y + 0.9 + j())]
        d.line([(P(a), P(b)) for a, b in pts], fill=colour, width=w)
    elif style == 'cross':
        d.line([(P(x + 1 + j()), P(y + 1 + j())), (P(x + 6 + j()), P(y + 6 + j()))], fill=colour, width=w)
        d.line([(P(x + 6 + j()), P(y + 1 + j())), (P(x + 1 + j()), P(y + 6 + j()))], fill=colour, width=w)
    elif style == 'slash':
        d.line([(P(x + 1.2 + j()), P(y + 5.8 + j())), (P(x + 5.8 + j()), P(y + 1.2 + j()))], fill=colour, width=w + 1)
    else:  # scribble
        pts = [(x + rnd.uniform(1, 6), y + rnd.uniform(1, 6)) for _ in range(7)]
        d.line([(P(a), P(b)) for a, b in pts], fill=colour, width=w)


# ------------------------------------------------------------------ reading a photo
def _norm(gray):
    """Divide by a blurred background so shadows and uneven light cancel out (paper ~1.0, ink < 1)."""
    g = gray.astype(np.float32)
    k = max(31, (min(g.shape) // 12) | 1)
    bg = cv2.dilate(g, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    bg = cv2.GaussianBlur(bg, (k, k), 0)
    return np.clip(g / (bg + 1e-3), 0, 1.2)


def find_marks(gray):
    """Return the 4 corner-mark centres (tl, tr, br, bl) in image pixels, or None."""
    n = _norm(gray)
    binv = (n < 0.6).astype(np.uint8) * 255
    binv = cv2.morphologyEx(binv, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    cnts, _ = cv2.findContours(binv, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    area_img = gray.shape[0] * gray.shape[1]
    cands = []
    for c in cnts:
        a = cv2.contourArea(c)
        if a < area_img * 0.00008 or a > area_img * 0.01:
            continue
        x, y, w, h = cv2.boundingRect(c)
        if not 0.6 < w / float(h) < 1.65:
            continue
        hull = cv2.convexHull(c)
        if a / (cv2.contourArea(hull) + 1e-6) < 0.88:
            continue
        mask = np.zeros(binv.shape, np.uint8)
        cv2.drawContours(mask, [c], -1, 255, -1)
        if cv2.mean(binv, mask=mask)[0] / 255.0 < 0.9:   # must be a filled square (QR finders and tick-boxes are not)
            continue
        if len(cv2.approxPolyDP(c, 0.08 * cv2.arcLength(c, True), True)) != 4:
            continue
        M = cv2.moments(c)
        cands.append((M['m10'] / M['m00'], M['m01'] / M['m00'], a))
    if len(cands) < 4:
        return None
    cands = sorted(cands, key=lambda t: -t[2])[:14]
    best, best_area = None, 0
    from itertools import combinations
    for combo in combinations(cands, 4):
        areas = [t[2] for t in combo]
        if max(areas) > 3.0 * min(areas):
            continue
        pts = np.array([[t[0], t[1]] for t in combo], np.float32)
        hull = cv2.convexHull(pts)
        if len(hull) != 4:
            continue
        qa = cv2.contourArea(hull)
        if qa > best_area:
            best, best_area = pts, qa
    if best is None:
        return None
    s, dd = best.sum(1), np.diff(best, axis=1).ravel()
    tl, br = best[np.argmin(s)], best[np.argmax(s)]
    tr, bl = best[np.argmin(dd)], best[np.argmax(dd)]
    quad = np.array([tl, tr, br, bl], np.float32)
    w = np.linalg.norm(quad[1] - quad[0]) + np.linalg.norm(quad[2] - quad[3])
    h = np.linalg.norm(quad[3] - quad[0]) + np.linalg.norm(quad[2] - quad[1])
    if h > w:  # slip photographed sideways
        quad = np.roll(quad, 1, axis=0)
    return quad


def straighten(img, quad, rot180=False):
    dst = np.array([[x * CANON, y * CANON] for x, y in MARK_CENTRES], np.float32)
    if rot180:
        quad = np.roll(quad, 2, axis=0)
    M = cv2.getPerspectiveTransform(quad, dst)
    return cv2.warpPerspective(img, M, (int(W_MM * CANON), int(H_MM * CANON)), borderValue=(255, 255, 255))


def read_qr(warped):
    det = cv2.QRCodeDetector()
    x0, y0, x1, y1 = [int(v * CANON) for v in (QR_BOX[0] - 4, QR_BOX[1] - 4, QR_BOX[2] + 4, QR_BOX[3] + 4)]
    for img in (warped[y0:y1, x0:x1], warped):
        for scale in (1.0, 1.6):
            im = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) if scale != 1.0 else img
            txt, _, _ = det.detectAndDecode(im)
            if txt:
                return txt
    return ''


def ink(normed, x_mm, y_mm, size_mm=BOX, inset=1.3):
    a, b = int((x_mm + inset) * CANON), int((y_mm + inset) * CANON)
    c, e = int((x_mm + size_mm - inset) * CANON), int((y_mm + size_mm - inset) * CANON)
    roi = normed[b:e, a:c]
    return float((roi < CUT['dark']).mean()), float((roi < CUT['light']).mean())


def classify(dark, light):
    if dark >= CUT['ticked_dark_frac']:
        return 'ticked'
    if light >= CUT['unsure_light_frac'] or dark >= CUT['ticked_dark_frac'] * 0.35:
        return 'unsure'
    return 'empty'


def read_slip(img):
    """Full read of one photo. Returns a dict, never raises on a bad photo."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    quad = find_marks(gray)
    if quad is None:
        return {'ok': False, 'error': 'corner marks not found - retake the photo (whole slip, flat, lit)'}
    warped, payload = None, ''
    for rot in (False, True):
        w = straighten(img, quad, rot)
        payload = read_qr(w)
        if payload:
            warped = w
            break
    if warped is None:
        warped = straighten(img, quad, False)
    wg = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY) if warped.ndim == 3 else warped
    normed = _norm(wg)
    boxes = []
    for i, t in enumerate(TESTS):
        x, y = box_xy(i)
        d, l = ink(normed, x, y)
        cx, cy = cancel_xy(i)
        cd, _ = ink(normed, cx, cy, inset=0.8)
        state = classify(d, l)
        cancelled = cd >= CUT['cancel_dark_frac']
        if cancelled:
            state = 'unsure'
        boxes.append({'i': i, 'test': t['name'], 'tube': t['tube'], 'state': state, 'cancel_mark': cancelled,
                      'ink': round(d, 3), 'light_ink': round(l, 3)})
    consent = {}
    for k, (lab, x) in enumerate(CONSENT):
        d, l = ink(normed, x, CONSENT_Y)
        consent[lab] = classify(d, l)
    return {'ok': True, 'qr': payload, 'patient': parse_payload(payload), 'boxes': boxes, 'consent': consent,
            'warped': warped, 'quad': quad.tolist()}


# ------------------------------------------------------------------ local LIS (SQLite)
SAFE = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # no 0/O, 1/I


def new_sample_root(rnd=random):
    body = ''.join(rnd.choice(SAFE) for _ in range(5))
    check = SAFE[sum(SAFE.index(ch) * (k + 1) for k, ch in enumerate(body)) % len(SAFE)]
    return f'EP-{body}{check}'


def db():
    con = sqlite3.connect(DB_PATH)
    con.executescript('''
    CREATE TABLE IF NOT EXISTS patients(op TEXT PRIMARY KEY, name TEXT, age TEXT, sex TEXT);
    CREATE TABLE IF NOT EXISTS orders(order_id TEXT PRIMARY KEY, op TEXT, created TEXT, tests TEXT, consent TEXT,
                                      status TEXT, active INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS samples(sample_id TEXT PRIMARY KEY, order_id TEXT, tube TEXT, tests TEXT,
                                       scanned INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS sync_log(n INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, kind TEXT, ref TEXT, payload TEXT,
                                        synced INTEGER DEFAULT 0);
    ''')
    return con


def log(con, kind, ref, payload):
    con.execute('INSERT INTO sync_log(at, kind, ref, payload) VALUES (?,?,?,?)',
                (dt.datetime.now().isoformat(timespec='seconds'), kind, ref, json.dumps(payload)))


def label_png(path, patient, sample_id, tube, tests):
    S = 12
    W, H = 50 * S, 25 * S
    im = Image.new('RGB', (W, H), 'white')
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W - 1, H - 1], outline=(180, 180, 180), width=2)
    d.rectangle([0, 0, int(3 * S), H], fill=tuple(CFG['tubes'][tube]['colour']))
    d.text((int(4.5 * S), int(1.5 * S)), (patient['name'] or 'NAME ?').upper()[:24], fill='black', font=font(int(3.6 * S), True))
    d.text((int(4.5 * S), int(6 * S)), f"OP {patient['op']}", fill='black', font=font(int(3.2 * S)))
    im.paste(qr_image(sample_id, int(13 * S)), (int(4.5 * S), int(10.5 * S)))
    d.text((int(19 * S), int(12 * S)), sample_id, fill='black', font=font(int(3.6 * S), True))
    d.text((int(19 * S), int(17 * S)), CFG['tubes'][tube]['label'], fill='black', font=font(int(2.8 * S)))
    d.text((int(19 * S), int(20.5 * S)), ', '.join(tests)[:34], fill=(80, 80, 80), font=font(int(2.6 * S)))
    im.save(path)


def screen_html(path, order, patient, samples, unsure, consent):
    rows = ''.join(
        f"<li class='{'ok' if s['scanned'] else 'todo'}'>{'&#10003;' if s['scanned'] else '&#9744;'} "
        f"{CFG['tubes'][s['tube']]['label']} &middot; {s['sample_id']} &middot; {', '.join(s['tests'])}</li>" for s in samples)
    checks = ''.join(f"<li>Box {u['i'] + 1}: <b>{u['test']}</b> &mdash; {'X beside the box' if u['cancel_mark'] else 'unclear mark'}"
                     f" &rarr; confirm with 1 tap</li>" for u in unsure) or '<li>None</li>'
    done = all(s['scanned'] for s in samples)
    html = f"""<!doctype html><meta charset='utf-8'><title>Ek Parchi collection screen</title>
<style>body{{font-family:Calibri,Arial,sans-serif;margin:24px;color:#1f2a2c}}h1{{font-size:44px;margin:0;color:#0b4a4f}}
.sub{{color:#5c6b6d;font-size:18px}}ul{{font-size:20px;line-height:1.7}}.ok{{color:#2e7d4f}}.todo{{color:#1f2a2c}}
.next{{display:inline-block;padding:12px 22px;border-radius:8px;color:#fff;font-weight:bold;font-size:20px;background:{'#2e7d4f' if done else '#7a8a8c'}}}
.box{{background:#fbeadb;border-radius:8px;padding:8px 16px;margin:12px 0}}</style>
<h1>{(patient['name'] or 'NAME NOT ON SLIP').upper()}</h1>
<div class='sub'>OP {patient['op']} &middot; {patient['age']} / {patient['sex']} &middot; order {order}</div>
<h2>Tubes to draw (scan each tube against this slip)</h2><ul>{rows}</ul>
<div class='box'><b>Needs a check</b><ul>{checks}</ul></div>
<p>Consent: {', '.join(f'{k}: {v}' for k, v in consent.items())}</p>
<span class='next'>{'Next patient' if done else 'Next (locked until every tube is scanned)'}</span>"""
    open(path, 'w', encoding='utf-8').write(html)


def make_order(res, name_override=None, rnd=random):
    con = db()
    p = res['patient'] or {'op': 'UNKNOWN', 'name': '', 'age': '', 'sex': ''}
    if name_override:
        p['name'] = name_override
    active = con.execute('SELECT order_id FROM orders WHERE active=1').fetchone()
    if active:
        left = con.execute('SELECT COUNT(*) FROM samples WHERE order_id=? AND scanned=0', active).fetchone()[0]
        if left:
            con.close()
            return None, f'NEXT IS LOCKED: {left} tube(s) of order {active[0]} are not scanned yet.'
        con.execute("UPDATE orders SET active=0, status='collected' WHERE order_id=?", active)
    ticked = [b for b in res['boxes'] if b['state'] == 'ticked']
    unsure = [b for b in res['boxes'] if b['state'] == 'unsure']
    order_id = 'ORD-' + dt.datetime.now().strftime('%y%m%d%H%M%S') + rnd.choice(SAFE)
    con.execute('INSERT OR REPLACE INTO patients VALUES (?,?,?,?)', (p['op'], p['name'], p['age'], p['sex']))
    con.execute('INSERT INTO orders VALUES (?,?,?,?,?,?,1)', (order_id, p['op'], dt.datetime.now().isoformat(timespec='seconds'),
                json.dumps([b['test'] for b in ticked]), json.dumps(res['consent']), 'ordered'))
    root = new_sample_root(rnd)
    by_tube = {}
    for b in ticked:
        by_tube.setdefault(b['tube'], []).append(b['test'])
    outdir = os.path.join(OUT, order_id)
    os.makedirs(outdir, exist_ok=True)
    samples = []
    for k, (tube, tests) in enumerate(sorted(by_tube.items())):
        sid = f'{root}-{chr(65 + k)}'
        con.execute('INSERT INTO samples VALUES (?,?,?,?,0)', (sid, order_id, tube, json.dumps(tests)))
        label_png(os.path.join(outdir, f'label_{sid}.png'), p, sid, tube, tests)
        samples.append({'sample_id': sid, 'tube': tube, 'tests': tests, 'scanned': 0})
    log(con, 'order', order_id, {'op': p['op'], 'tests': [b['test'] for b in ticked], 'samples': [s['sample_id'] for s in samples]})
    con.commit(); con.close()
    order = {'order_id': order_id, 'patient': p, 'tests': [b['test'] for b in ticked],
             'needs_check': [{'box': b['i'] + 1, 'test': b['test'], 'x_beside_box': b['cancel_mark']} for b in unsure],
             'consent': res['consent'], 'samples': samples}
    json.dump(order, open(os.path.join(outdir, 'order.json'), 'w'), indent=2)
    cv2.imwrite(os.path.join(outdir, 'straightened_slip.jpg'), res['warped'])
    screen_html(os.path.join(outdir, 'collection_screen.html'), order_id, p, samples, unsure, res['consent'])
    return order, outdir


# ------------------------------------------------------------------ commands
def cmd_make_slip(a):
    p = {'op': a.op, 'name': a.name or '', 'age': a.age or '', 'sex': a.sex or ''}
    im = render_slip(p, S=12)
    os.makedirs(OUT, exist_ok=True)
    base = os.path.join(OUT, f'slip_{a.op}')
    im.save(base + '.png')
    im.save(base + '.pdf', resolution=12 * 25.4)
    print(f'Slip written: {base}.pdf (print on A5 landscape, 100% scale) and {base}.png')


def cmd_scan(a):
    img = cv2.imread(a.photo)
    if img is None:
        sys.exit(f'Cannot open {a.photo}')
    res = read_slip(img)
    if not res['ok']:
        sys.exit('RETAKE: ' + res['error'])
    if not res['patient']:
        print('QR not read. Handwritten slip mode: the OP number and name are typed once.')
        op = a.op or input('OP number: ').strip()
        res['patient'] = {'op': op, 'name': '', 'age': '', 'sex': ''}
    name = a.name
    if not res['patient']['name'] and not name:
        name = input('Name is not on the QR. Type it once: ').strip() if sys.stdin.isatty() else ''
    if a.interactive:
        for b in res['boxes']:
            if b['state'] == 'unsure':
                why = 'X beside the box' if b['cancel_mark'] else 'unclear mark'
                ans = input(f"Box {b['i'] + 1} {b['test']} ({why}) - order it? [y/n] ").strip().lower()
                b['state'] = 'ticked' if ans.startswith('y') else 'empty'
    order, info = make_order(res, name)
    if order is None:
        sys.exit(info)
    print(json.dumps({k: v for k, v in order.items()}, indent=2))
    print(f'\nOpen the collection screen: {os.path.join(info, "collection_screen.html")}')


def cmd_tube(a):
    con = db()
    code = a.code
    if a.image:
        img = cv2.imread(a.image)
        code, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
        if not code:
            sys.exit('Tube label not read - rescan or type the printed number.')
    active = con.execute('SELECT order_id, op FROM orders WHERE active=1').fetchone()
    if not active:
        sys.exit('No patient on screen. Scan a slip first.')
    row = con.execute('SELECT order_id FROM samples WHERE sample_id=?', (code,)).fetchone()
    if not row or row[0] != active[0]:
        print(f'\a\033[91mMISMATCH: tube {code} does not belong to OP {active[1]} on screen. Do not use this tube.\033[0m')
        log(con, 'mismatch', active[0], {'tube': code}); con.commit()
        sys.exit(2)
    con.execute('UPDATE samples SET scanned=1 WHERE sample_id=?', (code,))
    log(con, 'tube_ok', active[0], {'tube': code})
    left = con.execute('SELECT COUNT(*) FROM samples WHERE order_id=? AND scanned=0', (active[0],)).fetchone()[0]
    con.commit()
    p = dict(zip(['op', 'name', 'age', 'sex'], con.execute('SELECT op,name,age,sex FROM patients WHERE op=?', (active[1],)).fetchone()))
    samples = [{'sample_id': s, 'tube': t, 'tests': json.loads(ts), 'scanned': sc}
               for s, t, ts, sc in con.execute('SELECT sample_id,tube,tests,scanned FROM samples WHERE order_id=?', (active[0],))]
    order = json.load(open(os.path.join(OUT, active[0], 'order.json')))
    screen_html(os.path.join(OUT, active[0], 'collection_screen.html'), active[0], p, samples,
                [{'i': u['box'] - 1, 'test': u['test'], 'cancel_mark': u['x_beside_box']} for u in order['needs_check']],
                order['consent'])
    print(f'\033[92mMATCH: {code} belongs to OP {active[1]}.\033[0m ' + ('All tubes matched - Next unlocked.' if left == 0 else f'{left} tube(s) left.'))


# ------------------------------------------------------------------ self-test on synthetic photos
def synth_photo(rnd, setup):
    p = {'op': f'2026-{rnd.randint(100000, 999999)}', 'name': rnd.choice(['Ramesh Kumar', 'Sunita Devi', 'Mohd. Arif', 'Lakshmi P']),
         'age': str(rnd.randint(18, 80)), 'sex': rnd.choice('MF')}
    n = rnd.randint(1, 4)
    chosen = rnd.sample(range(12), n)
    pens = {'black': (25, 25, 30), 'blue': (30, 60, 165), 'faint': (120, 135, 175)}
    pen = rnd.choices(list(pens), weights=[4, 4, 2])[0]
    marks, truth = [], ['empty'] * 12
    for i in chosen:
        cancel = rnd.random() < 0.08
        marks.append((i, rnd.choice(['check', 'check', 'cross', 'slash', 'scribble']), pens[pen], cancel))
        truth[i] = 'cancel' if cancel else 'ticked'
    S = 10
    slip = np.array(render_slip(p, S=S, ticks=marks))[:, :, ::-1].copy()
    H, W = slip.shape[:2]
    cw, ch = 2600, 1950
    bg = np.full((ch, cw, 3), (rnd.randint(60, 120), rnd.randint(90, 140), rnd.randint(120, 170)), np.uint8)
    bg = cv2.add(bg, np.random.default_rng(rnd.randint(0, 10 ** 6)).integers(0, 25, (ch, cw, 3), dtype=np.uint8))
    tilt = {'stand': 0.03, 'phone': 0.08, 'hard': 0.14}[setup]
    scale = rnd.uniform(0.62, 0.82) * cw / W
    cx, cy = cw / 2 + rnd.uniform(-120, 120), ch / 2 + rnd.uniform(-90, 90)
    ang = np.deg2rad(rnd.uniform(-6, 6) if setup != 'hard' else rnd.uniform(-15, 15))
    src = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
    dst = []
    for x, y in src:
        x, y = (x - W / 2) * scale, (y - H / 2) * scale
        x, y = x * np.cos(ang) - y * np.sin(ang), x * np.sin(ang) + y * np.cos(ang)
        dst.append([cx + x + rnd.uniform(-1, 1) * tilt * W * scale, cy + y + rnd.uniform(-1, 1) * tilt * H * scale])
    M = cv2.getPerspectiveTransform(src, np.float32(dst))
    warped = cv2.warpPerspective(slip, M, (cw, ch))
    mask = cv2.warpPerspective(np.full((H, W), 255, np.uint8), M, (cw, ch))
    img = np.where(mask[:, :, None] > 0, warped, bg).astype(np.float32)
    yy, xx = np.mgrid[0:ch, 0:cw].astype(np.float32)
    dark = {'stand': 0.12, 'phone': 0.3, 'hard': 0.6}[setup]
    shade = 1 - dark * np.clip((xx * rnd.uniform(-1, 1) + yy * rnd.uniform(-1, 1)) / (cw + ch) + 0.5, 0, 1)
    light = {'stand': 1.0, 'phone': rnd.uniform(0.8, 1.0), 'hard': rnd.uniform(0.45, 0.75)}[setup]
    img = img * shade[:, :, None] * light
    img += np.random.default_rng(rnd.randint(0, 10 ** 6)).normal(0, {'stand': 3, 'phone': 6, 'hard': 10}[setup], img.shape)
    img = np.clip(img, 0, 255).astype(np.uint8)
    blur = {'stand': rnd.uniform(0, 0.8), 'phone': rnd.uniform(0.3, 1.6), 'hard': rnd.uniform(1.0, 2.6)}[setup]
    if blur > 0.2:
        img = cv2.GaussianBlur(img, (0, 0), blur)
    ok, enc = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, rnd.randint(60, 90)])
    return cv2.imdecode(enc, cv2.IMREAD_COLOR), p, truth


def cmd_selftest(a):
    rnd = random.Random(a.seed)
    random.seed(a.seed)
    np.random.seed(a.seed)
    report = {}
    for setup in a.setups.split(','):
        n = found = qr_ok = right = 0
        taps = wrong_boxes = 0
        for k in range(a.n):
            img, p, truth = synth_photo(rnd, setup)
            n += 1
            res = read_slip(img)
            if not res['ok']:
                continue
            found += 1
            pat = res['patient']
            if pat and pat['op'] == p['op']:
                qr_ok += 1
            elif pat:
                print('WRONG OP read!', pat, p)
            wrong = 0
            for b, t in zip(res['boxes'], truth):
                if b['state'] == 'unsure':
                    taps += 1
                elif t == 'cancel':
                    wrong += b['state'] == 'ticked'
                elif b['state'] != t:
                    wrong += 1
            wrong_boxes += wrong
            right += (wrong == 0 and pat is not None and pat['op'] == p['op'])
            if a.save and k < 3:
                os.makedirs(os.path.join(OUT, 'selftest'), exist_ok=True)
                cv2.imwrite(os.path.join(OUT, 'selftest', f'{setup}_{k}.jpg'), img)
        report[setup] = {'photos': n, 'marks_found_%': round(100 * found / n, 1), 'qr_read_%': round(100 * qr_ok / n, 1),
                         'taps_per_slip': round(taps / max(found, 1), 2), 'wrong_boxes': wrong_boxes,
                         'slips_fully_right_after_check_%': round(100 * right / n, 1)}
        print(setup, report[setup])
    os.makedirs(OUT, exist_ok=True)
    json.dump(report, open(os.path.join(OUT, 'selftest_report.json'), 'w'), indent=2)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='cmd', required=True)
    s = sp.add_parser('make-slip'); s.add_argument('--op', required=True); s.add_argument('--name'); s.add_argument('--age'); s.add_argument('--sex')
    s.set_defaults(f=cmd_make_slip)
    s = sp.add_parser('scan'); s.add_argument('photo'); s.add_argument('--name'); s.add_argument('--op')
    s.add_argument('-i', '--interactive', action='store_true', help='ask y/n for each unsure box (the 1-tap check)')
    s.set_defaults(f=cmd_scan)
    s = sp.add_parser('tube'); s.add_argument('--code', help='sample number (typed or from a USB scanner)'); s.add_argument('--image')
    s.set_defaults(f=cmd_tube)
    s = sp.add_parser('selftest'); s.add_argument('--n', type=int, default=100); s.add_argument('--seed', type=int, default=1)
    s.add_argument('--setups', default='stand,phone,hard'); s.add_argument('--save', action='store_true')
    s.set_defaults(f=cmd_selftest)
    a = ap.parse_args()
    a.f(a)


if __name__ == '__main__':
    main()
