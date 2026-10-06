"""DiviTrack giriş noktası:  streamlit run app.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from divitrack.ui.main import run  # noqa: E402

run()
