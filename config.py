import os
import sys

PORT = int(os.environ.get("PORT", 5065))
GEONAMES_URL = os.environ.get("GEONAMES_URL", "http://api.geonames.org/")
DEFAULT_USERNAME = "demo"

DEFAULT_SEARCHLANG = "fr"
DEFAULT_LANG       = "fr"
DEFAULT_MAX_ROWS   = 8
DEFAULT_FUZZY      = 0.8
DEFAULT_THRESHOLD  = 40

# ---------------------------------------------------------------------------
# Chemins runtime
# ---------------------------------------------------------------------------

if getattr(sys, "frozen", False):
    # Mode exécutable PyInstaller
    _exe_dir = os.path.dirname(sys.executable)
    TEMPLATE_DIR = os.path.join(sys._MEIPASS, "templates")
    DATA_DIR = os.path.join(_exe_dir, "geonames-openrefine")
else:
    # Mode script
    _base = os.path.dirname(os.path.abspath(__file__))
    TEMPLATE_DIR = os.path.join(_base, "templates")
    DATA_DIR = _base

os.makedirs(DATA_DIR, exist_ok=True)

SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
SEARCH_FILE   = os.path.join(DATA_DIR, "search_cache.pkl")
RECORD_FILE   = os.path.join(DATA_DIR, "record_cache.pkl")
