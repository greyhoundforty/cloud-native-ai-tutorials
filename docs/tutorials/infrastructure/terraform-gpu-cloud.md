---
tags:
  - terraform
  - gpu
  - aws
  - gcp
---

# GPU Workloads on AWS with Terraform: From Zero to LLM Inference

You want a GPU in the cloud to run an LLM. This guide shows you how to provision one
with Terraform, install CUDA automatically via cloud-init, spin up Ollama, and tear
everything down when you're done — so you don't pay for idle hardware.

**What you'll build:**
- An EC2 `g4dn.xlarge` (NVIDIA T4 GPU) running Ubuntu 22.04
- CUDA 12 installed via user-data on first boot
- [Ollama](https://ollama.com) as the inference runtime
- Mistral-7B (or any model you choose) ready to serve requests

**Cost estimate:** ~$0.53/hr on-demand in `us-east-1`. A typical 2–3 hour session
costs under $2.

---

## Prerequisites

| Tool | Tested version |
|------|---------------|
| Terraform | ≥ 1.6 |
| AWS CLI | ≥ 2.15 |
| An AWS account with EC2 and IAM permissions | — |

You'll also need:
- An SSH key pair. Generate one with `ssh-keygen -t ed25519 -C "gpu-tutorial"` if
  you don't have one.
- AWS credentials configured (`aws configure` or environment variables).

**GPU instance quotas:** New AWS accounts have a default quota of 0 vCPUs for G/P
instance families. Check yours with:

```bash
aws service-quotas get-service-quota \
  --service-code ec2 \
  --quota-code L-417A185B  # Running On-Demand G and VT Instances
```

If the quota is 0, [request an increase](https://console.aws.amazon.com/servicequotas/)
for at least 4 vCPUs (enough for `g4dn.xlarge`). Approval usually takes minutes to
a few hours.

---

## Project layout

```
terraform-gpu-tutorial/
├── main.tf                    # Core infrastructure
├── variables.tf               # Input variable declarations
├── outputs.tf                 # SSH command, endpoint, cost
├── user_data.sh.tpl           # Bootstrap script (CUDA + Ollama)
└── terraform.tfvars.example   # Template — copy to terraform.tfvars
```

---

## Step 1 — Configure your variables

```bash
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:

```hcl
region         = "us-east-1"
name_prefix    = "llm-gpu"
instance_type  = "g4dn.xlarge"
root_volume_gb = 60
ssh_public_key = "ssh-ed25519 AAAA..."   # paste your public key
allowed_cidr   = "203.0.113.X/32"        # your IP: curl -s ifconfig.me
ollama_model   = "mistral"               # or llama3, phi3, gemma2, etc.
```

**Choosing an instance type:**

| Type | GPU | VRAM | On-demand (us-east-1) | Good for |
|------|-----|------|-----------------------|---------|
| `g4dn.xlarge` | T4 | 16 GB | ~$0.53/hr | 7B models (Q4), dev/test |
| `g4dn.2xlarge` | T4 | 16 GB | ~$0.75/hr | Same GPU, more CPU/RAM |
| `g5.xlarge` | A10G | 24 GB | ~$1.01/hr | 13B models, faster throughput |
| `g5.2xlarge` | A10G | 24 GB | ~$1.21/hr | 13B+ with headroom |
| `p3.2xlarge` | V100 | 16 GB | ~$3.06/hr | Legacy, widely available |

> **Spot instances:** Add `instance_market_options { market_type = "spot" }` to the
> `aws_instance` resource for up to 70% savings. Not recommended for long training
> runs due to interruption risk, but fine for inference sessions.

---

## Step 2 — Initialize and apply

```bash
terraform init
terraform plan
terraform apply
```

Terraform will create:
- A security group (SSH + Ollama port)
- An SSH key pair
- An IAM role with SSM permissions (for console access fallback)
- An EC2 instance with encrypted EBS root volume
- An Elastic IP

The `apply` finishes in about 30 seconds. The instance then runs its bootstrap
script in the background — this takes **5–10 minutes** (CUDA install + model pull).

---

## Step 3 — Wait for the instance to be ready

The Terraform output gives you a ready-to-run SSH command:

```bash
terraform output ssh_command
# → ssh -i ~/.ssh/id_ed25519 ubuntu@<public-ip>
```

Watch the bootstrap progress:

```bash
ssh -i ~/.ssh/id_ed25519 ubuntu@$(terraform output -raw public_ip) \
  "tail -f /var/log/user-data.log"
```

You'll see CUDA installation, Ollama startup, and the model pull. When you see
`Bootstrap complete. GPU instance is ready.` you're good to go.

Verify the GPU is recognized:

```bash
nvidia-smi
```

Expected output:
```
+-----------------------------------------------------------------------------------------+
| NVIDIA-SMI 550.x     Driver Version: 550.x    CUDA Version: 12.4               |
|-----------------------------------------+------------------------+----------------------+
| GPU  Name                 Persistence-M | Bus-Id          Disp.A | Volatile Uncorr. ECC |
| Fan  Temp   Perf          Pwr:Usage/Cap |           Memory-Usage | GPU-Util  Compute M. |
|=========================================+========================+======================|
|   0  Tesla T4                       Off |   00000000:00:1E.0 Off |                    0 |
| N/A   32C    P8              9W /  70W |       1MiB / 16384MiB |      0%      Default |
+-----------------------------------------------------------------------------------------+
```

---

## Step 4 — Run inference with Ollama

Ollama is now running as a systemd service. Check its status:

```bash
systemctl status ollama
```

### Interactive chat

```bash
ollama run mistral
```

Type your prompt and press Enter. You'll see tokens stream out in real time.
Exit with `/bye`.

### REST API

Ollama exposes a local-compatible API on port 11434. You can hit it from your
laptop since we opened that port in the security group:

```bash
OLLAMA_URL=$(terraform output -raw ollama_endpoint)

curl "$OLLAMA_URL/api/generate" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mistral",
    "prompt": "Explain Kubernetes resource limits in two sentences.",
    "stream": false
  }' | python3 -m json.tool
```

For streaming responses (like a chat interface would use):

```bash
curl "$OLLAMA_URL/api/generate" \
  -H "Content-Type: application/json" \
  -d '{"model": "mistral", "prompt": "What is a GPU?", "stream": true}'
```

### OpenAI-compatible endpoint

Ollama also exposes an OpenAI-compatible `/v1/chat/completions` endpoint, so you
can swap in the base URL for any code that already uses the OpenAI SDK:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://<public-ip>:11434/v1",
    api_key="ollama",  # any non-empty string
)

response = client.chat.completions.create(
    model="mistral",
    messages=[{"role": "user", "content": "What year did Kubernetes reach 1.0?"}],
)
print(response.choices[0].message.content)
```

### Monitoring GPU utilization

While running inference, open a second SSH session and watch utilization:

```bash
watch -n1 nvidia-smi
```

You should see GPU utilization jump to 80–100% during inference and drop back to
near 0% while idle. Memory usage for Mistral-7B (Q4_K_M) is around 4–5 GB of the
16 GB available.

---

## Step 5 — Switching models

Pull additional models at any time:

```bash
# Larger, more capable (needs A10G/g5 for comfortable fit)
ollama pull llama3

# Smaller/faster (fits on T4 with headroom)
ollama pull phi3
ollama pull gemma2:2b

# List what's downloaded
ollama list

# Remove a model to free disk space
ollama rm llama3
```

---

## Cost estimates

Assuming `g4dn.xlarge` in `us-east-1` (on-demand pricing, March 2026):

| Session | Duration | Compute | EBS (60 GB gp3) | Egress* | Total |
|---------|----------|---------|-----------------|---------|-------|
| Quick test | 1 hr | $0.53 | $0.02 | <$0.01 | ~$0.55 |
| Half-day dev | 4 hr | $2.10 | $0.08 | ~$0.05 | ~$2.23 |
| Full day | 8 hr | $4.21 | $0.16 | ~$0.10 | ~$4.47 |

\* Egress estimate assumes ~500 MB of API responses. First 100 GB/month is free within AWS.

**Spot pricing** for `g4dn.xlarge` averages $0.16–0.20/hr (70% savings). Use it for
interactive sessions where interruption is acceptable.

---

## Step 6 — Teardown

**Stop (but preserve) the instance:**

```bash
aws ec2 stop-instances --instance-ids $(terraform output -raw instance_id)
```

Stopped instances don't incur compute charges, but you still pay for EBS storage
(~$0.08/hr for 60 GB). Good for short breaks.

**Destroy everything:**

```bash
terraform destroy
```

This removes all resources including the EBS volume, security group, IAM role, and
Elastic IP. There's nothing left to incur charges.

> **Confirm before destroy:** Terraform will print a plan and ask for confirmation.
> Review it carefully — `terraform destroy` is irreversible.

---

## Appendix: GCP and Azure equivalents

This tutorial used AWS, but the same approach works on other clouds:

### Google Cloud Platform

```hcl
# GCP equivalent — NVIDIA T4 on n1-standard-4
resource "google_compute_instance" "gpu" {
  name         = "llm-gpu"
  machine_type = "n1-standard-4"
  zone         = "us-central1-a"

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2204-lts"
      size  = 60
    }
  }

  guest_accelerator {
    type  = "nvidia-tesla-t4"
    count = 1
  }

  scheduling {
    on_host_maintenance = "TERMINATE"  # Required for GPU instances
    automatic_restart   = false
  }

  network_interface {
    network = "default"
    access_config {}
  }

  metadata_startup_script = file("user_data.sh")
}
```

GCP T4 pricing: ~$0.35/hr for the accelerator + ~$0.19/hr for n1-standard-4 = **~$0.54/hr** total.

### Azure

```hcl
# Azure equivalent — NC4as T4 v3 (1× T4)
resource "azurerm_linux_virtual_machine" "gpu" {
  name                = "llm-gpu"
  resource_group_name = azurerm_resource_group.rg.name
  location            = "eastus"
  size                = "Standard_NC4as_T4_v3"
  admin_username      = "ubuntu"

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Premium_LRS"
    disk_size_gb         = 60
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts-gen2"
    version   = "latest"
  }

  custom_data = base64encode(file("user_data.sh"))
}
```

Azure NC4as T4 v3 pricing: ~$0.53/hr in East US.

### ROCm (AMD GPUs)

If you need AMD GPU support (e.g., AWS `g4ad` with Radeon Pro V520), replace the
CUDA section of `user_data.sh.tpl` with the ROCm installer:

```bash
# ROCm 6.x on Ubuntu 22.04
wget -q https://repo.radeon.com/amdgpu-install/6.1/ubuntu/jammy/amdgpu-install_6.1.60100-1_all.deb
dpkg -i amdgpu-install_6.1.60100-1_all.deb
amdgpu-install --usecase=rocm --no-dkms -y
```

Ollama detects ROCm automatically — no code changes needed.

---

## Troubleshooting

**`nvidia-smi: command not found` after SSH:**
The CUDA installation may still be running. Check: `tail -f /var/log/user-data.log`

**Ollama model pull fails:**
The instance needs outbound HTTPS access. Verify the security group allows egress
on port 443 (the default egress rule allows all traffic — check you didn't remove it).

**`403 Forbidden` on GPU quota:**
You need to request a vCPU quota increase for G instances. See the Prerequisites
section above.

**High inference latency on T4:**
The T4 is an inference-optimized card but has limited FP32 throughput. For faster
responses, use quantized models (Q4_K_M or Q5_K_M format) or upgrade to `g5.xlarge`
with the A10G.
