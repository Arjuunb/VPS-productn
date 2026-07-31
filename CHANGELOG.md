# Changelog

## v1.0.0-production — 2026-07-31

- Established the production Docker Compose topology with Nginx reverse proxy,
  durable SQLite volume, restart policies, bounded logs, and service health checks.
- Hardened the runtime container to run as a non-root user and removed the
  control credential from browser-rendered configuration.
- Added production environment, deployment, audit, validation, and runtime
  documentation for Ubuntu 24.04 / Hostinger VPS deployments.
- Removed Render and Vercel deployment configuration and the serverless-only API.
- Fixed the Dashboard TypeScript build failures.
