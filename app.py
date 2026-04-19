import atexit
import io
import json
import os
import pickle
import queue
import re
import threading
import time
import unicodedata
import zipfile
from functools import lru_cache

import requests
from flask import Flask, jsonify, render_template, request, send_file, Response, stream_with_context
from flask_cors import CORS
from staticmap import StaticMap, CircleMarker

import config

app = Flask(__name__, template_folder=config.TEMPLATE_DIR)
CORS(app)

# Clients SSE connectés (notifications quota)
_sse_clients = []
_sse_lock = threading.Lock()

_quota_notified = False


def push_notification(message, level="error"):
    """Envoie une notification à tous les clients SSE connectés."""
    data = json.dumps({"message": message, "level": level})
    with _sse_lock:
        for q in list(_sse_clients):
            try:
                q.put_nowait(data)
            except queue.Full:
                pass


# --- Paramètres ---

DEFAULT_SETTINGS = {
    "username":   config.DEFAULT_USERNAME,
    "remember":   False,
    "searchlang": config.DEFAULT_SEARCHLANG,
    "lang":       config.DEFAULT_LANG,
    "maxRows":    config.DEFAULT_MAX_ROWS,
    "fuzzy":      config.DEFAULT_FUZZY,
    "threshold":  config.DEFAULT_THRESHOLD,
}

_settings: dict = {}
_settings_lock = threading.Lock()


def load_settings():
    """Charge les paramètres depuis settings.json, avec fallback sur DEFAULT_SETTINGS."""
    if os.path.exists(config.SETTINGS_FILE):
        with open(config.SETTINGS_FILE, "r", encoding="utf-8") as f:
            stored = json.load(f)
        return {**DEFAULT_SETTINGS, **stored}
    return DEFAULT_SETTINGS.copy()


def get_settings():
    """Retourne les paramètres depuis le cache mémoire, relit le JSON si nécessaire."""
    with _settings_lock:
        if not _settings:
            _settings.update(load_settings())
    return _settings


def save_settings(settings):
    """Sauvegarde les paramètres dans settings.json et invalide le cache mémoire."""
    with open(config.SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    with _settings_lock:
        _settings.clear()


# --- Propriétés disponibles pour l'extension de données ---

EXTENSION_PROPERTIES = [
    {"id": "name",         "name": "Nom (localisé)"},
    {"id": "toponymName",  "name": "Nom officiel"},
    {"id": "lat",          "name": "Latitude"},
    {"id": "lng",          "name": "Longitude"},
    {"id": "countryCode",  "name": "Code pays"},
    {"id": "countryName",  "name": "Pays"},
    {"id": "adminName1",   "name": "Adm1"},
    {"id": "adminName2",   "name": "Adm2"},
    {"id": "adminName3",   "name": "Adm3"},
    {"id": "adminName4",   "name": "Adm4"},
    {"id": "adminName5",   "name": "Adm5"},
    {"id": "continentCode","name": "Continent"},
    {"id": "fcode",        "name": "Code type de lieu"},
    {"id": "fcodeName",    "name": "Type de lieu"},
    {"id": "fcl",          "name": "Classe"},
    {"id": "population",   "name": "Population"},
    {"id": "wikipediaURL", "name": "Wikipedia"},
    {"id": "geonameId",    "name": "GeoNames ID"},
]

PROPERTY_IDS = {p["id"] for p in EXTENSION_PROPERTIES}


# --- Scoring ---

def _normalize(text):
    text = text.lower().strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip()


def _bigrams(s):
    s = _normalize(s)
    return set(s[i:i+2] for i in range(len(s) - 1)) if len(s) >= 2 else set()


def _dice(a, b):
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return 0.0
    return 100.0 * 2 * len(ba & bb) / (len(ba) + len(bb))


def _candidate_names(g, searchlang):
    """Retourne la liste de noms candidats pour le scoring d'un résultat GeoNames."""
    names = []
    seen = set()

    def add(n):
        if n and n not in seen:
            seen.add(n)
            names.append(n)

    add(g.get("name"))
    add(g.get("toponymName"))

    alts = g.get("alternateNames") or []
    lang_alts = [a["name"] for a in alts if a.get("lang") == searchlang and a.get("name")]
    if lang_alts:
        for n in lang_alts:
            add(n)
    else:
        for a in alts:
            if a.get("lang") not in ("link", "wkdt", "post", "iata", "icao", "faac") and a.get("name"):
                add(a["name"])

    return names


def score_candidate(query, g, searchlang):
    """Score un résultat GeoNames contre la query source.

    1. Exact sur name ou toponymName          → 100, match=True
    2. Exact sur une variante                 → 90,  match=True
    3. Tous les mots de query dans name/topo  → 75,  match=False
    4. Tous les mots de query dans une var.   → 65,  match=False
    5. Meilleur Dice sur tous les noms        → Dice, match=False
    """
    names = _candidate_names(g, searchlang)
    if not names:
        return 0, False

    q = _normalize(query)
    main_names = [n for n in names[:2] if n]
    variants = names[2:]

    for n in main_names:
        if q == _normalize(n):
            return 100, True
    for n in variants:
        if q == _normalize(n):
            return 90, True

    q_words = set(q.split())
    for n in main_names:
        if q_words and q_words.issubset(set(_normalize(n).split())):
            return 75, False
    for n in variants:
        if q_words and q_words.issubset(set(_normalize(n).split())):
            return 65, False

    best = max((_dice(query, n) for n in names), default=0.0)
    return int(best), False


# --- Cache GeoNames ---
_RECORD_CACHE_MAX = 2_000_000
_SEARCH_CACHE_MAX = 500_000
_cache_lock = threading.Lock()


def _load_pkl(path):
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                return pickle.load(f)
        except Exception:
            pass
    return {}


def _save_pkl(path, data, label):
    try:
        with open(path, "wb") as f:
            pickle.dump(data, f)
        print(f"[{label}] {len(data)} entrées sauvegardées.")
    except Exception as e:
        print(f"[{label}] Erreur sauvegarde : {e}")


def _save_all():
    _save_pkl(config.RECORD_FILE, _record_cache, "record_cache")
    _save_pkl(config.SEARCH_FILE, _search_cache, "search_cache")


# _record_cache : geonameId (str) → notice complète
# _search_cache : (query_normalisée, searchlang, lang, fuzzy, maxRows) → liste de geonameIds
_record_cache: dict = _load_pkl(config.RECORD_FILE)
_search_cache: dict = _load_pkl(config.SEARCH_FILE)
atexit.register(_save_all)


@lru_cache(maxsize=512)
def fetch_geoname(geoname_id, username):
    """Appelle GeoNames getJSON pour un lieu donné. Résultat mis en cache (lru_cache)."""
    resp = requests.get(
        config.GEONAMES_URL + "getJSON",
        params={"geonameId": geoname_id, "username": username, "style": "FULL"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


# --- Manifeste du service ---

def service_manifest(base_url):
    """Retourne le manifeste du service de réconciliation."""
    return {
        "name": "GeoNames Reconciliation Service",
        "identifierSpace": "http://www.geonames.org/ontology/",
        "schemaSpace": "http://www.geonames.org/ontology/",
        "defaultTypes": [
            {"id": "places", "name": "Place search"},
            {"id": "geonameid", "name": "GeoNames ID"},
        ],
        "preview": {
            "url": base_url + "/reconcile/preview?id={{id}}",
            "width": 300,
            "height": 400,
        },
        "view": {"url": "https://sws.geonames.org/{{id}}/"},
        "extend": {
            "propose_properties": {"service_url": base_url, "service_path": "/reconcile/properties"},
            "property_settings": [],
        },
    }


# --- GUI ---

@app.route("/", methods=["GET"])
def index():
    settings = get_settings()
    return render_template("index.html", settings=settings)


@app.route("/settings", methods=["POST"])
def update_settings():
    settings = load_settings()
    settings["remember"] = "remember" in request.form
    username = request.form.get("username", "").strip()
    if not username:
        return render_template("index.html", settings=settings, error="Le nom d'utilisateur est obligatoire.")
    settings["username"] = username
    settings["searchlang"] = request.form.get("searchlang", "").strip()
    settings["lang"] = request.form.get("lang", "").strip()
    try:
        settings["maxRows"] = int(request.form.get("maxRows", config.DEFAULT_MAX_ROWS))
    except ValueError:
        settings["maxRows"] = config.DEFAULT_MAX_ROWS
    try:
        settings["fuzzy"] = max(0.0, min(1.0, float(request.form.get("fuzzy", config.DEFAULT_FUZZY))))
    except ValueError:
        settings["fuzzy"] = config.DEFAULT_FUZZY
    try:
        settings["threshold"] = max(0, min(100, int(request.form.get("threshold", config.DEFAULT_THRESHOLD))))
    except ValueError:
        settings["threshold"] = config.DEFAULT_THRESHOLD
    save_settings(settings)
    return render_template("index.html", settings=settings, saved=True)


# --- Réconciliation ---

@app.route("/reconcile", methods=["GET", "POST"])
def reconcile():
    global _quota_notified
    queries = request.values.get("queries")
    extend_data = request.values.get("extend")
    base_url = request.host_url.rstrip("/")

    if extend_data:
        result = extend_handler(extend_data)
        _save_all()
        return result

    if not queries:
        return jsonify(service_manifest(base_url))

    settings = get_settings()
    try:
        query_batch = json.loads(queries)
    except json.JSONDecodeError:
        return jsonify({"error": "Invalid queries JSON"}), 400

    results = {}
    quota_error = None

    for qid, qdata in query_batch.items():
        if quota_error:
            results[qid] = {"result": [], "error": quota_error}
            continue

        query_str = qdata.get("query", "")
        qtype = (qdata.get("type") or "")
        by_id = qtype == "geonameid" or query_str.strip().lstrip("-").isdigit()

        try:
            _t = time.perf_counter()
            if by_id:
                geoname_id = query_str.strip()
                cached = _record_cache.get(geoname_id)
                if cached:
                    g = cached
                    print(f"[reconcile:id] {geoname_id!r} — cache hit")
                else:
                    resp = requests.get(
                        config.GEONAMES_URL + "getJSON",
                        params={"geonameId": geoname_id, "username": settings["username"], "style": "FULL"},
                        timeout=10,
                    )
                    resp.raise_for_status()
                    g = resp.json()
                    if "status" in g:
                        msg = g["status"].get("message", "Erreur GeoNames inconnue")
                        if not _quota_notified:
                            push_notification(msg)
                            _quota_notified = True
                        quota_error = msg
                        results[qid] = {"result": [], "error": msg}
                        continue
                    if len(_record_cache) < _RECORD_CACHE_MAX:
                        with _cache_lock:
                            _record_cache[str(g["geonameId"])] = g
                    print(f"[reconcile:id] {geoname_id!r} → {g.get('toponymName', '')} ({time.perf_counter() - _t:.2f}s)")
                results[qid] = {
                    "result": [{
                        "id": str(g["geonameId"]),
                        "name": g.get("toponymName", g.get("name", "")),
                        "score": 100,
                        "match": True,
                        "type": [{"id": "geonameid", "name": "GeoNames ID"}],
                    }]
                }
            else:
                searchlang = settings.get("searchlang", "")
                cache_key = (_normalize(query_str), searchlang, settings.get("lang", ""),
                             settings["fuzzy"], settings["maxRows"])
                if cache_key in _search_cache:
                    geonames = [_record_cache[gid] for gid in _search_cache[cache_key] if gid in _record_cache]
                    print(f"[reconcile] {query_str!r} — cache hit ({len(geonames)} résultats)")
                else:
                    params = {
                        "q": query_str,
                        "maxRows": qdata.get("limit", settings["maxRows"]),
                        "username": settings["username"],
                        "fuzzy": settings["fuzzy"],
                        "style": "FULL",
                        "type": "json",
                    }
                    if searchlang:
                        params["searchlang"] = searchlang
                    if settings.get("lang"):
                        params["lang"] = settings["lang"]

                    resp = requests.get(config.GEONAMES_URL + "searchJSON", params=params, timeout=10)
                    resp.raise_for_status()
                    raw = resp.json()
                    _elapsed = time.perf_counter() - _t
                    if "status" in raw:
                        msg = raw["status"].get("message", "Erreur GeoNames inconnue")
                        print(f"[GeoNames] QUOTA ATTEINT : {msg}")
                        if not _quota_notified:
                            push_notification(msg)
                            _quota_notified = True
                        quota_error = msg
                        results[qid] = {"result": [], "error": msg}
                        continue
                    geonames = raw.get("geonames", [])
                    print(f"[reconcile] {query_str!r} — {len(geonames)} résultats ({_elapsed:.2f}s)")
                    with _cache_lock:
                        if len(_record_cache) < _RECORD_CACHE_MAX:
                            for g in geonames:
                                _record_cache[str(g["geonameId"])] = g
                        if len(_search_cache) < _SEARCH_CACHE_MAX:
                            _search_cache[cache_key] = [str(g["geonameId"]) for g in geonames]

                threshold = settings.get("threshold", config.DEFAULT_THRESHOLD)
                scored = []
                for g in geonames:
                    sc, match = score_candidate(query_str, g, searchlang)
                    if sc < threshold:
                        continue
                    scored.append({
                        "id": str(g["geonameId"]),
                        "name": g.get("name") or g.get("toponymName", ""),
                        "score": sc,
                        "match": match,
                        "type": [{"id": "places", "name": "Place search"}],
                    })
                results[qid] = {"result": scored}
        except requests.RequestException as e:
            results[qid] = {"result": [], "error": str(e)}

    _save_all()
    return jsonify(results)


# --- Preview ---

@app.route("/reconcile/preview", methods=["GET"])
def preview():
    geoname_id = request.args.get("id")
    if not geoname_id:
        return "ID not provided", 400

    # Exception : le bouton "Tester la preview" du GUI passe un paramètre username explicite
    username_param = request.args.get("username", "").strip()
    if geoname_id in _record_cache:
        data = _record_cache[geoname_id]
    elif username_param:
        try:
            data = fetch_geoname(geoname_id, username_param)
        except requests.RequestException as e:
            return f"Erreur réseau : {e}", 500
        if "status" in data:
            msg = data["status"].get("message", "Erreur GeoNames inconnue")
            return f"<p style='font-family:sans-serif;color:#cc0000;padding:16px'><strong>GeoNames :</strong> {msg}</p>", 200
        with _cache_lock:
            _record_cache[geoname_id] = data
    else:
        return "<p style='font-family:sans-serif;color:#888;padding:16px'>Preview non disponible — lancez d'abord une réconciliation.</p>", 200

    return render_template("preview.html", data=data)


# --- Extension de données ---

def extend_handler(extend_data):
    """Traite une requête d'extension de données. Utilise _record_cache en priorité, getJSON en fallback."""
    try:
        extend_req = json.loads(extend_data)
    except json.JSONDecodeError:
        return jsonify({"error": "Invalid extend JSON"}), 400

    ids = extend_req.get("ids", [])
    props = [p["id"] for p in extend_req.get("properties", []) if p["id"] in PROPERTY_IDS]

    settings = get_settings()
    rows = {}

    for geoname_id in ids:
        gid = str(geoname_id)
        if gid in _record_cache:
            data = _record_cache[gid]
        else:
            try:
                data = fetch_geoname(gid, settings["username"])
            except requests.RequestException:
                rows[gid] = {prop: [] for prop in props}
                continue
            with _cache_lock:
                _record_cache[gid] = data
        rows[gid] = {
            prop: [{"str": str(v)}] if (v := data.get(prop)) is not None else []
            for prop in props
        }

    meta = [next(p for p in EXTENSION_PROPERTIES if p["id"] == pid) for pid in props]
    return jsonify({"meta": meta, "rows": rows})


@app.route("/reconcile/properties", methods=["GET", "POST"])
def properties():
    return jsonify({"limit": 100, "type": "places", "properties": EXTENSION_PROPERTIES})


@app.route("/reconcile/extend", methods=["GET", "POST"])
def extend():
    extend_data = request.values.get("extend")
    if not extend_data:
        return jsonify({"error": "No extend parameter"}), 400
    result = extend_handler(extend_data)
    _save_all()
    return result


# --- Carte ---

def _zoom_from_bbox(west, east, south, north):
    """Calcule un niveau de zoom approximatif depuis une bbox."""
    span = max(abs(east - west), abs(north - south))
    if span > 20:   return 4
    if span > 8:    return 5
    if span > 3:    return 6
    if span > 1:    return 7
    if span > 0.3:  return 9
    if span > 0.05: return 11
    return 13


@lru_cache(maxsize=256)
def render_map(lat, lng, zoom=6):
    """Génère une image PNG de carte OSM avec un point rouge sur les coordonnées. Résultat mis en cache (lru_cache)."""
    m = StaticMap(280, 160)
    m.add_marker(CircleMarker((lng, lat), "#cc0000", 8))
    image = m.render(zoom=zoom, center=[lng, lat])
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


@app.route("/reconcile/map")
def map_image():
    try:
        lat = float(request.args.get("lat"))
        lng = float(request.args.get("lng"))
    except (TypeError, ValueError):
        return "Invalid parameters", 400

    try:
        west  = float(request.args.get("west"))
        east  = float(request.args.get("east"))
        south = float(request.args.get("south"))
        north = float(request.args.get("north"))
        zoom  = _zoom_from_bbox(west, east, south, north)
    except (TypeError, ValueError):
        zoom = 6

    try:
        png = render_map(lat, lng, zoom)
    except RuntimeError:
        return "Carte indisponible (tuiles OSM inaccessibles)", 503
    return send_file(io.BytesIO(png), mimetype="image/png")


# --- Test connexion ---

@app.route("/reset-quota", methods=["POST"])
def reset_quota():
    global _quota_notified
    _quota_notified = False
    return jsonify({"ok": True})


@app.route("/test-connection", methods=["GET"])
def test_connection():
    settings = get_settings()
    username = request.args.get("username", "").strip() or settings["username"]
    try:
        resp = requests.get(
            config.GEONAMES_URL + "searchJSON",
            params={"q": "Paris", "maxRows": 1, "username": username},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if "status" in data:
            code = data["status"].get("value", 0)
            if code in (18, 19, 20):
                msg = "Quota journalier dépassé."
            elif code == 10:
                msg = "Nom d'utilisateur invalide ou web services non activés."
            elif code == 13:
                msg = "Serveur GeoNames en maintenance. Réessayez dans quelques instants."
            else:
                msg = data["status"].get("message", "Erreur inconnue")
            return jsonify({"ok": False, "message": msg})
        return jsonify({"ok": True, "message": f"Connexion OK — compte : {username}"})
    except requests.RequestException as e:
        return jsonify({"ok": False, "message": str(e)})


# --- Gestion de session (cache) ---

@app.route("/clear-cache", methods=["POST"])
def clear_cache():
    fetch_geoname.cache_clear()
    render_map.cache_clear()
    with _cache_lock:
        _record_cache.clear()
        _search_cache.clear()
    for path in (config.RECORD_FILE, config.SEARCH_FILE):
        if os.path.exists(path):
            os.remove(path)
    return jsonify({"ok": True, "message": "Cache vidé."})


@app.route("/export-cache/pkl")
def export_cache_pkl():
    """Exporte les deux caches dans une archive zip téléchargeable."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("record_cache.pkl", pickle.dumps(_record_cache))
        zf.writestr("search_cache.pkl", pickle.dumps(_search_cache))
    buf.seek(0)
    return send_file(buf, mimetype="application/zip",
                     as_attachment=True, download_name="geonames_cache.zip")


@app.route("/import-cache", methods=["POST"])
def import_cache():
    """Importe une archive zip de cache et fusionne avec le cache courant."""
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "message": "Aucun fichier reçu."}), 400
    try:
        with zipfile.ZipFile(f) as zf:
            record_data = pickle.loads(zf.read("record_cache.pkl"))
            search_data = pickle.loads(zf.read("search_cache.pkl"))
        if not isinstance(record_data, dict) or not isinstance(search_data, dict):
            return jsonify({"ok": False, "message": "Format invalide."}), 400
        with _cache_lock:
            _record_cache.update(record_data)
            _search_cache.update(search_data)
        _save_all()
        return jsonify({"ok": True, "message": (
            f"{len(record_data)} notices et {len(search_data)} recherches importées. "
            f"Cache total : {len(_record_cache)} notices, {len(_search_cache)} recherches."
        )})
    except Exception as e:
        return jsonify({"ok": False, "message": f"Erreur : {e}"}), 400


@app.route("/notifications")
def notifications():
    q = queue.Queue(maxsize=10)
    with _sse_lock:
        _sse_clients.append(q)

    def stream():
        try:
            while True:
                try:
                    data = q.get(timeout=30)
                    yield f"data: {data}\n\n"
                except queue.Empty:
                    yield "data: {\"ping\": true}\n\n"
        finally:
            with _sse_lock:
                if q in _sse_clients:
                    _sse_clients.remove(q)

    return Response(
        stream_with_context(stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import logging
    import webbrowser

    class _FilterWerkzeug(logging.Filter):
        def filter(self, record):
            msg = record.getMessage()
            return "production" not in msg.lower() and "do not use" not in msg.lower()

    logging.getLogger("werkzeug").addFilter(_FilterWerkzeug())

    banner = r"""

   ______           _   __                         
  / ____/__  ____  / | / /___ _____ ___  ___  _____
 / / __/ _ \/ __ \/  |/ / __ `/ __ `__ \/ _ \/ ___/
/ /_/ /  __/ /_/ / /|  / /_/ / / / / / /  __(__  ) 
\____/\___/\____/_/ |_/\__,_/_/ /_/ /_/\___/____/  
   / __ \___  _________  ____  _____(_) /__        
  / /_/ / _ \/ ___/ __ \/ __ \/ ___/ / / _ \       
 / _, _/  __/ /__/ /_/ / / / / /__/ / /  __/       
/_/ |_|\___/\___/\____/_/ /_/\___/_/_/\___/        
                                                   

"""
    print(banner)
    print(f"v1.2  avril 2026")
    print(f" * Service OpenRefine : http://127.0.0.1:{config.PORT}/reconcile")
    print(f" * Interface config   : http://127.0.0.1:{config.PORT}/")
    print()

    url = f"http://127.0.0.1:{config.PORT}/"
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    app.run(port=config.PORT, debug=False, threaded=True)
