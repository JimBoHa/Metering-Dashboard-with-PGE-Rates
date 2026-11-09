import argparse
import os
from pathlib import Path
import uvicorn

def main():
    parser=argparse.ArgumentParser(description="Independent read-only metering dashboard")
    parser.add_argument("--host",default="127.0.0.1")
    parser.add_argument("--port",type=int,default=8095)
    parser.add_argument("--data-dir",default=os.environ.get("METERING_DATA_DIR",str(Path.home()/".local/share/metering-dashboard")))
    args=parser.parse_args()
    os.environ["METERING_DATA_DIR"]=args.data_dir
    from .web import create_app
    uvicorn.run(create_app(),host=args.host,port=args.port,workers=1,access_log=False)
if __name__=="__main__":main()
