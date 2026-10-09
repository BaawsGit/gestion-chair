#!/bin/bash
pip install -r requirements.txt --break-system-packages
python3 manage.py collectstatic --no-input
