output "db_name" {
  value       = local.db_name
  description = "Tenant database name. Export in full is pg_dump against this."
}

output "s3_prefix" {
  value       = local.s3_prefix
  description = "Raw artifact prefix for this tenant."
}

output "kms_key_arn" {
  value       = aws_kms_key.tenant.arn
  description = "Per-tenant envelope encryption key."
}

output "role_names" {
  value       = local.role_names
  description = "The four per-tenant roles, keyed by role."
}

output "secret_paths" {
  value       = { for r in local.roles : r => aws_secretsmanager_secret.role[r].name }
  description = "Where each role's credential lives. Resolved at run time, never stored in config."
}

output "export_command" {
  value       = "pg_dump --no-owner --format=custom --dbname=postgresql://${var.db_host}/${local.db_name} --file=${var.tenant_slug}-export.dump"
  description = "The contractual full export (spec section 3.1), as one shell command."
}
