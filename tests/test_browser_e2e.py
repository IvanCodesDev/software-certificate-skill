from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "capture_web_screenshots.py"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(os.environ.get("RUN_BROWSER_E2E") == "1", "set RUN_BROWSER_E2E=1 with Playwright Chromium")
class BrowserCaptureEndToEndTests(unittest.TestCase):
    def test_launch_login_operate_capture_and_write_back(self):
        with tempfile.TemporaryDirectory(prefix="softcert-browser-") as temp:
            root = Path(temp)
            port = free_port()
            server = root / "fixture_server.py"
            server.write_text(r'''import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HTML = b"""<!doctype html><html><head><meta charset='utf-8'><style>
body{margin:0;font-family:Arial;background:linear-gradient(135deg,#eee,#bbb);color:#222}
header{height:72px;background:#222;color:white;display:flex;align-items:center;padding:0 36px}
main{padding:36px}.panel{background:white;border:1px solid #555;padding:28px;min-height:520px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:18px}.card{height:90px;background:#ddd;border-left:8px solid #555;padding:14px}
input,button{font-size:18px;padding:10px;margin:8px}#dashboard{display:none}
</style></head><body><header>Evidence Dashboard</header><main><section id='login' class='panel'>
<h1>Test Login</h1><label>User <input id='username'></label><label>Password <input id='password' type='password'></label>
<button id='login-button'>Sign in</button></section><section id='dashboard' class='panel'><h1>Dashboard Ready</h1>
<button id='run-button'>Run analysis</button><p id='result'>Waiting</p><div class='grid'>
<div class='card'>Input</div><div class='card'>Rules</div><div class='card'>Progress</div><div class='card'>Output</div>
<div class='card'>Trace</div><div class='card'>Checks</div><div class='card'>Status</div><div class='card'>Archive</div></div></section></main>
<script>document.querySelector('#login-button').onclick=()=>{document.querySelector('#login').style.display='none';document.querySelector('#dashboard').style.display='block'};
document.querySelector('#run-button').onclick=()=>document.querySelector('#result').textContent='Analysis completed';</script></body></html>"""
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.end_headers(); self.wfile.write(HTML)
    def log_message(self, *_): pass
ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), Handler).serve_forever()
''', encoding="utf-8")
            plan = root / "screenshot-plan.json"
            output = root / "screenshots"
            graph = root / "evidence.json"
            graph_out = root / "evidence.with-shots.json"
            graph.write_text(json.dumps({"nodes": [{"id": "CAP-core", "type": "capability"}], "edges": [], "summary": {}}), encoding="utf-8")
            command = f'"{sys.executable}" "{server}" {port}'
            plan.write_text(json.dumps({
                "schema_version": "1.0", "base_url": f"http://127.0.0.1:{port}",
                "server": {"command": command, "cwd": str(root), "health_url": f"http://127.0.0.1:{port}/login", "startup_timeout_seconds": 30},
                "browser": {"engine": "chromium", "headless": True, "viewport": {"width": 1280, "height": 800}},
                "captures": [{"id": "dashboard-result", "title": "分析结果", "route": "/login", "role": "测试管理员",
                    "evidence_ids": ["CAP-core"], "ready_selector": "#login",
                    "actions": [{"type": "fill", "selector": "#username", "value": "tester"},
                                {"type": "fill", "selector": "#password", "value": "secret"},
                                {"type": "click", "selector": "#login-button"},
                                {"type": "click", "selector": "#run-button"}],
                    "assertions": [{"type": "assert_visible", "selector": "#dashboard"},
                                   {"type": "assert_text", "selector": "#result", "contains": "Analysis completed"}]}]
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(SCRIPT), "--plan", str(plan), "--output", str(output),
                "--evidence-source", str(graph), "--evidence-output", str(graph_out),
            ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            index = json.loads((output / "screenshot-index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["state"], "captured")
            self.assertEqual(index["summary"], {"requested": 1, "completed": 1, "passed": 1,
                                                   "quality_warnings": 0, "errors": 0, "missing_planned": 0})
            capture = index["captures"][0]
            self.assertEqual(capture["status"], "pass")
            self.assertTrue(Path(capture["path"]).is_file())
            updated_graph = json.loads(graph_out.read_text(encoding="utf-8"))
            self.assertTrue(any(node.get("id") == "SHOT-dashboard-result" for node in updated_graph["nodes"]))


if __name__ == "__main__":
    unittest.main()
