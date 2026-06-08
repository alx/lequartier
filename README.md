# LeQuartier

LeQuartier is a comprehensive toolset for property curation, analysis, and mapping, focusing on platforms like Airbnb and Zillow. It combines a web interface for data visualization and management, browser extensions for data extraction, and backend scripts for scraping and automation.

## Features

*   **Web Interface:** A Flask-based web application to manage, visualize, and curate property data, utilizing HTMX for dynamic interactions.
*   **Browser Integrations:** Userscripts and browser extensions for Airbnb and Zillow to assist in data collection.
*   **Data Processing:** Python-based tools and scripts for scraping, data enrichment, and mapping.

## Prerequisites

*   **Python 3.11+**
*   **Node.js** (required for running scripts and managing extension assets)
*   `uv` or `pip` for Python package management

## Setup

### Python Environment
1.  Navigate to the project root.
2.  Install dependencies:
    ```bash
    # Using uv (recommended)
    uv sync
    # Or using pip
    pip install -r pyproject.toml
    ```

### Node Environment
1.  Navigate to the `extensions/` directory.
2.  Install dependencies:
    ```bash
    npm install
    ```

### Configuration
1.  Copy `.env.example` to `.env`.
2.  Populate the required API keys and environment variables in `.env`.

## Running the Project

To start the Flask development server:
```bash
python -m src.web.app
```
*(The application will be available at `http://127.0.0.1:5010`)*

## Directory Structure

*   `src/`: Core Python Flask web application.
*   `extensions/`: Browser extensions (Chrome/Firefox) and userscripts for property platforms.
*   `scripts/`: Utility scripts for data conversion and image generation (Open Graph images, previews).
*   `cache/`: Local data caching.
*   `logs/`: Application logs.
