"""Location of the shared assets in a source checkout or PyInstaller bundle."""
from pathlib import Path

RESOURCE_ROOT = Path(__file__).resolve().parents[1]
