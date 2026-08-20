import os, pathlib
ws = os.environ.get('AGENT_CLUSTER_WORKSPACE', '')
pathlib.Path(ws, 'hook-' + os.environ.get('AGENT_CLUSTER_EVENT', '') + '.txt').write_text(os.environ.get('AGENT_CLUSTER_PLUGIN', ''), encoding='utf-8')
