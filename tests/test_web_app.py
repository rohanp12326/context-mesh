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


def test_streamlit_chat_react_execution():
    at = AppTest.from_file(str(APP_PATH), default_timeout=300)
    at.run()
    assert not at.exception

    # Submit a query via the composer (text input + send button)
    at.text_input(key="composer_text").set_value("What are my most urgent tasks")
    submit = next(b for b in at.button if b.label == "↑")
    submit.click().run()
    assert not at.exception

    # Verify message recorded in session state
    messages = at.session_state["messages"]
    assert len(messages) >= 2
    assistant_msg = messages[-1]
    assert assistant_msg["role"] == "assistant"
    assert "react_steps" in assistant_msg
    assert len(assistant_msg["react_steps"]) > 0

    # The queued query must be drained once the answer is rendered
    assert at.session_state["pending_query"] is None


