"""Lets the tests import the shared, Linux and Windows code from their folders."""
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(DESKTOP), str(DESKTOP / 'linux'), str(DESKTOP / 'windows')]
