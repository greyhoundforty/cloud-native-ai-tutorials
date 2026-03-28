# Demo

Terraform for GPU cloud instances.

## Prerequisites
- Terraform >= 1.6, cloud provider credentials, SSH key pair

## Quick Start
```bash
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your region, instance type, key path
terraform init
terraform plan
terraform apply
# When done:
terraform destroy
```

`user_data.sh.tpl` bootstraps CUDA drivers, Docker, and Ollama.
