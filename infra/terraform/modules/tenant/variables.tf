variable "tenant_slug" {
  type        = string
  description = "DNS-safe slug. Used in the database name, the S3 prefix and every role name."

  validation {
    # Matches the control plane's own constraint, so a slug Terraform accepts is
    # a slug the registry will accept.
    condition     = can(regex("^[a-z][a-z0-9-]{1,38}[a-z0-9]$", var.tenant_slug))
    error_message = "tenant_slug must be lowercase alphanumeric with hyphens, 3-40 characters, starting with a letter."
  }
}

variable "legal_name" {
  type        = string
  description = "The acquirer's legal name, as it appears on the SOW."
}

variable "motion" {
  type        = string
  description = "diligence or operating. Diligence tenants are ephemeral and must carry archive_after."

  validation {
    condition     = contains(["diligence", "operating"], var.motion)
    error_message = "motion must be diligence or operating."
  }
}

variable "archive_after" {
  type        = string
  default     = null
  description = "Destruction date for a diligence tenant. Close plus 30 days."

  validation {
    condition     = var.archive_after == null || can(formatdate("YYYY-MM-DD", "${var.archive_after}T00:00:00Z"))
    error_message = "archive_after must be a YYYY-MM-DD date."
  }
}

variable "db_host_backend" {
  type        = string
  default     = "neon"
  description = "neon or rds. See infra/terraform/README.md; the abstraction is deliberately one variable."

  validation {
    condition     = contains(["neon", "rds"], var.db_host_backend)
    error_message = "db_host_backend must be neon or rds."
  }
}

variable "db_host" {
  type        = string
  description = "Resolved database host, written to the control plane registry."
}

variable "admin_role" {
  type        = string
  default     = "fracture_admin"
  description = "Role that owns the tenant database; used by migrations only."
}

variable "artifact_bucket" {
  type        = string
  description = "Bucket holding every raw extraction artifact."
}

variable "raw_retention_days" {
  type        = number
  default     = 2557 # seven years
  description = "Raw artifact retention. Spec 17.4 proposes seven years with a per-tenant override."
}

variable "enable_object_lock" {
  type        = bool
  default     = true
  description = "Object lock on the raw prefix. Append-only evidence is only append-only if it cannot be deleted."
}

variable "connection_limit" {
  type        = number
  default     = 40
  description = "Per-tenant connection cap. Bounds the noisy-neighbour blast radius."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Tags applied to every taggable resource."
}
