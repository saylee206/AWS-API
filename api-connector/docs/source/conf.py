# Configuration file for the Sphinx documentation builder.

import os
import sys
sys.path.insert(0, os.path.abspath('../..'))

# Project information
project = 'My FastAPI Project'
copyright = '2024, Saylee Mangalmurti'
author = 'Saylee Mangalmurti'
release = '1.0.0'

# Extensions configuration
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.viewcode',
    'sphinx_autodoc_typehints'
]

# Template and source paths
templates_path = ['_templates']
exclude_patterns = []

# HTML output settings
html_theme = 'alabaster'
html_static_path = ['_static']
