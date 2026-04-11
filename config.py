import os

PORT = int(os.environ.get("PORT", 5065))
GEONAMES_URL = os.environ.get("GEONAMES_URL", "http://api.geonames.org/")
MAPBOX_ACCESS_TOKEN = os.environ.get("MAPBOX_ACCESS_TOKEN")
SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")
DEFAULT_USERNAME = "demo"
