from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
TUSHARE_TOKEN_FILE = PROJECT_ROOT / '.tushare_token'