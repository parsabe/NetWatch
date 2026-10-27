"""Test-wide settings: never send Telegram messages, never read the autotune overlay."""
import os

os.environ["HORIZON_NO_NOTIFY"] = "1"
os.environ["HORIZON_NO_AUTOTUNED"] = "1"
