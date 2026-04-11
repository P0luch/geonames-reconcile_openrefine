import json
import os

import requests
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

import config

app = Flask(__name__)
CORS(app)


# --- Settings persistence ---

DEFAULT_SETTINGS = {
    "username": config.DEFAULT_USERNAME,
    "remember": False,
    "lang": "",
    "maxRows": 10,
    "fuzzy": 0.8,
}


def load_settings():
    if os.path.exists(config.SETTINGS_FILE):
        with open(config.SETTINGS_FILE, "r", encoding="utf-8") as f:
            stored = json.load(f)
        settings = DEFAULT_SETTINGS.copy()
        settings.update(stored)
        # If remember is False, reset username to default
        if not settings.get("remember"):
            settings["username"] = config.DEFAULT_USERNAME
        return settings
    return DEFAULT_SETTINGS.copy()


def save_settings(settings):
    with open(config.SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


# --- Service metadata ---

def service_manifest(base_url):
    return {
        "name": "GeoNames Reconciliation Service",
        "identifierSpace": "http://www.geonames.org/ontology/",
        "schemaSpace": "http://www.geonames.org/ontology/",
        "defaultTypes": [{"id": "places", "name": "Place search"}],
        "preview": {
            "url": base_url + "/reconcile/preview?id={{id}}",
            "width": 250,
            "height": 400,
        },
        "view": {"url": "https://sws.geonames.org/{{id}}/"},
    }


# --- GUI ---

@app.route("/", methods=["GET"])
def index():
    settings = load_settings()
    return render_template("index.html", settings=settings)


@app.route("/settings", methods=["POST"])
def update_settings():
    settings = load_settings()
    settings["username"] = request.form.get("username", config.DEFAULT_USERNAME).strip()
    settings["remember"] = "remember" in request.form
    settings["lang"] = request.form.get("lang", "").strip()
    settings["maxRows"] = int(request.form.get("maxRows", 10))
    settings["fuzzy"] = float(request.form.get("fuzzy", 0.8))
    save_settings(settings)
    return render_template("index.html", settings=settings, saved=True)


# --- Reconciliation ---

@app.route("/reconcile", methods=["GET", "POST"])
def reconcile():
    queries = request.values.get("queries")
    base_url = request.host_url.rstrip("/")

    if not queries:
        return jsonify(service_manifest(base_url + "/reconcile".rstrip("/reconcile") if False else base_url))

    settings = load_settings()
    try:
        query_batch = json.loads(queries)
    except json.JSONDecodeError:
        return jsonify({"error": "Invalid queries JSON"}), 400

    results = {}
    for qid, qdata in query_batch.items():
        params = {
            "q": qdata.get("query", ""),
            "maxRows": qdata.get("limit", settings["maxRows"]),
            "username": settings["username"],
            "fuzzy": settings["fuzzy"],
            "type": "json",
        }
        if settings.get("lang"):
            params["lang"] = settings["lang"]

        try:
            resp = requests.get(config.GEONAMES_URL + "searchJSON", params=params, timeout=10)
            resp.raise_for_status()
            geonames = resp.json().get("geonames", [])
            results[qid] = {
                "result": [
                    {
                        "id": str(g["geonameId"]),
                        "name": g.get("toponymName", g.get("name", "")),
                        "score": 0,
                        "match": False,
                        "type": [{"id": "places", "name": "Place search"}],
                    }
                    for g in geonames
                ]
            }
        except Exception as e:
            results[qid] = {"result": [], "error": str(e)}

    return jsonify(results)


# --- Preview ---

@app.route("/reconcile/preview", methods=["GET"])
def preview():
    geoname_id = request.args.get("id")
    if not geoname_id:
        return "ID not provided", 400

    settings = load_settings()
    try:
        resp = requests.get(
            config.GEONAMES_URL + "getJSON",
            params={"geonameId": geoname_id, "username": settings["username"]},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Error fetching data: {e}", 500

    return render_template(
        "preview.html",
        data=data,
        token=config.MAPBOX_ACCESS_TOKEN,
    )


if __name__ == "__main__":
    app.run(port=config.PORT, debug=True)
