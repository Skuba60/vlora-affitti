"""
Monitor annunci affitti a lungo periodo - Vlore (Albania)
===========================================================

Cosa fa questo script, in parole semplici:
1. Va a visitare le pagine di ricerca (gia' filtrate per Vlore + affitto
   lungo periodo) di due siti: MerrJep.al e Njoftime.al
2. Legge la lista di annunci trovati
3. Confronta questa lista con quella salvata l'ultima volta (file seen.json)
4. Se trova annunci NUOVI (che non c'erano prima), li manda su Telegram
5. Aggiorna il file seen.json cosi' la prossima volta sa cosa ha gia' visto

Non serve capire il codice per usarlo: basta seguire il README.
"""

import json
import os
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# CONFIGURAZIONE
# ---------------------------------------------------------------------------

SEEN_FILE = Path("seen.json")

SITES = {
    "merrjep": {
        "url": "https://www.merrjep.al/njoftime/imobiliare-vendbanime/apartamente/vlore/q-me-qera-afatgjate",
        "label": "MerrJep.al",
    },
    "njoftime_al": {
        "url": "https://www.njoftime.al/apartamente-me-qera-afatgjate-vlore/l-al-c-4.html",
        "label": "Njoftime.al",
    },
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "sq,it;q=0.9,en;q=0.8",
}

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


# ---------------------------------------------------------------------------
# FUNZIONI
# ---------------------------------------------------------------------------

def load_seen():
    if SEEN_FILE.exists():
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {site_key: [] for site_key in SITES}


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)


def extract_listings(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    listings = {}

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]

        if not re.search(r"\d{5,}", href):
            continue
        if any(bad in href.lower() for bad in ["login", "register", "kerko", "search", "category", "categori"]):
            continue

        if href.startswith("/"):
            full_url = base_url.split("/")[0] + "//" + base_url.split("/")[2] + href
        elif href.startswith("http"):
            full_url = href
        else:
            continue

        listing_id = full_url.split("?")[0].rstrip("/")

        title = a_tag.get_text(strip=True)
        if not title:
            title = a_tag.get("title", "").strip()
        if not title:
            title = "(annuncio senza titolo leggibile, apri il link)"

        if listing_id not in listings or len(title) > len(listings[listing_id]):
            listings[listing_id] = title

    return listings


def fetch_site(site_key, site_info):
    try:
        resp = requests.get(site_info["url"], headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"[ATTENZIONE] Non sono riuscito a leggere {site_info['label']}: {e}")
        return {}

    return extract_listings(resp.text, site_info["url"])


def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[ATTENZIONE] TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID non configurati, salto invio.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, data=payload, timeout=15)
        if not r.ok:
            print(f"[ATTENZIONE] Telegram ha risposto con errore: {r.text}")
    except Exception as e:
        print(f"[ATTENZIONE] Invio Telegram fallito: {e}")


# ---------------------------------------------------------------------------
# PROGRAMMA PRINCIPALE
# ---------------------------------------------------------------------------

def main():
    seen = load_seen()
    is_first_run = all(len(v) == 0 for v in seen.values())

    total_new = 0

    for site_key, site_info in SITES.items():
        current_listings = fetch_site(site_key, site_info)
        already_seen_ids = set(seen.get(site_key, []))

        new_ids = [lid for lid in current_listings if lid not in already_seen_ids]

        print(f"{site_info['label']}: trovati {len(current_listings)} annunci, "
              f"di cui {len(new_ids)} nuovi.")

        if not is_first_run:
            for listing_id in new_ids:
                title = current_listings[listing_id]
                message = (
                    f"🏠 <b>Nuovo annuncio - {site_info['label']}</b>\n"
                    f"{title}\n"
                    f"{listing_id}"
                )
                send_telegram_message(message)
                total_new += 1
                time.sleep(1)

        seen[site_key] = list(current_listings.keys())

    if is_first_run:
        print("Prima esecuzione: ho salvato gli annunci attuali come 'gia' visti', "
              "senza inviare notifiche (altrimenti ne avresti ricevute centinaia in una volta).")
        send_telegram_message(
            "✅ Il monitor annunci affitti Vlore e' attivo.\n"
            "Ho salvato gli annunci attualmente online. "
            "Da domani ti avviso solo per i NUOVI annunci."
        )
    else:
        print(f"Totale nuovi annunci inviati: {total_new}")

    save_seen(seen)


if __name__ == "__main__":
    main()
