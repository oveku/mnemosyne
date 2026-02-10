"""Test configuration and shared fixtures for Mnemosyne tests."""

import os
import sys
import pytest

# Add server app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
