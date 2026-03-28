output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.gpu.id
}

output "public_ip" {
  description = "Public IP of the instance (via Elastic IP)"
  value       = aws_eip.gpu.public_ip
}

output "ssh_command" {
  description = "Ready-to-run SSH command"
  value       = "ssh -i ~/.ssh/id_ed25519 ubuntu@${aws_eip.gpu.public_ip}"
}

output "ollama_endpoint" {
  description = "Ollama API endpoint"
  value       = "http://${aws_eip.gpu.public_ip}:11434"
}

output "hourly_cost_usd" {
  description = "Approximate on-demand hourly cost (us-east-1, March 2026)"
  value = {
    "g4dn.xlarge"  = "$0.526"
    "g4dn.2xlarge" = "$0.752"
    "g5.xlarge"    = "$1.006"
    "g5.2xlarge"   = "$1.212"
    "p3.2xlarge"   = "$3.060"
  }[var.instance_type]
}
