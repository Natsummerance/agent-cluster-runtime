import json, os, pathlib
payload = json.loads(open(0, encoding='utf-8').read())
out = os.environ.get('HOOK_OUT', '')
pathlib.Path(out).write_text(json.dumps(payload), encoding='utf-8')
