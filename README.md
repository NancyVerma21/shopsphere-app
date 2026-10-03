# ShopSphere — Final Buggy AUT

This folder is the single final intentionally buggy Application Under Test for the ShopSphere SDET portfolio. It contains the complete Flask application, templates, CSS, product images, and the 19-defect inventory.

The listed defects are intentional and should not be fixed during the defect-detection phase. The objective is to discover, reproduce, automate, investigate, and document them.

## Local setup

1. Use Python 3.11.
2. Create a virtual environment.
3. Install `requirements.txt`.
4. Copy your existing local `.env` into this folder (or create it from `.env.example`).
5. Start with `python app.py`.

The `.env` file and `.venv` are intentionally not included because they contain local credentials and machine-specific configuration.
