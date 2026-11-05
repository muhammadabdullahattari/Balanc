# ADR-017: Floci for Phase 9 pilot hosting

Status: Target accepted by explicit user decision. Runtime compatibility and deployment acceptance pending verification.

## Decision

Replace AWS as the Phase 9 pilot infrastructure target with the user's in-house Floci platform. The user states it is fully free. Retain Vercel for the frontend. Preserve FastAPI, PostgreSQL, owned authentication, encrypted evidence, scanning/OCR, financial invariants and the existing phase sequence.

Screenshots show a Floci local-cloud console advertising compute, registry, database, storage and other services. They do not prove workload execution, PostgreSQL semantics, persistence, networking or GitHub deployment compatibility. Verify those capabilities using disposable synthetic resources before choosing the deployment adapter. Never assume ECR, SSM, IAM/OIDC or AWS CLI semantics work unchanged.

Existing AWS deployment code is retained as a reference; the AWS deployment environment must remain unapproved for this pilot. Adapting workflows requires verified Floci endpoints, execution/authentication contracts and network reachability. No credentials or console-session identifiers belong in documentation.

Readiness, rollback, least privilege, private database, HTTPS/cookies/CSRF, persistent evidence and coordinated backup/restore remain release requirements. The future SRS production architecture is not qualified by this free pilot deployment.

## Verification

No Floci resources were provisioned and no Floci compatibility or uptime test was performed by this documentation amendment. Phase 9 starts with the capability checks in the implementation plan.
