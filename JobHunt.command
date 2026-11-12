#!/bin/bash
# Double-click (macOS) to open the dashboard. Your data lives in ./data (or $JOBHUNT_DATA).
cd "$(dirname "$0")" && exec python3 app.py
