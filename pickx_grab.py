#!/usr/bin/env python3
"""
pickx_grab.py — Récupère la grille Pickx et l'écrit au format XMLTV.

Remplace le grabber iptv-org/epg, cassé depuis le 21 octobre 2026 :
Pickx a migré vers tv.pickx.be et l'ancienne API (px-epg.azureedge.net,
avec son numéro de version caché dans la page) ne répond plus.

Nouvelle source : l'API publique du guide TV de tv.pickx.be.
  - TRAY/LIVECHANNELS : liste des chaînes et leurs identifiants
  - TRAY/EPG?filter_day=N&filter_channelIds=… : programmes du jour N
    (0 = aujourd'hui, 1 = demain… jusqu'à 7)

Le XML produit a la même forme que celui d'iptv-org : les scripts qui
le lisent (enquetes.py et les Radars) n'ont rien à changer.

Les chaînes sont lues dans pickx_channels.xml (le texte de chaque
<channel> est le nom affiché). Le nouvel identifiant Pickx est retrouvé
par le nom ; un attribut ntv_id="1005" sur la balise force la
correspondance si le nom ne suffit pas.

Garde-fou : si aucun programme n'est récupéré, le script s'arrête en
erreur. Le workflow s'interrompt alors avant le commit, et l'appli
garde les données de la veille au lieu d'afficher une grille vide.

Bibliothèque standard uniquement.
Usage : python3 pickx_grab.py --channels=pickx_channels.xml --days=7 \
                              --output=/tmp/pickx_guide.xml
"""

import argparse
import json
import sys
import time
import unicodedata
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

API = "https://api.prod-ntvbe.tvapi-web.pickx.be/AGL/1.5/A/FRA/WEB/PXS"

# Paramètres repris tels quels de la page tv.pickx.be (visiteur non connecté)
LIVECHANNELS_PARAMS = {
    "filter_regionId": "4",
    "filter_technicalPackages": "136,145,154,160,193,196,214,217,223,226,232",
    "filter_isAdult": "false",
    "sortOrder": "asc",
}

HEADERS = {
    "Origin": "https://tv.pickx.be",
    "Referer": "https://tv.pickx.be/",
    "User-Agent": "Mozilla/5.0 (compatible; radar-connectes)",
    "Accept": "application/json",
}


# ============================================================
# RÉSEAU
# ============================================================

def get_json(path, params, tentatives=3):
    """GET sur l'API Pickx, avec quelques nouvelles tentatives."""
    url = f"{API}{path}?{urlencode(params)}"
    derniere_erreur = None
    for essai in range(1, tentatives + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8"))
            if data.get("resultCode") != "OK":
                raise RuntimeError(f"{data.get('errorDescription')} {data.get('message')}")
            return data.get("resultObj") or {}
        except Exception as e:  # réseau, JSON, réponse KO
            derniere_erreur = e
            print(f"   ⚠️  essai {essai}/{tentatives} : {e}")
            time.sleep(2 * essai)
    raise RuntimeError(f"Échec de {path} : {derniere_erreur}")


# ============================================================
# CHAÎNES
# ============================================================

def normalize(s):
    """« RTBF La Une » et « La Une » -> « la une » ; « RTBF Tipik TV » -> « tipik »."""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = " ".join(s.lower().replace("-", " ").split())
    for prefixe in ("rtbf ",):
        if s.startswith(prefixe):
            s = s[len(prefixe):]
    for suffixe in (" hd", " tv"):
        if s.endswith(suffixe):
            s = s[: -len(suffixe)]
    return s.replace(" ", "")


def load_wanted(path):
    """Lit pickx_channels.xml -> liste de dicts {name, xmltv_id, ntv_id}."""
    root = ET.parse(path).getroot()
    wanted = []
    for ch in root.findall("channel"):
        name = (ch.text or "").strip()
        wanted.append({
            "name": name,
            "xmltv_id": ch.get("xmltv_id") or name,
            "ntv_id": ch.get("ntv_id"),
        })
    return wanted


def fetch_livechannels():
    """Toutes les chaînes Pickx -> dict {id: nom}."""
    chaines = {}
    debut = 0
    while True:
        params = dict(LIVECHANNELS_PARAMS, **{"from": debut, "to": debut + 99})
        obj = get_json("/TRAY/LIVECHANNELS", params)
        lot = obj.get("containers") or []
        for c in lot:
            meta = c.get("metadata") or {}
            nom = meta.get("channelName") or meta.get("title") or ""
            chaines[str(c.get("id"))] = nom
        if len(lot) < 100:
            return chaines
        debut += 100


def resolve_channels(wanted, livechannels):
    """Associe chaque chaîne voulue à son identifiant Pickx."""
    par_nom = {}
    for cid, nom in livechannels.items():
        par_nom.setdefault(normalize(nom), cid)

    resolved, missing = [], []
    for w in wanted:
        cid = w["ntv_id"] or par_nom.get(normalize(w["name"]))
        if cid:
            resolved.append(dict(w, ntv_id=cid))
            print(f"   ✓ {w['name']} -> {cid} ({livechannels.get(cid, '?')})")
        else:
            missing.append(w["name"])
            print(f"   ✗ {w['name']} : introuvable chez Pickx")
    return resolved, missing


# ============================================================
# PROGRAMMES
# ============================================================

def xmltv_time(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def add_programme(tv, xmltv_id, meta):
    start, stop = meta.get("airingStartTime"), meta.get("airingEndTime")
    title = (meta.get("title") or "").strip()
    if not (start and stop and title):
        return False

    p = ET.SubElement(tv, "programme", {
        "start": xmltv_time(start), "stop": xmltv_time(stop), "channel": xmltv_id,
    })
    ET.SubElement(p, "title", {"lang": "fr"}).text = title

    sous_titre = (meta.get("episodeTitle") or "").strip()
    if sous_titre:
        ET.SubElement(p, "sub-title", {"lang": "fr"}).text = sous_titre

    desc = (meta.get("longDescription") or meta.get("shortDescription") or "").strip()
    if desc:
        ET.SubElement(p, "desc", {"lang": "fr"}).text = desc

    # Générique : lu par les radars Réalisation et Interprétation
    def noms(champ):
        valeur = meta.get(champ) or []
        if isinstance(valeur, str):
            valeur = valeur.split(",")
        return [n.strip() for n in valeur if n and n.strip()]

    realisateurs, acteurs = noms("directors"), noms("actors")
    if realisateurs or acteurs:
        credits = ET.SubElement(p, "credits")
        for nom in realisateurs:
            ET.SubElement(credits, "director").text = nom
        for nom in acteurs:
            ET.SubElement(credits, "actor").text = nom

    for genre in meta.get("genres") or []:
        ET.SubElement(p, "category", {"lang": "en"}).text = genre

    # Même double système qu'avant : xmltv_ns (indexé à zéro) et onscreen
    saison, episode = meta.get("season"), meta.get("episodeNumber")
    if episode:
        s_ns = str(saison - 1) if saison else ""
        ET.SubElement(p, "episode-num", {"system": "xmltv_ns"}).text = f"{s_ns}.{episode - 1}.0/1"
        onscreen = f"S{saison:02d}E{episode:02d}" if saison else f"E{episode:02d}"
        ET.SubElement(p, "episode-num", {"system": "onscreen"}).text = onscreen
    return True


# ============================================================
# MAIN
# ============================================================

def main():
    ap = argparse.ArgumentParser(description="Grille Pickx -> XMLTV")
    ap.add_argument("--channels", default="pickx_channels.xml")
    ap.add_argument("--days", type=int, default=7,
                    help="Jours à récupérer après aujourd'hui (0 à 7)")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    wanted = load_wanted(args.channels)
    print(f"📡 {len(wanted)} chaîne(s) demandée(s)")
    livechannels = fetch_livechannels()
    print(f"   {len(livechannels)} chaînes chez Pickx")
    resolved, missing = resolve_channels(wanted, livechannels)
    if not resolved:
        sys.exit("❌ Aucune chaîne reconnue : rien à récupérer.")

    tv = ET.Element("tv", {"generator-info-name": "pickx_grab.py"})
    for w in resolved:
        ch = ET.SubElement(tv, "channel", {"id": w["xmltv_id"]})
        ET.SubElement(ch, "display-name").text = w["name"]

    by_ntv = {w["ntv_id"]: w["xmltv_id"] for w in resolved}
    ids = ",".join(by_ntv)
    total, par_chaine = 0, {w["name"]: 0 for w in resolved}
    noms = {w["xmltv_id"]: w["name"] for w in resolved}

    for jour in range(0, min(args.days, 7) + 1):
        try:
            obj = get_json("/TRAY/EPG", {"filter_day": jour, "filter_channelIds": ids})
        except RuntimeError as e:
            print(f"   ⚠️  jour {jour} ignoré : {e}")
            continue
        n_jour = 0
        for chaine in obj.get("containers") or []:
            xmltv_id = by_ntv.get(str(chaine.get("id")))
            if not xmltv_id:
                continue
            for item in chaine.get("containers") or []:
                if add_programme(tv, xmltv_id, item.get("metadata") or {}):
                    n_jour += 1
                    par_chaine[noms[xmltv_id]] += 1
        total += n_jour
        print(f"   jour {jour} : {n_jour} programmes")

    print("\n📊 Par chaîne : " + ", ".join(f"{k} {v}" for k, v in par_chaine.items()))
    vides = [k for k, v in par_chaine.items() if v == 0]
    if missing or vides:
        print(f"⚠️  Sans programme : {', '.join(missing + vides)}")

    # Garde-fou : une grille vide ne doit jamais écraser les données publiées
    if total == 0:
        sys.exit("❌ Aucun programme récupéré : on s'arrête sans rien publier.")

    ET.ElementTree(tv).write(args.output, encoding="utf-8", xml_declaration=True)
    print(f"\n💾 {total} programmes écrits dans {args.output}")


if __name__ == "__main__":
    main()
