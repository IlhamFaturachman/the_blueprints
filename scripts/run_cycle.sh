#!/bin/bash
cd /opt/the_blueprints
source venv/bin/activate
./run_paper_5usd.sh --paper --aggressive >> logs/cron.log 2>&1
