# Control plane: fracture_control plus the Dagster deployment.
#
# This database is never joined to tenant data. postgres_fdw and dblink are
# deliberately not installed anywhere (spec section 3.2); the test suite asserts
# that, because a convenient cross-database join is exactly how a
# database-per-tenant guarantee becomes a claim rather than a control.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws        = { source = "hashicorp/aws", version = "~> 5.0" }
    postgresql = { source = "cyrilgdn/postgresql", version = "~> 1.22" }
  }
}

resource "postgresql_database" "control" {
  name  = "fracture_control"
  owner = var.admin_role
}

resource "aws_s3_bucket" "artifacts" {
  bucket              = var.artifact_bucket
  object_lock_enabled = true
  tags                = var.tags
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Audit log shipped out of the tenant database to append-only storage
# (spec section 9). Out of the database on purpose: an audit log a compromised
# database role can rewrite is not an audit log.
resource "aws_s3_bucket" "audit" {
  bucket              = var.audit_bucket
  object_lock_enabled = true
  tags                = var.tags
}

resource "aws_s3_bucket_object_lock_configuration" "audit" {
  bucket = aws_s3_bucket.audit.id
  rule {
    default_retention {
      mode = "COMPLIANCE"
      days = var.audit_retention_days
    }
  }
}

resource "aws_ecs_cluster" "platform" {
  name = "fracture-${var.environment}"
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
  tags = var.tags
}

resource "aws_cloudwatch_log_group" "dagster" {
  name              = "/fracture/${var.environment}/dagster"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}
