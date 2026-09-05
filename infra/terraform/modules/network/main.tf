# Network: a VPC with no route to the public internet for the data path.
#
# Tenant databases and the Fargate tasks that reach them sit in private subnets.
# Everything the platform needs from AWS -- S3 for artifacts, Secrets Manager for
# credentials, KMS for the per-tenant keys -- arrives over VPC endpoints, so a
# security questionnaire's "does client data traverse the public internet"
# answers itself.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

resource "aws_vpc" "main" {
  cidr_block           = var.cidr_block
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(var.tags, { Name = "fracture-${var.environment}" })
}

resource "aws_subnet" "private" {
  count             = length(var.availability_zones)
  vpc_id            = aws_vpc.main.id
  availability_zone = var.availability_zones[count.index]
  cidr_block        = cidrsubnet(var.cidr_block, 4, count.index)
  tags              = merge(var.tags, { Name = "fracture-${var.environment}-private-${count.index}" })
}

resource "aws_subnet" "public" {
  count                   = var.enable_nat ? length(var.availability_zones) : 0
  vpc_id                  = aws_vpc.main.id
  availability_zone       = var.availability_zones[count.index]
  cidr_block              = cidrsubnet(var.cidr_block, 4, count.index + 8)
  map_public_ip_on_launch = false
  tags                    = merge(var.tags, { Name = "fracture-${var.environment}-public-${count.index}" })
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
  tags              = var.tags
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  tags   = merge(var.tags, { Name = "fracture-${var.environment}-private" })
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

resource "aws_security_group" "interface_endpoints" {
  name        = "fracture-${var.environment}-endpoints"
  description = "HTTPS from the private subnets to AWS interface endpoints"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTPS from private subnets"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [for s in aws_subnet.private : s.cidr_block]
  }

  tags = var.tags
}

resource "aws_vpc_endpoint" "interface" {
  for_each = toset(["secretsmanager", "kms", "logs", "ecr.api", "ecr.dkr"])

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.interface_endpoints.id]
  private_dns_enabled = true
  tags                = merge(var.tags, { Name = "fracture-${var.environment}-${each.key}" })
}

resource "aws_flow_log" "vpc" {
  count                = var.enable_flow_logs ? 1 : 0
  vpc_id               = aws_vpc.main.id
  traffic_type         = "ALL"
  log_destination_type = "s3"
  log_destination      = var.flow_log_bucket_arn
  tags                 = var.tags
}
