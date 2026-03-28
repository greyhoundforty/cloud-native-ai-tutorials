terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
  }
}

provider "aws" {
  region = var.region
}

###############################################################################
# Data sources
###############################################################################

data "aws_ami" "ubuntu_2204" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

###############################################################################
# Security group
###############################################################################

resource "aws_security_group" "gpu_inference" {
  name        = "${var.name_prefix}-gpu-inference"
  description = "Allow SSH access to GPU inference instance"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.allowed_cidr]
  }

  # Ollama API (optional — remove if not needed externally)
  ingress {
    description = "Ollama API"
    from_port   = 11434
    to_port     = 11434
    protocol    = "tcp"
    cidr_blocks = [var.allowed_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = var.common_tags
}

###############################################################################
# Key pair (bring your own public key)
###############################################################################

resource "aws_key_pair" "deployer" {
  key_name   = "${var.name_prefix}-key"
  public_key = var.ssh_public_key
}

###############################################################################
# IAM instance profile (minimal — required for SSM Session Manager fallback)
###############################################################################

data "aws_iam_policy_document" "ec2_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "gpu_instance" {
  name               = "${var.name_prefix}-gpu-instance"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json
  tags               = var.common_tags
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.gpu_instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "gpu_instance" {
  name = "${var.name_prefix}-gpu-instance"
  role = aws_iam_role.gpu_instance.name
}

###############################################################################
# EC2 instance
###############################################################################

resource "aws_instance" "gpu" {
  ami                    = data.aws_ami.ubuntu_2204.id
  instance_type          = var.instance_type
  key_name               = aws_key_pair.deployer.key_name
  subnet_id              = tolist(data.aws_subnets.default.ids)[0]
  vpc_security_group_ids = [aws_security_group.gpu_inference.id]
  iam_instance_profile   = aws_iam_instance_profile.gpu_instance.name

  # Enough space for model weights (Mistral-7B ~4 GB quantised, Llama-3-8B ~5 GB)
  root_block_device {
    volume_size           = var.root_volume_gb
    volume_type           = "gp3"
    delete_on_termination = true
    encrypted             = true
  }

  user_data = templatefile("${path.module}/user_data.sh.tpl", {
    ollama_model = var.ollama_model
  })

  # Prevent accidental destroy when terraform plan is run interactively
  lifecycle {
    prevent_destroy = false
  }

  tags = merge(var.common_tags, {
    Name = "${var.name_prefix}-gpu"
  })
}

###############################################################################
# Elastic IP (optional but useful for stable SSH address during dev)
###############################################################################

resource "aws_eip" "gpu" {
  instance = aws_instance.gpu.id
  domain   = "vpc"
  tags     = var.common_tags
}
