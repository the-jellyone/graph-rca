"""Report verified V3 inference policy availability without injecting a fault."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rca_live_v3.assets import Assets, POLICIES


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--bundle",required=True)
    args=parser.parse_args()
    labels={"dqn":"DQN","dp":"Dynamic programming","voi":"Value of information","expected-sarsa":"Expected SARSA","two-step-sarsa":"Two-step SARSA","expected-sarsa-hybrid":"Expected SARSA + planner","two-step-sarsa-hybrid":"Two-step SARSA + planner","random-valid":"Random valid baseline","expanded-rule":"Expanded rule baseline"}
    try:
        assets=Assets(args.bundle)
    except Exception as exc:
        print(json.dumps({"available":False,"error":f"{type(exc).__name__}: {exc}","policies":[]}))
        return 2
    rows=[]
    for name in POLICIES:
        try:
            assets.policy(name)
            rows.append({"id":name,"label":labels[name],"available":True,"reason":None})
        except Exception as exc:
            rows.append({"id":name,"label":labels[name],"available":False,"reason":f"{type(exc).__name__}: {exc}"[:240]})
    print(json.dumps({"available":True,"policies":rows}))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
