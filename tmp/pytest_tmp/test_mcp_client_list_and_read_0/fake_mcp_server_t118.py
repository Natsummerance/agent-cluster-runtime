
import sys, json, base64

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}, "resources": {}},
            "serverInfo": {"name": "fake", "version": "1.0"},
        }})
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [
            {"name": "echo", "description": "echo text",
             "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
        ]}})
    elif method == "tools/call":
        args = (msg.get("params") or {}).get("arguments", {})
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "content": [{"type": "text", "text": "ECHO:" + str(args.get("text", ""))}], "isError": False}})
    elif method == "resources/list":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"resources": [
            {"uri": "note://demo/1", "name": "note1", "description": "demo note", "mimeType": "text/plain"},
        ]}})
    elif method == "resources/read":
        uri = (msg.get("params") or {}).get("uri")
        if uri == "note://demo/1":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {"contents": [
                {"uri": uri, "mimeType": "text/plain", "text": "hello resource"}]}})
        elif uri == "blob://demo/1":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {"contents": [
                {"uri": uri, "mimeType": "application/octet-stream",
                 "blob": base64.b64encode(b"blob-bytes").decode()}]}})
        else:
            send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32002, "message": "resource not found"}})
