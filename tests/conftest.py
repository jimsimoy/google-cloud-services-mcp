import sys
from pathlib import Path

# google_cloud_services.py and server.py sit at the repo root, not under src/ —
# add it to sys.path so `import google_cloud_services` works without installing
# the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
