import os, pathlib
ws = pathlib.Path(os.environ['AGENT_CLUSTER_WORKSPACE'])
ev = os.environ['AGENT_CLUSTER_EVENT']
(ws / ('hook-' + ev + '.txt')).write_text(ev)
