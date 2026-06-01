#!/bin/bash
# FSF post-update health check
echo "=========================================="
echo "  FSF Health Check — $(date '+%Y-%m-%d %I:%M:%S %p %Z')"
echo "=========================================="
echo ""
echo "=== Docker Version ==="
docker version --format 'Client: {{.Client.Version}}  Server: {{.Server.Version}}' 2>&1
echo ""
echo "=== Ollama Version ==="
ollama --version 2>&1
echo ""
echo "=== Docker Containers ==="
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>&1
echo ""
echo "=== launchd FSF Services ==="
launchctl list 2>/dev/null | grep dev.forest
echo ""
echo "=== Daemon Health (localhost:7423) ==="
curl -s --max-time 5 http://127.0.0.1:7423/healthz 2>&1 | python3 -m json.tool 2>/dev/null || curl -s --max-time 5 http://127.0.0.1:7423/healthz 2>&1
echo ""
echo "=== Ollama Models (localhost:11434) ==="
curl -s --max-time 5 http://127.0.0.1:11434/api/tags 2>&1 | python3 -c "import sys,json; data=json.load(sys.stdin); [print(f\"  {m['name']} ({m['size']/(1024**3):.1f}GB)\") for m in data.get('models',[])]" 2>/dev/null || curl -s --max-time 5 http://127.0.0.1:11434/api/tags 2>&1 | head -5
echo ""
echo "=========================================="
echo "  Health check complete"
echo "=========================================="
echo ""
echo "Press any key to close..."
read -n 1
