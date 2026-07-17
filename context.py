from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
CACHE_DIR = PROJECT_ROOT / "data_cache"
TUSHARE_TOKEN_FILE = PROJECT_ROOT / '.tushare_token'
