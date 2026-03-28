---
tags:
  - ansible
  - proxmox
  - gpu
  - ollama
---

# Ansible + Proxmox for AI Lab Infrastructure

*Build a GPU-accelerated VM fleet for local AI workloads using Ansible and Proxmox VE.*

---

## Overview

Running large language models locally — via [Ollama](https://ollama.com) or similar runtimes — demands reproducible infrastructure. This tutorial walks you through using Ansible to automate Proxmox VE VM provisioning, GPU pass-through, NVIDIA driver installation, and Ollama deployment. By the end you will have a repeatable playbook that transforms a bare Proxmox host into a ready-to-use AI inference node.

**What you'll build:**

- Ansible inventory for one or more Proxmox hosts
- VM provisioning playbook (cloud-init, resource sizing)
- IOMMU/VFIO GPU pass-through configuration
- NVIDIA driver + CUDA install playbook
- Ollama install + smoke-test validation

**Prerequisites:**

| Requirement | Notes |
|---|---|
| Proxmox VE 8.x | Fresh install or existing cluster |
| NVIDIA GPU (optional but recommended) | GTX 1080 or newer; AMD ROCm path noted where it differs |
| Ansible 2.15+ | `pip install ansible` or system package |
| `community.general` collection | `ansible-galaxy collection install community.general` |
| SSH access to Proxmox host as root | Or a user with `sudo` and API token |
| Proxmox API token | Created in the UI under Datacenter → Permissions |

> **Note on GPU pass-through:** GPU pass-through requires VT-d (Intel) or AMD-Vi on the host CPU and must be enabled in BIOS before proceeding. If you don't have a discrete GPU, skip the GPU sections — Ollama works on CPU with smaller models (e.g., `phi3`, `gemma:2b`).

---

## 1. Inventory Setup

Ansible needs to know where your Proxmox hosts are and how to reach them.

### 1.1 Directory Layout

```
ai-lab/
├── inventory/
│   ├── hosts.yml
│   └── group_vars/
│       ├── all.yml
│       └── proxmox.yml
├── playbooks/
│   ├── provision_vm.yml
│   ├── gpu_passthrough.yml
│   ├── nvidia_drivers.yml
│   └── ollama.yml
├── roles/
│   └── ollama/
│       ├── tasks/main.yml
│       └── defaults/main.yml
└── ansible.cfg
```

### 1.2 `ansible.cfg`

```ini
[defaults]
inventory          = inventory/hosts.yml
remote_user        = root
private_key_file   = ~/.ssh/id_ed25519
host_key_checking  = False
retry_files_enabled = False
stdout_callback    = yaml
```

### 1.3 `inventory/hosts.yml`

```yaml
all:
  children:
    proxmox:
      hosts:
        pve01:
          ansible_host: 192.168.1.10
          proxmox_api_host: 192.168.1.10
          proxmox_node: pve01
    ai_vms:
      hosts:
        ollama01:
          ansible_host: 192.168.1.100   # assigned via DHCP or fixed lease
          ansible_user: ubuntu
```

### 1.4 `inventory/group_vars/all.yml`

```yaml
# Proxmox API credentials (use Ansible Vault in production)
proxmox_api_user: "ansible@pve"
proxmox_api_token_id: "ansible-token"
proxmox_api_token_secret: "{{ vault_proxmox_token_secret }}"
proxmox_storage: "local-lvm"

# VM defaults
vm_memory_mb: 16384
vm_cores: 8
vm_disk_gb: 60
vm_net_bridge: vmbr0
```

> **Security note:** Store `vault_proxmox_token_secret` in an Ansible Vault file:
> ```
> ansible-vault create inventory/group_vars/vault.yml
> ```
> Then reference it in `all.yml` as shown above.

### 1.5 Create the Proxmox API Token

In the Proxmox UI:
1. **Datacenter → Permissions → API Tokens → Add**
2. User: `ansible@pve`, Token ID: `ansible-token`, uncheck *Privilege Separation*
3. Copy the token secret — you won't see it again
4. Grant the token `PVEVMAdmin` + `PVEDatastoreAdmin` role on `/` or the target storage pool

---

## 2. VM Provisioning Playbook

This playbook creates a VM from a cloud-init-enabled Ubuntu 24.04 template. If you don't have a template yet, see the sidebar below.

### 2.1 Create a Cloud-Init Template (one-time setup)

Run on the Proxmox host directly or via an ad-hoc Ansible task:

```bash
# Download Ubuntu 24.04 cloud image
wget -q https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img \
     -O /tmp/ubuntu-24.04-cloud.img

# Create VM shell
qm create 9000 --name ubuntu-cloud-template --memory 2048 --cores 2 \
  --net0 virtio,bridge=vmbr0

# Import disk
qm importdisk 9000 /tmp/ubuntu-24.04-cloud.img local-lvm

# Attach disk and set boot options
qm set 9000 --scsihw virtio-scsi-pci --scsi0 local-lvm:vm-9000-disk-0
qm set 9000 --boot c --bootdisk scsi0
qm set 9000 --ide2 local-lvm:cloudinit
qm set 9000 --serial0 socket --vga serial0

# Convert to template
qm template 9000
```

### 2.2 `playbooks/provision_vm.yml`

```yaml
---
- name: Provision AI lab VM on Proxmox
  hosts: proxmox
  gather_facts: false
  vars:
    vm_id: 200
    vm_name: ollama01
    vm_template_id: 9000
    vm_ip: "192.168.1.100/24"
    vm_gateway: "192.168.1.1"
    vm_dns: "1.1.1.1"
    vm_ssh_key: "{{ lookup('file', '~/.ssh/id_ed25519.pub') }}"

  tasks:
    - name: Clone template into new VM
      community.general.proxmox_kvm:
        api_host: "{{ proxmox_api_host }}"
        api_user: "{{ proxmox_api_user }}"
        api_token_id: "{{ proxmox_api_token_id }}"
        api_token_secret: "{{ proxmox_api_token_secret }}"
        node: "{{ proxmox_node }}"
        clone: "{{ vm_template_id }}"
        vmid: "{{ vm_id }}"
        name: "{{ vm_name }}"
        storage: "{{ proxmox_storage }}"
        full: true
        state: present
      register: clone_result

    - name: Configure VM hardware
      community.general.proxmox_kvm:
        api_host: "{{ proxmox_api_host }}"
        api_user: "{{ proxmox_api_user }}"
        api_token_id: "{{ proxmox_api_token_id }}"
        api_token_secret: "{{ proxmox_api_token_secret }}"
        node: "{{ proxmox_node }}"
        vmid: "{{ vm_id }}"
        memory: "{{ vm_memory_mb }}"
        cores: "{{ vm_cores }}"
        state: present
        update: true

    - name: Resize boot disk
      community.general.proxmox_disk:
        api_host: "{{ proxmox_api_host }}"
        api_user: "{{ proxmox_api_user }}"
        api_token_id: "{{ proxmox_api_token_id }}"
        api_token_secret: "{{ proxmox_api_token_secret }}"
        vmid: "{{ vm_id }}"
        disk: scsi0
        size: "{{ vm_disk_gb }}G"
        state: resized

    - name: Set cloud-init networking
      community.general.proxmox_kvm:
        api_host: "{{ proxmox_api_host }}"
        api_user: "{{ proxmox_api_user }}"
        api_token_id: "{{ proxmox_api_token_id }}"
        api_token_secret: "{{ proxmox_api_token_secret }}"
        node: "{{ proxmox_node }}"
        vmid: "{{ vm_id }}"
        ipconfig:
          ipconfig0: "ip={{ vm_ip }},gw={{ vm_gateway }}"
        nameservers: "{{ vm_dns }}"
        sshkeys: "{{ vm_ssh_key }}"
        state: present
        update: true

    - name: Start VM
      community.general.proxmox_kvm:
        api_host: "{{ proxmox_api_host }}"
        api_user: "{{ proxmox_api_user }}"
        api_token_id: "{{ proxmox_api_token_id }}"
        api_token_secret: "{{ proxmox_api_token_secret }}"
        node: "{{ proxmox_node }}"
        vmid: "{{ vm_id }}"
        state: started

    - name: Wait for SSH to become available
      ansible.builtin.wait_for:
        host: "{{ vm_ip | ansible.utils.ipaddr('address') }}"
        port: 22
        delay: 10
        timeout: 120
      delegate_to: localhost
```

Run it:

```bash
ansible-playbook playbooks/provision_vm.yml \
  --ask-vault-pass \
  -e "vm_id=200 vm_name=ollama01"
```

---

## 3. GPU Pass-Through Configuration

GPU pass-through lets the VM talk directly to the physical GPU instead of going through an emulation layer — essential for inference performance.

### 3.1 How It Works

Proxmox uses the Linux kernel's VFIO driver to isolate the GPU from the host OS and hand it to the VM. The host must:

1. Enable IOMMU in BIOS and kernel
2. Bind the GPU to the `vfio-pci` driver instead of `nouveau`/`nvidia`
3. Attach the PCI device to the VM config

### 3.2 `playbooks/gpu_passthrough.yml`

```yaml
---
- name: Configure GPU pass-through on Proxmox host
  hosts: proxmox
  gather_facts: true

  vars:
    gpu_vendor_id: "10de"    # NVIDIA vendor ID; use 1002 for AMD
    iommu_param: "intel_iommu=on iommu=pt"   # use amd_iommu=on for AMD

  tasks:
    - name: Detect GPU PCI IDs on host
      ansible.builtin.shell: |
        lspci -nn | grep -iE 'VGA|3D|Display' | grep -i nvidia
      register: gpu_info
      changed_when: false

    - name: Display detected GPUs
      ansible.builtin.debug:
        msg: "{{ gpu_info.stdout_lines }}"

    - name: Enable IOMMU in GRUB
      ansible.builtin.lineinfile:
        path: /etc/default/grub
        regexp: '^GRUB_CMDLINE_LINUX_DEFAULT='
        line: 'GRUB_CMDLINE_LINUX_DEFAULT="quiet {{ iommu_param }}"'
        backup: true
      register: grub_changed

    - name: Update GRUB
      ansible.builtin.command: update-grub
      when: grub_changed.changed

    - name: Load VFIO modules at boot
      ansible.builtin.copy:
        dest: /etc/modules-load.d/vfio.conf
        content: |
          vfio
          vfio_iommu_type1
          vfio_pci
        mode: "0644"

    - name: Blacklist open-source GPU drivers on host
      ansible.builtin.copy:
        dest: /etc/modprobe.d/blacklist-gpu.conf
        content: |
          blacklist nouveau
          blacklist nvidia
          blacklist radeon
        mode: "0644"

    - name: Get GPU PCI IDs for VFIO binding
      ansible.builtin.shell: |
        lspci -nn | grep -i nvidia | grep -oP '\[\K[0-9a-f]{4}:[0-9a-f]{4}(?=\])' | \
          paste -sd ',' -
      register: gpu_ids
      changed_when: false

    - name: Bind GPU to VFIO
      ansible.builtin.copy:
        dest: /etc/modprobe.d/vfio.conf
        content: "options vfio-pci ids={{ gpu_ids.stdout }}\n"
        mode: "0644"
      when: gpu_ids.stdout | length > 0

    - name: Update initramfs
      ansible.builtin.command: update-initramfs -u -k all
      when: grub_changed.changed or gpu_ids.stdout | length > 0

    - name: Reboot host to apply IOMMU changes
      ansible.builtin.reboot:
        msg: "Rebooting to enable IOMMU and VFIO"
        reboot_timeout: 300
      when: grub_changed.changed

    - name: Attach GPU to VM
      community.general.proxmox_kvm:
        api_host: "{{ proxmox_api_host }}"
        api_user: "{{ proxmox_api_user }}"
        api_token_id: "{{ proxmox_api_token_id }}"
        api_token_secret: "{{ proxmox_api_token_secret }}"
        node: "{{ proxmox_node }}"
        vmid: "{{ vm_id }}"
        hostpci:
          hostpci0: "{{ pci_slot }},pcie=1,x-vga=1"
        state: present
        update: true
      vars:
        # Override per-run: ansible-playbook ... -e "pci_slot=0000:01:00"
        pci_slot: "{{ gpu_pci_slot | default('0000:01:00') }}"
        vm_id: "{{ target_vm_id | default(200) }}"
```

> **Finding your PCI slot:** On the Proxmox host, run `lspci | grep -i nvidia`. The address like `01:00.0` is your PCI slot — pass it as `gpu_pci_slot=0000:01:00`.

Run it:

```bash
ansible-playbook playbooks/gpu_passthrough.yml \
  --ask-vault-pass \
  -e "gpu_pci_slot=0000:01:00 target_vm_id=200"
```

---

## 4. NVIDIA Driver Installation (in the VM)

With the GPU visible to the VM, install the NVIDIA drivers and CUDA toolkit inside the guest.

### 4.1 `playbooks/nvidia_drivers.yml`

```yaml
---
- name: Install NVIDIA drivers and CUDA inside AI VM
  hosts: ai_vms
  become: true
  gather_facts: true

  vars:
    nvidia_driver_version: "550"     # use `ubuntu-drivers devices` to find recommended
    cuda_keyring_url: >-
      https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb

  tasks:
    - name: Install prerequisite packages
      ansible.builtin.apt:
        name:
          - linux-headers-{{ ansible_kernel }}
          - build-essential
          - dkms
          - ubuntu-drivers-common
        update_cache: true
        state: present

    - name: Install NVIDIA driver
      ansible.builtin.apt:
        name: "nvidia-driver-{{ nvidia_driver_version }}"
        state: present
      register: nvidia_installed

    - name: Download CUDA keyring package
      ansible.builtin.get_url:
        url: "{{ cuda_keyring_url }}"
        dest: /tmp/cuda-keyring.deb
        mode: "0644"

    - name: Install CUDA keyring
      ansible.builtin.apt:
        deb: /tmp/cuda-keyring.deb
        state: present

    - name: Install CUDA toolkit
      ansible.builtin.apt:
        name: cuda-toolkit
        update_cache: true
        state: present

    - name: Add CUDA to PATH for all users
      ansible.builtin.copy:
        dest: /etc/profile.d/cuda.sh
        content: |
          export PATH="/usr/local/cuda/bin:$PATH"
          export LD_LIBRARY_PATH="/usr/local/cuda/lib64:$LD_LIBRARY_PATH"
        mode: "0644"

    - name: Reboot to load new driver
      ansible.builtin.reboot:
        reboot_timeout: 300
      when: nvidia_installed.changed
```

---

## 5. Ollama Installation and Validation

Ollama is a local LLM server with a Docker-style CLI. This role installs it, configures systemd, and runs a smoke test.

### 5.1 `roles/ollama/defaults/main.yml`

```yaml
ollama_version: "latest"
ollama_install_url: "https://ollama.com/install.sh"
ollama_host: "0.0.0.0"
ollama_port: 11434
ollama_models:
  - phi3          # ~2GB — fast smoke-test model
```

### 5.2 `roles/ollama/tasks/main.yml`

```yaml
---
- name: Download Ollama installer
  ansible.builtin.get_url:
    url: "{{ ollama_install_url }}"
    dest: /tmp/ollama-install.sh
    mode: "0755"

- name: Run Ollama installer
  ansible.builtin.command: /tmp/ollama-install.sh
  args:
    creates: /usr/local/bin/ollama
  environment:
    OLLAMA_VERSION: "{{ ollama_version }}"

- name: Configure Ollama systemd override
  ansible.builtin.copy:
    dest: /etc/systemd/system/ollama.service.d/override.conf
    content: |
      [Service]
      Environment="OLLAMA_HOST={{ ollama_host }}:{{ ollama_port }}"
    mode: "0644"
  notify: Restart ollama

- name: Enable and start Ollama service
  ansible.builtin.systemd:
    name: ollama
    enabled: true
    state: started
    daemon_reload: true

- name: Wait for Ollama API to be ready
  ansible.builtin.wait_for:
    host: 127.0.0.1
    port: "{{ ollama_port }}"
    timeout: 60

- name: Pull smoke-test models
  ansible.builtin.command: "ollama pull {{ item }}"
  loop: "{{ ollama_models }}"
  changed_when: true

- name: Smoke-test — run inference
  ansible.builtin.uri:
    url: "http://127.0.0.1:{{ ollama_port }}/api/generate"
    method: POST
    body_format: json
    body:
      model: phi3
      prompt: "In one sentence, what is a Kubernetes pod?"
      stream: false
    return_content: true
    timeout: 120
  register: ollama_response

- name: Display inference response
  ansible.builtin.debug:
    msg: "{{ ollama_response.json.response }}"
```

### 5.3 Add handlers file

`roles/ollama/handlers/main.yml`:

```yaml
---
- name: Restart ollama
  ansible.builtin.systemd:
    name: ollama
    state: restarted
```

### 5.4 `playbooks/ollama.yml`

```yaml
---
- name: Install and validate Ollama on AI VMs
  hosts: ai_vms
  become: true
  roles:
    - ollama
```

---

## 6. Running the Full Stack

Execute playbooks in order:

```bash
# 1. Provision the VM
ansible-playbook playbooks/provision_vm.yml --ask-vault-pass

# 2. Configure GPU pass-through on Proxmox host (skip if no GPU)
ansible-playbook playbooks/gpu_passthrough.yml --ask-vault-pass \
  -e "gpu_pci_slot=0000:01:00 target_vm_id=200"

# 3. Install NVIDIA drivers inside the VM (skip if no GPU)
ansible-playbook playbooks/nvidia_drivers.yml --ask-vault-pass

# 4. Install Ollama
ansible-playbook playbooks/ollama.yml --ask-vault-pass
```

Or chain them with a site playbook:

```yaml
# site.yml
---
- import_playbook: playbooks/provision_vm.yml
- import_playbook: playbooks/gpu_passthrough.yml
- import_playbook: playbooks/nvidia_drivers.yml
- import_playbook: playbooks/ollama.yml
```

```bash
ansible-playbook site.yml --ask-vault-pass \
  -e "gpu_pci_slot=0000:01:00 target_vm_id=200"
```

---

## 7. Post-Install Validation

After all playbooks complete, verify the stack from outside the VM:

```bash
# Check GPU is visible inside VM
ssh ubuntu@192.168.1.100 nvidia-smi

# List available models
curl http://192.168.1.100:11434/api/tags | python3 -m json.tool

# Run a prompt
curl http://192.168.1.100:11434/api/generate \
  -d '{"model":"phi3","prompt":"Explain Kubernetes in one sentence","stream":false}' \
  | python3 -m json.tool
```

Expected `nvidia-smi` output (truncated):

```
+-----------------------------------------------------------------------------+
| NVIDIA-SMI 550.x      Driver Version: 550.x    CUDA Version: 12.4          |
|-------------------------------+----------------------+----------------------|
| GPU  Name        Persistence-M| Bus-Id        Disp.A | Volatile Uncorr. ECC |
|   0  NVIDIA RTX 3090     Off  | 00000000:01:00.0 Off |                  N/A |
+-----------------------------------------------------------------------------+
```

Expected Ollama response:

```json
{
  "model": "phi3",
  "response": "Kubernetes is an open-source container orchestration platform...",
  "done": true
}
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| VM fails to start after GPU attach | IOMMU not enabled or VFIO not loaded | Verify `dmesg | grep -i iommu` on host shows IOMMU active |
| `nvidia-smi` not found in VM | Driver install failed silently | Re-run `nvidia_drivers.yml` with `-v`; check `dkms status` |
| Ollama service crashes on start | Port conflict or GPU memory | Check `journalctl -u ollama -n 50`; try without GPU env var |
| Proxmox module tasks fail with 403 | API token permissions too narrow | Grant `Administrator` role on `/` temporarily to diagnose |
| `community.general.proxmox_kvm` not found | Collection not installed | `ansible-galaxy collection install community.general` |

---

## Next Steps

- **Scale out:** Add more hosts to `[proxmox]` and more VM entries to `[ai_vms]` — the playbooks are idempotent.
- **GitOps:** Store this repo in Git and trigger playbooks from a CI pipeline when the inventory changes.
- **Model management:** Extend the `ollama_models` list in `defaults/main.yml` to pre-pull larger models (`llama3`, `mixtral`).
- **Monitoring:** Pair with a Prometheus + DCGM exporter to track GPU utilization across VMs.
