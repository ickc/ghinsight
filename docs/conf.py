"""Sphinx configuration for the ghinsight documentation site."""

from __future__ import annotations

from datetime import date

project = "ghinsight"
author = "Kolen Cheung"
copyright = f"{date.today().year}, {author}"

extensions = [
    "myst_nb",                 # MyST markdown + executable notebooks
    "autoapi.extension",       # automatic API docs from source
    "sphinx.ext.napoleon",     # Google/NumPy-style docstrings
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
]

# -- MyST / MyST-NB ---------------------------------------------------------
myst_enable_extensions = ["colon_fence", "deflist"]

# Treat jupytext "percent" .py scripts as executable notebooks, so the Python
# tutorial lives as a reviewable script but renders with outputs.
nb_custom_formats = {".py": ["jupytext.reads", {"fmt": "py:percent"}]}
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "myst-nb",
    ".py": "myst-nb",
}
nb_execution_mode = "auto"
nb_execution_timeout = 180
nb_execution_raise_on_error = True

# -- AutoAPI ----------------------------------------------------------------
autoapi_type = "python"
autoapi_dirs = ["../src/ghinsight"]
autoapi_options = [
    "members",
    "undoc-members",
    "show-inheritance",
    "show-module-summary",
    "imported-members",
]
autoapi_member_order = "groupwise"
autoapi_python_class_content = "both"
autoapi_keep_files = False

# -- intersphinx ------------------------------------------------------------
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "pandas": ("https://pandas.pydata.org/docs/", None),
}

# -- HTML -------------------------------------------------------------------
html_theme = "furo"
html_title = "ghinsight"
# ``.py`` is registered as a notebook source above, so exclude this config file.
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", "conf.py"]
