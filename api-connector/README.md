# Project Documentation

## Setup Sphinx Documentation

### Prerequisites
- Python 3.8+
- pip

### Installation
1. Install required packages:
```bash
pip install sphinx sphinx-autodoc-typehints fastapi
```

2. Generate Documentation:
```bash
# Navigate to the docs directory
cd docs

# Generate API documentation
sphinx-apidoc -o source/ ..

# Build HTML documentation
make html
```

3. View Documentation:
Open `docs/build/html/index.html` in your web browser.

### Customization
- Modify `docs/source/conf.py` to adjust documentation settings
- Update `docs/source/index.rst` to add more content or change structure