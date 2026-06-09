#!/usr/bin/env bash
# wsl_bootstrap.sh -- one-shot Ubuntu (WSL2) setup for reachy-twin.
#
# Invoked over WSL from Windows like:
#   wsl -d Ubuntu-24.04 -- bash -lc \
#     "curl -fsSL https://raw.githubusercontent.com/producer456/reachy-twin/main/wsl_bootstrap.sh | bash"
#
# Idempotent: re-running is safe.
set -e
echo "== reachy-twin WSL2 bootstrap =="

# 1. apt deps -- GStreamer is the whole point of being on Linux
sudo DEBIAN_FRONTEND=noninteractive apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git curl build-essential pkg-config libssl-dev \
    gstreamer1.0-tools libgstreamer1.0-dev \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav python3-gi python3-gst-1.0

# 2. clone the repo if not present
cd "$HOME"
if [ ! -d reachy-twin ]; then
    git clone https://github.com/producer456/reachy-twin
fi
cd reachy-twin

# 3. run the Linux setup_host.sh
bash setup_host.sh

# 4. .env -- MARCUS_URL points back at Windows-host Marcus over the WSL2 NAT gateway
#    (`ip route` first hop = the Windows host's address inside the WSL VM)
HOST_IP=$(ip route | awk '/^default/ { print $3; exit }')
if [ -z "$HOST_IP" ]; then HOST_IP="$(hostname -I | awk '{print $1}')"; fi
if grep -q '^MARCUS_URL=$' .env 2>/dev/null; then
    sed -i "s|^MARCUS_URL=$|MARCUS_URL=http://${HOST_IP}:7860|" .env
fi
echo "MARCUS_URL set to http://${HOST_IP}:7860 (Windows host as seen from WSL2)"

# 5. dump a launcher script
cat > "$HOME/start_reachy.sh" <<'EOF'
#!/usr/bin/env bash
cd "$HOME/reachy-twin"
mkdir -p logs
nohup ./.venv/bin/reachy-mini-daemon                  > logs/daemon.log 2>&1 &
echo "daemon PID $!"
sleep 6                                              # wait for daemon to bind :8000
REACHY_PANEL_HOST=0.0.0.0 nohup ./.venv/bin/python -m twin.panel > logs/panel.log 2>&1 &
echo "panel  PID $!"
EOF
chmod +x "$HOME/start_reachy.sh"

echo
echo "============================================================"
echo "WSL2 bootstrap done."
echo "Next (still inside WSL2):"
echo "  ~/start_reachy.sh                  # launches daemon + panel"
echo "Then from Windows admin PowerShell:"
echo "  WSL2 IP -> netsh interface portproxy add v4tov4 listenport=8500 \\"
echo "                listenaddress=0.0.0.0 connectport=8500 connectaddress=\$(wsl hostname -I)"
echo "============================================================"
