# Tenant module: one database, four roles, one KMS key, one S3 prefix.
#
# Database-per-tenant is the literal implementation of "isolation enforced at
# the database rather than in application code" (spec section 3.1). Export in
# full becomes pg_dump; at exit the buyer is handed a database, not a filtered
# extract.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws        = { source = "hashicorp/aws", version = "~> 5.0" }
    postgresql = { source = "cyrilgdn/postgresql", version = "~> 1.22" }
  }
}

locals {
  # Same four names as fracture.control.provisioning.ROLES. Kept in this order
  # so a diff against the Python is a straight line-for-line read.
  roles    = ["owner", "loader", "transform", "reader"]
  db_name  = "tenant_${replace(var.tenant_slug, "-", "_")}"
  s3_prefix = "tenants/${var.tenant_slug}"
  role_names = { for r in local.roles : r => "t_${replace(var.tenant_slug, "-", "_")}_${r}" }

  is_diligence = var.motion == "diligence"
}

# Per-tenant key, so a key compromise is bounded to one tenant and revoking a
# tenant's access is a key policy change rather than a data migration.
resource "aws_kms_key" "tenant" {
  description             = "Fracture tenant ${var.tenant_slug} envelope encryption"
  enable_key_rotation     = true
  deletion_window_in_days = local.is_diligence ? 7 : 30

  tags = merge(var.tags, {
    tenant = var.tenant_slug
    motion = var.motion
  })
}

resource "aws_kms_alias" "tenant" {
  name          = "alias/fracture-${var.tenant_slug}"
  target_key_id = aws_kms_key.tenant.key_id
}

# Raw artifacts: the evidence trail, held separately from the database on
# purpose. If a tenant disputes a number you produce the file you received and
# its hash (spec section 2).
resource "aws_s3_bucket_object_lock_configuration" "raw" {
  count  = var.enable_object_lock ? 1 : 0
  bucket = var.artifact_bucket

  rule {
    default_retention {
      mode = "GOVERNANCE"
      # Seven years, per the open decision in spec 17.4, with a per-tenant
      # override. Indefinite is right for the audit story and wrong for a breach.
      days = var.raw_retention_days
    }
  }
}

resource "postgresql_database" "tenant" {
  name              = local.db_name
  owner             = var.admin_role
  lc_collate        = "en_US.UTF-8"
  connection_limit  = var.connection_limit
  allow_connections = true
}

resource "random_password" "role" {
  for_each = toset(local.roles)
  length   = 40
  special  = false
}

resource "aws_secretsmanager_secret" "role" {
  for_each   = toset(local.roles)
  name       = "${local.s3_prefix}/db/${each.key}"
  kms_key_id = aws_kms_key.tenant.arn
  tags       = merge(var.tags, { tenant = var.tenant_slug, role = each.key })
}

resource "aws_secretsmanager_secret_version" "role" {
  for_each  = toset(local.roles)
  secret_id = aws_secretsmanager_secret.role[each.key].id
  secret_string = jsonencode({
    username = local.role_names[each.key]
    password = random_password.role[each.key].result
    dbname   = local.db_name
  })
}

resource "postgresql_role" "tenant" {
  for_each = toset(local.roles)

  name     = local.role_names[each.key]
  login    = true
  password = random_password.role[each.key].result

  # Only the owner role may create objects; loader, transform and reader are
  # granted exactly what fracture.control.provisioning.ROLE_GRANTS gives them.
  create_database = false
  create_role     = false
  superuser       = false
  inherit         = true
}

# PUBLIC connect is revoked so a role from one tenant cannot reach another
# tenant's database even if it learns the name.
resource "postgresql_grant" "revoke_public_connect" {
  database    = postgresql_database.tenant.name
  role        = "public"
  object_type = "database"
  privileges  = []
}

resource "postgresql_grant" "connect" {
  for_each = toset(local.roles)

  database    = postgresql_database.tenant.name
  role        = postgresql_role.tenant[each.key].name
  object_type = "database"
  privileges  = ["CONNECT"]
}

# Registry row. Everything downstream assembles its DSN from this, so the
# database host stays a control-plane fact rather than deploy config.
resource "null_resource" "register_tenant" {
  triggers = {
    tenant_slug = var.tenant_slug
    db_name     = local.db_name
    motion      = var.motion
    kms_key     = aws_kms_key.tenant.arn
  }

  provisioner "local-exec" {
    command = join(" ", [
      "fracture tenant register",
      "--slug ${var.tenant_slug}",
      "--legal-name '${var.legal_name}'",
      "--motion ${var.motion}",
      "--kms-key-arn ${aws_kms_key.tenant.arn}",
      "--db-host ${var.db_host}",
      local.is_diligence ? "--archive-after ${var.archive_after}" : "",
      "--provision",
    ])
  }

  depends_on = [postgresql_database.tenant, postgresql_role.tenant]
}
