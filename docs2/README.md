# Recotem 2.0 Documentation

## Getting started

- [Quickstart](quickstart.md) — install, write a recipe, train, curl `/predict` in 5 minutes

## Reference

- [Recipe reference](recipe-reference.md) — every field, type, default, validation rule

## Data sources

- [CSV / Parquet](data-sources/csv.md) — local files, fsspec paths (S3, GCS), dtype overrides
- [BigQuery](data-sources/bigquery.md) — ADC auth, GA4 query patterns, parameter binding

## Deployment

- [Docker](deployment/docker.md) — `docker-compose.example.yaml` walkthrough
- [Kubernetes](deployment/k8s.md) — CronJob + Deployment, Helm chart values
- [Cron / systemd](deployment/cron.md) — plain Linux cron and systemd timers

## Operations

- [Operations](operations.md) — key rotation, API key rotation, artifact recovery, sizing, SLOs, troubleshooting
- [Security](security.md) — trust boundaries, threat model, IAM scopes, secrets handling

## Extending

- [Plugin authoring](plugin-authoring.md) — writing a custom DataSource plugin
