#!/usr/bin/env python3
"""
Radar Rugby
===========
Scanne une grille EPG Pickx (format XMLTV) et identifie les programmes
de rugby, via les mots-clés listés dans watchlist.json (titre / sous-titre
/ catégorie). Philosophie : filet large, faux positifs acceptés.

Usage :
    python3 radar.py --source /tmp/pickx_guide.xml
"""

import argparse
import json
import sys
from datetime import datetime, timezone


from xml.etree import ElementTree as ET


# Mapping des codes xmltv_id Pickx vers des noms de chaînes lisibles
CHANNEL_NAMES = {
    "TF1.fr@HD": "TF1",
    "France2.fr@HD": "France 2",
    "France3.fr@HD": "France 3",
    "France4.fr@HD": "France 4",
    "France5.fr@HD": "France 5",
    "arte.fr@HD": "Arte",
    "TV5MondeFranceBelgiumSwitzerlandMonaco.fr@HD": "TV5 Monde",
    "Action.fr@HD": "Action",
    "LaUne.be@HD": "La Une",
    "LaTrois.be@HD": "La Trois",
    "Tipik.be@HD": "Tipik",
    "RTLTVI.be@HD": "RTL TVI",
    "VRT1.be@HD": "VRT 1",
    "VRTCanvas.be@HD": "VRT Canvas",
    "VTM.be@HD": "VTM",
    "VTM2.be@HD": "VTM 2",
    "BBCOne.uk@HD": "BBC One",
    "BBCTwo.uk@HD": "BBC Two",
    "Rai1.it@HD": "RAI Uno",
    "Rai2.it@HD": "RAI Due",
    "Rai3.it@HD": "RAI Tre",
    "TVEInternacionalEuropeAsia.es@HD": "TVE International",
}


def load_watchlist(path="watchlist.json"):
    """Charge les mots-clés rugby et la table des compétitions."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    motscles = data.get("motscles", [])
    # Pré-normalisation : minuscules, sans espaces superflus
    motscles = [m.strip().lower() for m in motscles if m.strip()]
    competitions = data.get("competitions", {})
    competitions = {k.strip().lower(): v for k, v in competitions.items()}
    return motscles, competitions


def parse_datetime(dt_string):
    """Parse une date XMLTV (format '20260613210000 +0000') en ISO."""
    if not dt_string:
        return None
    try:
        # Garde uniquement la partie avant le fuseau si présent
        parts = dt_string.split()
        date_part = parts[0]
        # Format YYYYMMDDHHMMSS
        dt = datetime.strptime(date_part[:14], "%Y%m%d%H%M%S")
        # Si fuseau présent, l'appliquer
        if len(parts) > 1:
            tz = parts[1]
            sign = 1 if tz[0] == "+" else -1
            hours = int(tz[1:3])
            minutes = int(tz[3:5])
            offset = sign * (hours * 60 + minutes)
            from datetime import timedelta
            dt = dt.replace(tzinfo=timezone(timedelta(minutes=offset)))
        else:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, IndexError):
        return None


def detect_competition(haystack, competitions):
    """Renvoie le libellé de compétition le plus pertinent trouvé dans le texte.

    On parcourt la table dans l'ordre de déclaration (du plus spécifique au
    plus générique). Défaut : 'Rugby'.
    """
    for cle, libelle in competitions.items():
        if cle in haystack:
            return libelle
    return "Rugby"


def extract_programmes(xml_path, motscles, competitions):
    """Parse le XML et retourne les programmes contenant un mot-clé rugby."""
    tree = ET.parse(xml_path)
    root = tree.getroot()

    matches = []
    total_programmes = 0

    for prog in root.findall("programme"):
        total_programmes += 1

        title_el = prog.find("title")
        title = (title_el.text or "").strip() if title_el is not None else ""

        sub_el = prog.find("sub-title")
        subtitle = (sub_el.text or "").strip() if sub_el is not None else ""

        desc_el = prog.find("desc")
        description = (desc_el.text or "").strip() if desc_el is not None else ""

        # Catégorie : Pickx peut en lister plusieurs
        categories = []
        for c in prog.findall("category"):
            if c.text:
                categories.append(c.text.strip())
        category = " / ".join(categories)

        # Texte de recherche : titre + sous-titre + catégorie + description
        # (en minuscules). On inclut la description pour ne pas rater les matchs
        # dont le titre est générique mais dont le résumé nomme les équipes.
        haystack = " ".join([title, subtitle, category, description]).lower()

        # Cherche le premier mot-clé présent
        matched_keyword = None
        for mot in motscles:
            if mot in haystack:
                matched_keyword = mot
                break

        if not matched_keyword:
            continue

        competition = detect_competition(haystack, competitions)

        # Chaîne lisible
        channel_id = prog.get("channel", "")
        channel_name = CHANNEL_NAMES.get(channel_id, channel_id)

        matches.append({
            "start": parse_datetime(prog.get("start", "")),
            "stop": parse_datetime(prog.get("stop", "")),
            "channel": channel_name,
            "title": title,
            "subtitle": subtitle,
            "competition": competition,
            "matched_keyword": matched_keyword,
            "description": description,
            "category": category,
        })

    print(f"Total programmes analyses : {total_programmes}", file=sys.stderr)
    print(f"Matches rugby : {len(matches)}", file=sys.stderr)

    # Déduplication par (title, start, channel) : Pickx diffuse des variantes HD/SD/+1
    seen = set()
    deduped = []
    for m in matches:
        key = (m["title"], m["start"], m["channel"])
        if key not in seen:
            seen.add(key)
            deduped.append(m)

    # Tri par date de début
    deduped.sort(key=lambda m: m["start"] or "")

    return deduped


def main():
    parser = argparse.ArgumentParser(description="Radar Rugby - filtre EPG Pickx par mots-cles rugby")
    parser.add_argument("--source", default="/tmp/pickx_guide.xml", help="Chemin du XML Pickx")
    parser.add_argument("--watchlist", default="watchlist.json", help="Chemin de la watchlist")
    parser.add_argument("--output", default="radar.json", help="Fichier JSON de sortie")
    args = parser.parse_args()

    motscles, competitions = load_watchlist(args.watchlist)
    print(f"Mots-cles : {len(motscles)}", file=sys.stderr)

    matches = extract_programmes(args.source, motscles, competitions)

    # Calcul de la fenêtre temporelle observée
    if matches:
        dates = [m["start"][:10] for m in matches if m["start"]]
        window_start = min(dates) if dates else ""
        window_end = max(dates) if dates else ""
    else:
        today = datetime.now(timezone.utc).date().isoformat()
        window_start = today
        window_end = today

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_start": window_start,
        "window_end": window_end,
        "source": "Pickx via iptv-org/epg",
        "keywords_size": len(motscles),
        "keywords": motscles,
        "count": len(matches),
        "programmes": matches,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"radar.json ecrit : {len(matches)} programmes", file=sys.stderr)


if __name__ == "__main__":
    main()
