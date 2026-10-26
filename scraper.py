"""
Monitor annunci affitti a lungo periodo - Vlore (Albania)
===========================================================

Cosa fa questo script, in parole semplici:
1. Va a visitare le pagine di ricerca dei siti configurati (portali generali
   + agenzie immobiliari)
2. Legge la lista di annunci trovati
3. Per le agenzie (che mostrano vendite E affitti insieme), tiene solo
   quelli di AFFITTO
4. Confronta questa lista con quella salvata l'ultima volta (file seen.json)
5. Se trova annunci NUOVI, li manda su Telegram
6. Aggiorna il file seen.json cosi' la prossima volta sa cosa ha gia' visto

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

# Parole che, se compaiono nel testo della scheda annuncio, indicano che
# e' un affitto (usate solo per le agenzie, che mischiano vendite e affitti)
RENT_KEYWORDS = ["qera", "qira", "muaj", "/month", "for rent", "to rent"]

SITES = {
    "merrjep": {
        "url": "https://www.merrjep.al/njoftime/imobiliare-vendbanime/apartamente/vlore/q-me-qera-afatgjate",
        "label": "MerrJep.al",
    },
    "duashpi": {
        "url": "https://duashpi.al/it/casa-in-affitto-a-vlore",
        "label": "DuaShpi.al",
    },
    "homezone": {
        "url": "https://homezone.al/it/properties/rent/vlore",
        "label": "Homezone.al",
    },
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "sq,it;q=0.9,en;q=0.8",
}

TELEGRAM_BOT_TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()


# ---------------------------------------------------------------------------
# FUNZIONI
# ---------------------------------------------------------------------------

def load_seen():
    """Legge il file con gli annunci gia' visti (se esiste)."""
    if SEEN_FILE.exists():
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for site_key, value in data.items():
            if isinstance(value, list):
                data[site_key] = {listing_id: "" for listing_id in value}
        return data
    return {site_key: {} for site_key in SITES}


def save_seen(seen):
    """Salva il file con gli annunci visti, per la prossima esecuzione."""
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)


def is_rent_listing(text):
    """Controlla se il testo della scheda annuncio indica un affitto."""
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in RENT_KEYWORDS)


def extract_listings(html, base_url, site_config):
    """
    Estrae dagli annunci della pagina: id univoco, titolo, link.

    Due modalita':
    - Portali generali (MerrJep, DuaShpi, Homezone): un link e' un annuncio
      se contiene un numero lungo nell'indirizzo (id dell'annuncio)
    - Agenzie (identificate da "link_contains" nella configurazione): un
      link e' un annuncio se il suo indirizzo contiene quella stringa
      (es. "/property/"), indipendentemente dal formato dell'id
    """
    soup = BeautifulSoup(html, "html.parser")
    listings = {}

    link_contains = site_config.get("link_contains")
    link_regex = site_config.get("link_regex")
    rent_only = site_config.get("rent_only", False)

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]

        if link_regex:
            if not re.search(link_regex, href.split("?")[0]):
                continue
        elif link_contains:
            if link_contains not in href:
                continue
        else:
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

        card_text = a_tag.get_text(" ", strip=True)
        title = card_text
        if not title:
            title = a_tag.get("title", "").strip()
        if not title:
            title = "(annuncio senza titolo leggibile, apri il link)"

        if rent_only and not is_rent_listing(card_text):
            continue  # e' una vendita, non un affitto: lo saltiamo

        if listing_id not in listings or len(title) > len(listings[listing_id]):
            listings[listing_id] = title[:200]  # teniamo il titolo ragionevolmente corto

    return listings


def fetch_site(site_key, site_info):
    try:
        resp = requests.get(site_info["url"], headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"[ATTENZIONE] Non sono riuscito a leggere {site_info['label']}: {e}")
        return None  # None = errore di lettura (diverso da "0 annunci trovati")

    return extract_listings(resp.text, site_info["url"], site_info)


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
    site_keys = list(SITES.keys())
    is_first_run = all(len(seen.get(k, {})) == 0 for k in site_keys)

    # Puliamo eventuali siti rimossi in passato dalla configurazione
    seen = {k: v for k, v in seen.items() if k in site_keys}

    total_new = 0

    for site_key, site_info in SITES.items():
        current_listings = fetch_site(site_key, site_info)

        # Se il sito non risponde, teniamo la memoria di prima: cosi' al
        # prossimo giro non ci arriva una raffica di falsi "nuovi annunci"
        if current_listings is None:
            print(f"{site_info['label']}: errore di lettura, mantengo i dati precedenti.")
            continue

        # Se prima c'erano annunci e ora ne troviamo 0, e' quasi certamente
        # un problema di lettura (sito cambiato/bloccato), non 0 annunci veri
        if not current_listings and seen.get(site_key):
            print(f"{site_info['label']}: 0 annunci trovati ma prima ce n'erano, "
                  "mantengo i dati precedenti.")
            continue

        already_seen_ids = set(seen.get(site_key, {}).keys())

        new_ids = [lid for lid in current_listings if lid not in already_seen_ids]

        print(f"{site_info['label']}: trovati {len(current_listings)} annunci, "
              f"di cui {len(new_ids)} nuovi.")

        if not already_seen_ids:
            print(f"{site_info['label']}: nessuna memoria precedente, salvo gli annunci "
                  "attuali senza inviare notifiche.")

        if not is_first_run and already_seen_ids:
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

        seen[site_key] = current_listings

    seen["_meta"] = {
        "last_run": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "site_labels": {k: v["label"] for k, v in SITES.items()},
    }

    if is_first_run:
        print("Prima esecuzione: ho salvato gli annunci attuali come 'gia' visti', "
              "senza inviare notifiche.")
        send_telegram_message(
            "✅ Il monitor annunci affitti Vlore e' attivo (ora anche con le agenzie).\n"
            "Ho salvato gli annunci attualmente online. "
            "Da domani ti avviso solo per i NUOVI annunci."
        )
    else:
        print(f"Totale nuovi annunci inviati: {total_new}")

    save_seen(seen)


if __name__ == "__main__":
    main()
