# Backend Architecture

## Responsibility map

```text
backend/src/agent_platform/
├── api/              HTTP routes, dependency wiring, request/response DTOs
├── application/      use-case orchestration
├── domain/           tenant-safe business models and invariants
├── ports/            replaceable interfaces for identity, runtime, storage
├── adapters/         Fake, DeepSeek, Codex, model, channel implementations
├── infrastructure/  MySQL, object store, queues, secrets and configuration
└── workers/          asynchronous runs, delivery and recovery
```

`harness-engine/` remains an independent SDK and Runtime implementation. The
platform backend depends on engine-neutral ports and adapters; the SDK does not
import the platform backend.

## Dependency rules

```text
api -> application -> domain
application -> ports
adapters -> ports + domain DTOs
infrastructure -> ports
workers -> application + ports
```

- Domain code does not import FastAPI, database drivers, vendor SDKs or React.
- API routes do not call vendor Runtime objects directly.
- Runtime adapters translate vendor sessions, events and Usage to platform types.
- TenantContext is resolved at the request boundary and passed explicitly to application and repository methods.
- Fake adapters are deterministic and are used for contract tests and local development.

## First vertical slice

The initial slice exposes:

```text
GET /api/health
GET /api/v1/digital-employees
```

The employee list uses a local Fake IdentityProvider and returns only active
employees from the resolved tenant. The frontend uses a Vite development proxy
to call the backend without coupling the UI to a backend port.

## Frontend choice

The management console uses React + TypeScript + Vite, React Router,
TanStack Query and Ant Design. React owns page composition, TanStack Query owns
server state, and Ant Design supplies accessible management controls such as
tables, forms, cards and filters. The UI uses project-owned components and
styles; StaffDeck code and static assets are not imported.
