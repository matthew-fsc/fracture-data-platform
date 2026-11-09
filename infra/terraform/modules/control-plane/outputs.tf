output "control_database" { value = postgresql_database.control.name }
output "artifact_bucket" { value = aws_s3_bucket.artifacts.id }
output "audit_bucket" { value = aws_s3_bucket.audit.id }
output "ecs_cluster_arn" { value = aws_ecs_cluster.platform.arn }
