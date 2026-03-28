variable "region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix for all resource names"
  type        = string
  default     = "llm-gpu"
}

variable "instance_type" {
  description = "EC2 instance type with GPU"
  type        = string
  default     = "g4dn.xlarge" # 1× NVIDIA T4 16 GB, 4 vCPU, 16 GB RAM

  validation {
    condition = contains([
      "g4dn.xlarge",  # T4 16 GB  — cheapest GPU option
      "g4dn.2xlarge", # T4 16 GB  — more CPU/RAM
      "g5.xlarge",    # A10G 24 GB — better for larger models
      "g5.2xlarge",   # A10G 24 GB
      "p3.2xlarge",   # V100 16 GB — legacy but widely available
    ], var.instance_type)
    error_message = "Choose a supported GPU instance type."
  }
}

variable "root_volume_gb" {
  description = "Root EBS volume size in GiB"
  type        = number
  default     = 60
}

variable "ssh_public_key" {
  description = "SSH public key material (contents of id_ed25519.pub or similar)"
  type        = string
}

variable "allowed_cidr" {
  description = "CIDR allowed to reach SSH/Ollama ports. Restrict to your IP in production."
  type        = string
  default     = "0.0.0.0/0"
}

variable "ollama_model" {
  description = "Ollama model to pull on first boot (e.g. mistral, llama3, phi3)"
  type        = string
  default     = "mistral"
}

variable "common_tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default = {
    Project   = "llm-gpu-tutorial"
    ManagedBy = "terraform"
  }
}
