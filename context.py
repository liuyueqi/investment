from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
CACHE_DIR = PROJECT_ROOT / "data_cache"
# CACHE_DIR = CACHE_DIR / date.today().strftime("%Y%m%d")
