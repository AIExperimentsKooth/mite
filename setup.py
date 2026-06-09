"""Minimal setup.py for editable installs.

pyproject.toml has the actual build config; this file exists so
`pip install -e .` works on older pip/setuptools.
"""
from setuptools import setup
setup()
