@echo off
setlocal
cd /d "%~dp0"
if "%TARGETCOMPASS_MCP_PORT%"=="" set TARGETCOMPASS_MCP_PORT=8790
echo Starting TargetCompass MCP HTTP server on http://127.0.0.1:%TARGETCOMPASS_MCP_PORT%/
echo Use tc_lite.py mcp-token to create a project-bound token before connecting external clients.
python tc_lite.py mcp-http-server --host 127.0.0.1 --port %TARGETCOMPASS_MCP_PORT%
pause
