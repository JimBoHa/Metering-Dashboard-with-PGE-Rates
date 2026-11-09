#!/usr/bin/env python3
"""Refresh the configured PG&E tariff; writes only to the private data directory."""
import argparse
import asyncio
import os
from pathlib import Path
from metering.store import Store
from metering.rates import fetch_tariff

parser=argparse.ArgumentParser()
parser.add_argument("--data-dir",default=os.environ.get("METERING_DATA_DIR",str(Path.home()/".local/share/metering-dashboard")))
args=parser.parse_args();store=Store(args.data_dir)
try:
    result=asyncio.run(fetch_tariff(store,store.config().rate))
    print(f"Imported {result['selection']['schedule']} effective {result['effective_from']}; source retained privately")
finally:store.close()
