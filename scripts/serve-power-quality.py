#!/usr/bin/env python3
"""Start only an explicitly configured local domain instance. No model calls."""
import os
import sys
from pathlib import Path
import argparse
parser=argparse.ArgumentParser()
parser.add_argument('--port',type=int,required=True)
args=parser.parse_args()
if not os.environ.get('DATABASE_URL') or not os.environ.get('DATA_DIR') or os.environ.get('WORKER_IN_API','').lower()!='false':
    raise SystemExit('Explicit DATABASE_URL, DATA_DIR and WORKER_IN_API=false required')
os.environ['POWER_QUALITY_LOCAL_MODE']='true'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.power_quality.local_access import credential
credential()  # Fail before listening if local secret storage is unsafe.
from app.main import app
import uvicorn
uvicorn.run(app,host='127.0.0.1',port=args.port,proxy_headers=False,log_level='info')
