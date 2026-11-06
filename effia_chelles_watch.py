#!/usr/bin/env python3
"""Surveille les abonnements Navigo gratuits du P+R gare de Chelles (EFFIA)."""
import json
import os
import re
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

URL = ('https://www.effia.com/parking/parking-gare-de-chelles-pr-effia'
       '?orderType=subscription&vehicle=AUTO&qty=1'
       '&entry=2026-12-01T00%3A00&exit=2026-12-31T00%3A00')
NTFY_URL = os.environ.get('NTFY_URL')  # optionnel
STATE = Path.home() / '.effia_chelles_state.json'
SOLD_OUT = re.compile('Plus d.abonnement disponible', re.I)
SUBSCRIBE = re.compile('^S.abonner$', re.I)
DEBUG = '--debug' in sys.argv


def dump(page):
    page.screenshot(path='debug.png', full_page=True)
    Path('debug.txt').write_text(page.inner_text('body'), encoding='utf-8')


def check():
    """Retourne {libelle de l'offre gratuite: place disponible (bool)}."""
    result = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(locale='fr-FR')
        try:
            page.set_default_timeout(15000)
            page.goto(URL, wait_until='domcontentloaded')
            print('page chargee')
            sel = page.locator('select').filter(
                has=page.locator('option', has_text='Gratuit')).first
            sel.wait_for(state='attached')
            print('liste trouvee')
            texts = sel.locator('option').all_text_contents()
            free = [(i, t.strip()) for i, t in enumerate(texts) if 'Gratuit' in t]
            if not free:
                raise RuntimeError('Aucune offre Gratuit dans la liste')
            for i, label in free:
                sel.select_option(index=i, force=True)
                page.wait_for_timeout(3000)
                sold_out = page.get_by_text(SOLD_OUT).filter(visible=True).count() > 0
                can_subscribe = page.get_by_text(SUBSCRIBE).filter(visible=True).count() > 0
                result[label] = (not sold_out) and can_subscribe
            if DEBUG:
                dump(page)
        except Exception:
            dump(page)
            raise
        finally:
            browser.close()
    return result


def send_mail(title, msg):
    host = os.environ.get('SMTP_HOST')
    if not host:
        return
    user = os.environ['SMTP_USER']
    port = int(os.environ.get('SMTP_PORT') or 465)
    m = EmailMessage()
    m['Subject'] = title
    m['From'] = user
    m['To'] = os.environ.get('MAIL_TO') or user
    m.set_content(msg)
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=20)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
        server.starttls()
    with server:
        server.login(user, os.environ['SMTP_PASS'])
        server.send_message(m)


def notify(title, msg, priority='default'):
    print(f'[notif] {title} - {msg}')
    send_mail(title, msg)
    if NTFY_URL:
        requests.post(NTFY_URL, data=msg.encode('utf-8'),
                      headers={'Title': title, 'Priority': priority}, timeout=15)


def main():
    if '--test' in sys.argv:
        notify('Chelles P+R : test', 'Notification de test OK')
        return
    last = json.loads(STATE.read_text()) if STATE.exists() else {}
    try:
        current = check()
    except Exception as e:
        print(f'ERREUR : {e}')
        # Sur GitHub, l'echec du job est deja signale par e-mail
        if 'error' not in last and not os.environ.get('GITHUB_ACTIONS'):
            notify('Chelles P+R : script en erreur', str(e)[:300])
        STATE.write_text(json.dumps({'error': str(e)[:300]}))
        sys.exit(1)
    if DEBUG:
        print(json.dumps(current, ensure_ascii=False, indent=2))
    for label, ok in current.items():
        if ok and not last.get(label):
            notify('Chelles P+R : place libre !',
                   f'{label} est disponible : {URL}', 'urgent')
    STATE.write_text(json.dumps(current))


if __name__ == '__main__':
    main()
