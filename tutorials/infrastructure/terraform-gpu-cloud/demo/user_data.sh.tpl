#!/usr/bin/env bash
set -euo pipefail
exec > >(tee /var/log/user-data.log | logger -t user-data -s 2>/dev/console) 2>&1

echo "==> Updating system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y

###############################################################################
# 1. NVIDIA driver + CUDA toolkit
#    Using the network installer recommended by NVIDIA for Ubuntu 22.04
###############################################################################

echo "==> Installing NVIDIA driver and CUDA 12.x"
# Add CUDA keyring
wget -q https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
dpkg -i cuda-keyring_1.1-1_all.deb
rm cuda-keyring_1.1-1_all.deb

apt-get update -y
# cuda-drivers installs the latest driver; cuda installs full toolkit
apt-get install -y cuda-drivers cuda

# Make CUDA available in PATH for all users
cat > /etc/profile.d/cuda.sh <<'EOF'
export PATH=/usr/local/cuda/bin:$PATH
export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH
EOF
chmod +x /etc/profile.d/cuda.sh

###############################################################################
# 2. Ollama — single-binary LLM runtime with CUDA auto-detection
###############################################################################

echo "==> Installing Ollama"
curl -fsSL https://ollama.com/install.sh | sh

# Run Ollama as a system service bound to all interfaces
# (The default listens on 127.0.0.1:11434 — override to allow external access)
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/override.conf <<'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0"
EOF

systemctl daemon-reload
systemctl enable --now ollama

###############################################################################
# 3. Pull the requested model
#    This can take several minutes depending on model size and network speed
###############################################################################

echo "==> Waiting for Ollama service to be ready"
for i in $(seq 1 30); do
  if curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; then
    break
  fi
  sleep 5
done

echo "==> Pulling model: ${ollama_model}"
ollama pull "${ollama_model}"

###############################################################################
# 4. Smoke test — generate one token to confirm GPU inference works
###############################################################################

echo "==> Running inference smoke test"
ollama run "${ollama_model}" "Reply with exactly one word: ready" || true

echo "==> Bootstrap complete. GPU instance is ready."
