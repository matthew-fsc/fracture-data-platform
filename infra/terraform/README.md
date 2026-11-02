# Infrastructure

Five modules (spec section 10). Tenant standup is one command:

    terraform apply -var tenant_slug=meridian-partners -var motion=operating

Target is under 30 minutes from signed SOW to a tenant that can accept its first
credential. Diligence tenants use the same module with `motion=diligence` and a
populated `archive_after`, so promoting a diligence tenant to operating on close
is a variable change, not a rebuild -- which is what preserves the
credit-at-close economics (spec section 13).

| Module | Contents |
|---|---|
| `network` | VPC, private subnets, VPC endpoints for S3, Secrets Manager and KMS |
| `control-plane` | control database, registry, Dagster deployment |
| `tenant` | database, four roles, KMS key, S3 prefix, secret paths, Dagster partition |
| `compute` | Fargate services and task definitions |
| `reporting` | pack storage, signed-URL delivery |

## The host decision (spec section 17.1)

`var.db_host_backend` selects `neon` or `rds`. The abstraction is one variable
and one output (`db_host`) precisely so the decision stays reversible: Neon's
branching makes per-tenant dev and point-in-time investigation genuinely
cheaper, and RDS is the easier answer on a PE security questionnaire. Nothing
above this layer knows which is in use -- the platform assembles its DSN from
the control plane's `db_host` column.

## What Terraform owns and what Python owns

Terraform owns the KMS key, the S3 prefix and object-lock policy, the secret
paths, and the database instance. `fracture.control.provisioning` owns the roles,
the schema and the grants, because those have to be re-appliable on every
migration fan-out rather than only at standup. Both read the same role list, so
they cannot drift: `provisioning.ROLES` and `local.roles` below are the same four
names, and `tests/test_provisioning.py` asserts it.
