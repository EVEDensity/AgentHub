# Deployment Assets

`deploy/` contains local and production deployment support: Compose files,
images, observability configuration, and operational defaults.

Every deployment document must state prerequisites, exposed ports, secrets,
health checks, persistence, rollback, and the verification command. Local
Community deployment should not require the full enterprise service topology.

Do not use deployment configuration to silently change domain semantics. A
feature unavailable in a deployment must fail explicitly and be observable.
