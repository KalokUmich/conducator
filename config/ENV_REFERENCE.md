# Environment Variable Reference

When deploying Conductor to ECS (or any container platform), set these environment variables to override the YAML config. All are optional for local dev but required for production.

## Database & Cache

| Variable | Example | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://conductor:SECRET@my-rds.abc.eu-west-2.rds.amazonaws.com:5432/conductor` | Full async Postgres URL. Overrides `postgres.*` + `secrets.postgres.*` from YAML entirely. |
| `REDIS_URL` | `redis://:SECRET@my-elasticache.abc.euw2.cache.amazonaws.com:6379/0` | Full Redis URL. Overrides `redis.*` + `secrets.redis.*` from YAML entirely. |

## Langfuse (set on the Langfuse container, not the backend)

| Variable | Example | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://conductor:SECRET@my-rds.abc.eu-west-2.rds.amazonaws.com:5432/langfuse` | Langfuse's own database (same RDS instance, different database name). |
| `NEXTAUTH_SECRET` | (random 32+ char string) | Session encryption key. **Must change from dev default.** |
| `NEXTAUTH_URL` | `https://langfuse.internal.example.com` | Langfuse's own public URL. |
| `SALT` | (random 32+ char string) | Hashing salt. **Must change from dev default.** |

## Server

| Variable | Example | Description |
|---|---|---|
| `BACKEND_HOST` | `0.0.0.0` | Bind address. |
| `BACKEND_PORT` | `8000` | Bind port. |

## AI Providers (if not using YAML secrets)

| Variable | Example | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-...` | Anthropic direct API key. |
| `AWS_ACCESS_KEY_ID` | `AKIA...` | Bedrock credentials (or use ECS task role instead). |
| `AWS_SECRET_ACCESS_KEY` | `...` | Bedrock credentials. |
| `AWS_SESSION_TOKEN` | `...` | Optional, for temporary STS credentials. |
| `AWS_DEFAULT_REGION` | `eu-west-2` | Bedrock region. |
| `OPENAI_API_KEY` | `sk-...` | OpenAI API key. |

## Jira Integration

| Variable | Example | Description |
|---|---|---|
| `JIRA_CLIENT_ID` | `ezAx...` | Atlassian OAuth 2.0 client ID. |
| `JIRA_CLIENT_SECRET` | `ATOA...` | Atlassian OAuth 2.0 client secret. |

## ECS Task Definition Snippet

```json
{
  "containerDefinitions": [
    {
      "name": "conductor-backend",
      "image": "conductor/backend:latest",
      "portMappings": [{ "containerPort": 8000 }],
      "environment": [
        { "name": "BACKEND_HOST", "value": "0.0.0.0" },
        { "name": "BACKEND_PORT", "value": "8000" }
      ],
      "secrets": [
        { "name": "DATABASE_URL", "valueFrom": "arn:aws:secretsmanager:eu-west-2:ACCOUNT:secret:conductor/database-url" },
        { "name": "REDIS_URL", "valueFrom": "arn:aws:secretsmanager:eu-west-2:ACCOUNT:secret:conductor/redis-url" }
      ]
    },
    {
      "name": "conductor-langfuse",
      "image": "langfuse/langfuse:2",
      "portMappings": [{ "containerPort": 3000 }],
      "secrets": [
        { "name": "DATABASE_URL", "valueFrom": "arn:aws:secretsmanager:eu-west-2:ACCOUNT:secret:conductor/langfuse-database-url" },
        { "name": "NEXTAUTH_SECRET", "valueFrom": "arn:aws:secretsmanager:eu-west-2:ACCOUNT:secret:conductor/langfuse-nextauth-secret" },
        { "name": "SALT", "valueFrom": "arn:aws:secretsmanager:eu-west-2:ACCOUNT:secret:conductor/langfuse-salt" }
      ],
      "environment": [
        { "name": "NEXTAUTH_URL", "value": "https://langfuse.internal.example.com" },
        { "name": "TELEMETRY_ENABLED", "value": "false" }
      ]
    }
  ]
}
```

## AWS Secrets Manager Keys

Create these secrets in Secrets Manager (plain text, not JSON):

| Secret Name | Value |
|---|---|
| `conductor/database-url` | `postgresql+asyncpg://conductor:PASSWORD@my-rds:5432/conductor` |
| `conductor/redis-url` | `redis://:PASSWORD@my-elasticache:6379/0` |
| `conductor/langfuse-database-url` | `postgresql://conductor:PASSWORD@my-rds:5432/langfuse` |
| `conductor/langfuse-nextauth-secret` | (random string) |
| `conductor/langfuse-salt` | (random string) |

## Notes

- ECS task role should have Bedrock access (`bedrock:InvokeModel`) — no need for AWS key env vars.
- `DATABASE_URL` and `REDIS_URL` always take highest priority, overriding anything in YAML config.
- The backend and Langfuse share the same RDS instance but use different databases (`conductor` vs `langfuse`).
