# Tasks

## 1. Tenant connection and model catalog

- [x] 1.1 Add the next forward MySQL migration for `model_connections`, `model_catalog_items`, `model_test_records`, and ModelRoute connection/model references; preserve legacy reads and provide a rollback boundary. Verify the migration runner and inspect every new/changed table and column comment through `information_schema.TABLES` and `information_schema.COLUMNS`.
- [x] 1.2 Add tenant-scoped domain objects, repository ports, MySQL repositories, and ModelRoute backfill from existing provider/endpoint/model/SecretRef data. Verify same-tenant create/read/backfill and cross-tenant rejection with repository and MySQL integration tests.
- [x] 1.3 Add `ModelCatalogProvider`, `ModelAuthorizationProvider`, and an environment-backed `ModelSecretProvider`; implement authenticated OpenAI-compatible model discovery plus manual model entry. Verify multiple model IDs share one connection, unsupported catalog discovery permits manual entry, and credentials never appear in repr/JSON/log assertions.
- [x] 1.4 Add connection and catalog API endpoints for tenant list/create/version update, model list/sync, and selectable model routes. Verify API schema compatibility, tenant filtering, missing credential errors, and no secret value in responses.

## 2. Model routes and Worker invocation

- [x] 2.1 Update ModelRoute create/read/disable flows to reference a same-tenant connection and model ID, with new versions for configuration changes. Verify employee-version binding rejects disabled, missing, or cross-tenant routes.
- [x] 2.2 Extend RunSnapshot and persistence to pin route/connection versions, provider, model ID, protocol, endpoint, parameters, pricing reference, and SecretRef metadata. Verify a queued Run keeps its original snapshot after route or connection edits and contains no credential value.
- [x] 2.3 Pass the pinned model execution configuration through RuntimeRequest and HarnessRuntimeAdapter into the DeepSeek Harness custom OpenAI-compatible provider. Resolve SecretRef inside Worker and normalize upstream usage/request IDs when available; verify exact local route/model selection, secret redaction, and `ESTIMATED` fallback with adapter contract tests.
- [x] 2.4 Apply platform model authorization before provider dispatch and filter employee model selectors to active routes available to the current tenant/identity. Verify denied or revoked routes fail before any provider request.

## 3. Connection and model tests

- [x] 3.1 Add persistent asynchronous model test jobs and result states `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, and `UNKNOWN`, processed by the existing Run Worker process. Verify legal state transitions, tenant isolation, fencing, bounded output, and no automatic retry for uncertain outcomes.
- [x] 3.2 Implement connection tests using authenticated catalog discovery when supported and model tests using one explicit, synthetic, no-tools Harness request. Verify HTTP 401, unsupported catalog, model-not-found, timeout, provider error, request reference, usage state, and redaction paths with deterministic fake providers.
- [x] 3.3 Add UI confirmation before a real model test, disclose that the request may incur provider cost, and show result status/latency/usage/error category. Verify cancel/close behavior and that the prototype does not trigger a request until confirmed.

## 4. Model management UI and employee binding

- [x] 4.1 Add a tenant-scoped Model Management page for connections, multiple models per connection, ModelRoutes, authorization source, and empty/loading/error states. Verify list, detail, and tenant switching with frontend component tests.
- [x] 4.2 Add create/edit connection flows for provider protocol, Base URL, and SecretRef; add model discovery/manual entry, route creation, and explicit connection/model tests. Verify form validation, endpoint guidance for Docker Worker, and all test states in frontend tests.
- [x] 4.3 Update the employee-version editor to select only active, authorized routes and display route alias plus model ID. Update `docs/原型/模型管理.html` and verify the selector and prototype describe the same tenant/model/test behavior.

## 5. Documentation and end-to-end validation

- [x] 5.1 Add `docs/模型管理与模型接入.md` with architecture and sequence diagrams, connection/catalog/route/test data model, API and state behavior, tenant/secret boundaries, verified/unverified items, and exact validation commands. Verify every documented endpoint and status matches the implementation.
- [x] 5.2 Run the MySQL migration/tenant/comment integration tests, backend model-route and Worker tests, frontend tests/build, and `openspec validate tenant-model-management --strict`; record commands and results in the Chinese validation document.
- [x] 5.3 With a valid local SecretRef configured, execute one confirmed model test and one Run from the Docker Worker using `host.docker.internal:63072`; verify the selected model ID, response, trace/usage status, and absence of API key in storage/logs. Leave Dadi/new-api containers stopped and record gateway authorization as deferred.
