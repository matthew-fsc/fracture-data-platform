# Compute: Fargate task definitions for the Dagster daemon, the webserver, and
# the per-run executor.
#
# Each run executes in an isolated task with exactly one tenant's DSN injected
# via a scoped role assumption (spec section 7). The IAM policy below is the
# mechanism: the executor task role can assume only the per-tenant role named in
# the run's configuration, so there is no code path that holds two tenants'
# credentials simultaneously even if the orchestrator has a bug.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

resource "aws_iam_role" "executor" {
  name = "fracture-${var.environment}-executor"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

# Deliberately narrow: the executor may read one tenant's secrets and decrypt
# with one tenant's key, both selected by the tenant tag on the running task.
resource "aws_iam_role_policy" "executor_tenant_scope" {
  name = "tenant-scoped-access"
  role = aws_iam_role.executor.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = "arn:aws:secretsmanager:${var.region}:${var.account_id}:secret:tenants/*"
        Condition = {
          StringEquals = {
            "aws:ResourceTag/tenant" = "$${aws:PrincipalTag/tenant}"
          }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:ResourceTag/tenant" = "$${aws:PrincipalTag/tenant}"
          }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "arn:aws:s3:::${var.artifact_bucket}/tenants/$${aws:PrincipalTag/tenant}/*"
      }
    ]
  })
}

resource "aws_ecs_task_definition" "dagster_daemon" {
  family                   = "fracture-${var.environment}-dagster-daemon"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.daemon_cpu
  memory                   = var.daemon_memory
  execution_role_arn       = var.task_execution_role_arn
  task_role_arn            = aws_iam_role.executor.arn

  container_definitions = jsonencode([{
    name      = "dagster-daemon"
    image     = var.image
    essential = true
    command   = ["dagster-daemon", "run"]
    environment = [
      { name = "FRACTURE_ENV", value = var.environment },
      { name = "DAGSTER_HOME", value = "/opt/dagster/home" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = var.log_group
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "daemon"
      }
    }
  }])

  tags = var.tags
}

resource "aws_ecs_task_definition" "run_executor" {
  family                   = "fracture-${var.environment}-run"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.run_cpu
  memory                   = var.run_memory
  execution_role_arn       = var.task_execution_role_arn
  task_role_arn            = aws_iam_role.executor.arn

  container_definitions = jsonencode([{
    name      = "run"
    image     = var.image
    essential = true
    environment = [
      { name = "FRACTURE_ENV", value = var.environment },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = var.log_group
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "run"
      }
    }
  }])

  tags = var.tags
}
