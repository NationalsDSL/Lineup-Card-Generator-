"""Streamlit Community Cloud entrypoint."""

from pathlib import Path
import runpy


APP_FILE = Path(__file__).with_name("lineup.py")
runpy.run_path(str(APP_FILE), run_name="__main__")
