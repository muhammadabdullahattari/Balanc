"""Run only inside a disposable root container; command doubles test deploy control flow."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

MOCK = r"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
name=Path(sys.argv[0]).name; args=sys.argv[1:]
p=Path('/tmp/deploy-state.json'); state=json.loads(p.read_text())
state['calls'].append([name,*args]); result=0
if name=='aws': print('synthetic-test-password')
elif name=='seq': print(*range(1,4),sep=chr(10))
elif name=='docker':
 if args[0]=='login': sys.stdin.read()
 elif args[0]=='container': result=0 if args[-1] in state['containers'] else 1
 elif args[0]=='run':
  if state['scenario']=='run_failure': result=1
  else: state['containers'][args[args.index('--name')+1]]=args[-1]
 elif args[0]=='exec':
  state['probes']=state.get('probes',0)+int(args[1]=='ledgerpilot-api-candidate')
  bad=state['scenario']=='bad_readiness'
  bad=bad or (state['scenario']=='post_handoff_failure' and state['probes']>1)
  result=int(args[1]=='ledgerpilot-api-candidate' and bad)
 elif args[0]=='rename':
  if state['scenario']=='rename_failure' and args[1]=='ledgerpilot-api-candidate': result=1
  else: state['containers'][args[2]]=state['containers'].pop(args[1])
 elif args[0]=='rm': state['containers'].pop(args[-1],None)
elif name=='nginx' and args==['-s','reload']:
 if state['scenario']=='reload_failure' and not state.get('failed_reload'):
  state['failed_reload']=True; result=1
p.write_text(json.dumps(state)); sys.exit(result)
"""


class Deployment(unittest.TestCase):
    def test_success_and_failures(self):
        self.assertEqual(os.getuid(), 0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ["aws", "docker", "nginx", "sleep", "seq"]:
                tool = root / name
                tool.write_text(MOCK)
                tool.chmod(0o755)
            Path("/etc/ledgerpilot").mkdir(exist_ok=True)
            env = Path("/etc/ledgerpilot/api.env")
            env.write_text("SYNTHETIC=1\n")
            env.chmod(0o600)
            Path("/etc/nginx").mkdir(exist_ok=True)
            upstream = Path("/etc/nginx/ledgerpilot-upstream.conf")
            original = "upstream ledgerpilot_api { server 127.0.0.1:8000; }\n"
            for scenario in [
                "success",
                "run_failure",
                "bad_readiness",
                "reload_failure",
                "post_handoff_failure",
                "rename_failure",
            ]:
                with self.subTest(scenario=scenario):
                    state = Path("/tmp/deploy-state.json")
                    state.write_text(
                        json.dumps(
                            {
                                "scenario": scenario,
                                "containers": {"ledgerpilot-api": "old-image"},
                                "calls": [],
                            }
                        )
                    )
                    upstream.write_text(original)
                    result = subprocess.run(
                        [
                            "bash",
                            "/source/deploy/aws/deploy-api.sh",
                            "test-registry/new-image",
                            "test-region",
                        ],
                        env={**os.environ, "PATH": str(root) + ":" + os.environ["PATH"]},
                        capture_output=True,
                        text=True,
                        timeout=180,
                    )
                    final = json.loads(state.read_text())
                    self.assertEqual(result.returncode == 0, scenario == "success", result.stderr)
                    self.assertEqual(
                        final["containers"],
                        {
                            "ledgerpilot-api": "test-registry/new-image"
                            if scenario == "success"
                            else "old-image"
                        },
                    )
                    self.assertIn(
                        ":8001" if scenario == "success" else ":8000", upstream.read_text()
                    )
                    calls = final["calls"]
                    runs = [c for c in calls if c[:2] == ["docker", "run"]]
                    self.assertIn(
                        "type=bind,source=/var/lib/ledgerpilot/evidence,target=/var/lib/ledgerpilot/evidence",
                        runs[0],
                    )
                    self.assertEqual(Path("/var/lib/ledgerpilot/evidence").stat().st_uid, 10001)
                    if scenario != "success":
                        self.assertFalse(any(c[:2] == ["docker", "stop"] for c in calls))
                        self.assertTrue(
                            any(c[:3] == ["docker", "exec", "ledgerpilot-api"] for c in calls)
                        )
                    else:
                        ready = next(
                            i
                            for i, c in enumerate(calls)
                            if c[:3] == ["docker", "exec", "ledgerpilot-api-candidate"]
                        )
                        stopped = next(
                            i for i, c in enumerate(calls) if c[:2] == ["docker", "stop"]
                        )
                        self.assertLess(ready, stopped)


if __name__ == "__main__":
    unittest.main()
