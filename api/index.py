import os
import sys

# Add project root to sys.path to enable imports of backend modules
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from backend.main import app

# Vercel requires the app variable to be exposed
