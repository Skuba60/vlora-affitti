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

# File dove salviamo gli annunci gia' visti (cosi' non li segnaliamo due volte)
SEEN_FILE = Path("seen.json")

# Le pagine da controllare. Sono gia' filtrate per Vlore + affitto lungo periodo.
# Se in futuro vuoi aggiungere un'altra citta' o un altro sito, si aggiunge
# qui una nuova riga.
SITES = {
    "merrjep": {
        "url": "https://www.merrjep.al/njoftime/imobiliare-vendbanime/apartamente/vlore/q-me-qera-afatgjate",
        "label": "MerrJep.al",
    },
    "njoftime_al": {
        "url": "https://www.njoftime.al/apartamente-me-qera-afatgjate-vlore/l-al-c-4.html",
        "label": "Njoftime.al",
    },
    "duashpi": {
        "url": "https://duashpi.al/it/casa-in-affitto-a-vlore",
        "label": "DuaShpi.al",
    },
}

HEADERS = {
    # Ci presentiamo come un normale browser, altrimenti alcuni siti bloccano
    # le richieste automatiche.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "sq,it;q=0.9,en;q=0.8",
}

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# TELEGRAM_CHAT_ID puo' contenere UNO o PIU' ID, separati da virgola.
# Esempio con un solo destinatario:  TELEGRAM_CHAT_ID=123456789
# Esempio con due destinatari:       TELEGRAM_CHAT_ID=123456789,987654321
_raw_chat_ids = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_CHAT_IDS = [cid.strip() for cid in _raw_chat_ids.split(",") if cid.strip()]


# ---------------------------------------------------------------------------
# FUNZIONI
# ---------------------------------------------------------------------------

def load_seen():
    """Legge il file con gli annunci gia' visti (se esiste)."""
    if SEEN_FILE.exists():
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    # Prima esecuzione: nessun sito ha ancora annunci salvati
    return {site_key: [] for site_key in SITES}


def save_seen(seen):
    """Salva il file con gli annunci visti, per la prossima esecuzione."""
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)


def extract_listings(html, base_url):
    """
    Estrae dagli annunci della pagina: id univoco, titolo, link.

    Nota tecnica: i siti di annunci albanesi cambiano ogni tanto la grafica
    del sito, ma il link a ogni singolo annuncio resta sempre un indirizzo
    unico (contiene un numero identificativo). Per questo cerchiamo tutti i
    link della pagina che sembrano puntare a un annuncio, invece di basarci
    su nomi di "classi grafiche" che potrebbero cambiare da un giorno
    all'altro e rompere lo script.
    """
    soup = BeautifulSoup(html, "html.parser")
    listings = {}

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]

        # Deve essere un link che porta a un singolo annuncio (contiene un
        # numero lungo, tipico id dell'annuncio) e non a una pagina generica
        # del sito (categoria, homepage, login, ecc.)
        if not re.search(r"\d{5,}", href):
            continue
        if any(bad in href.lower() for bad in ["login", "register", "kerko", "search", "category", "categori"]):
            continue

        # Rendiamo il link completo se e' relativo (es. "/njoftime/...")
        if href.startswith("/"):
            full_url = base_url.split("/")[0] + "//" + base_url.split("/")[2] + href
        elif href.startswith("http"):
            full_url = href
        else:
            continue

        # Usiamo il link stesso come identificativo univoco dell'annuncio
        listing_id = full_url.split("?")[0].rstrip("/")

        title = a_tag.get_text(strip=True)
        if not title:
            title = a_tag.get("title", "").strip()
        if not title:
            title = "(annuncio senza titolo leggibile, apri il link)"

        # Alcuni annunci compaiono con piu' link nella stessa card (immagine
        # + titolo): teniamo solo la prima versione trovata, preferendo
        # quella con un titolo piu' lungo/descrittivo.
        if listing_id not in listings or len(title) > len(listings[listing_id]):
            listings[listing_id] = title

    return listings  # dict: {id_annuncio: titolo}


def fetch_site(site_key, site_info):
    """Scarica la pagina di un sito e ne estrae gli annunci."""
    try:
        resp = requests.get(site_info["url"], headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"[ATTENZIONE] Non sono riuscito a leggere {site_info['label']}: {e}")
        return {}

    return extract_listings(resp.text, site_info["url"])


def send_telegram_message(text):
    """Invia un messaggio a TUTTI i destinatari configurati in TELEGRAM_CHAT_IDS."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_IDS:
        print("[ATTENZIONE] TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID non configurati, salto invio.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    for chat_id in TELEGRAM_CHAT_IDS:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        try:
            r = requests.post(url, data=payload, timeout=15)
            if not r.ok:
                print(f"[ATTENZIONE] Telegram ha risposto con errore per {chat_id}: {r.text}")
        except Exception as e:
            print(f"[ATTENZIONE] Invio Telegram fallito per {chat_id}: {e}")


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
                time.sleep(1)  # piccola pausa per non intasare Telegram

        # Aggiorniamo la lista di annunci visti con quelli di oggi
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
