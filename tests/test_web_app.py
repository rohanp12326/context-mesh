from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_PATH = PROJECT_ROOT / "apps" / "web" / "app.py"


def test_streamlit_app_loads_without_exceptions():
    at = AppTest.from_file(str(APP_PATH), default_timeout=15)
    at.run()
    assert not at.exception
    assert len(at.tabs) >= 5
    assert len(at.text_input) > 0

