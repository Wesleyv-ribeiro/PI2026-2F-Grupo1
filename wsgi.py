import os
import sys

ROOT = os.path.dirname(__file__)
APP_DIR = os.path.join(ROOT, "CONCOOP_final")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from app import app
