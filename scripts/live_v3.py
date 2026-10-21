"""Run from the application directory. See docs/live_v3_demo.md."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from rca_live_v3.cli import main
if __name__=='__main__':
    raise SystemExit(main())
