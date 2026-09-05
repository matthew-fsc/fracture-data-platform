# Reporting: pack storage and signed-URL delivery.
#
# Packs are delivered two ways (spec section 11): hosted for retainer clients,
# and PDF for board and lender distribution. Both come from the same rendered
# artefact, so a lender's PDF and the client's hosted page cannot disagree.
#
# Delivery is by short-lived signed URL rather than a public object, because a
# board pack that is one guessed key away from being public is a breach waiting
# for a bored intern.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

resource "aws_s3_bucket" "packs" {
  bucket = var.pack_bucket
  tags   = var.tags
}

resource "aws_s3_bucket_public_access_block" "packs" {
  bucket                  = aws_s3_bucket.packs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "packs" {
  bucket = aws_s3_bucket.packs.id
  # An issued pack is immutable. Versioning means a superseding pack never
  # destroys the one a board already read.
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "packs" {
  bucket = aws_s3_bucket.packs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_iam_policy" "pack_delivery" {
  name        = "fracture-${var.environment}-pack-delivery"
  description = "Read one tenant's packs and mint a signed URL for them."

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject"]
      Resource = "${aws_s3_bucket.packs.arn}/tenants/$${aws:PrincipalTag/tenant}/*"
    }]
  })
}

resource "aws_cloudwatch_log_group" "pack_delivery" {
  name              = "/fracture/${var.environment}/pack-delivery"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}
