import os, pathlib
pathlib.Path(os.environ['HOOK_OUT']).write_text(os.environ.get('AGENT_CLUSTER_EVENT', 'none') + ':' + os.environ.get('AGENT_CLUSTER_PLUGIN', ''), encoding='utf-8')
