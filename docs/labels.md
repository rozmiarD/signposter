# Label System (Skeleton)

**Status:** Example definitions only.

Labels are the primary signaling mechanism between GitHub and Signposter.

## Workflow Labels (Bootstrap Phase)

These labels were created as part of initial GitHub preparation.
They use neutral terminology and are intended to support future dispatcher logic.

### Phase Labels
- `phase:plan`
- `phase:build`
- `phase:review`
- `phase:merge`

### State Labels
- `state:ready`
- `state:active`
- `state:blocked`
- `state:failed`
- `state:done`

### Risk Labels
- `risk:low`
- `risk:medium`
- `risk:high`

### Role Labels
- `role:planner`
- `role:worker`
- `role:reviewer`
- `role:gatekeeper`

### Area Labels
- `area:docs`
- `area:tests`
- `area:core`
- `area:github`
- `area:scheduler`
- `area:dispatcher`
- `area:runner`
- `area:config`
- `area:ci`

### Gate Labels
- `gate:ci`
- `gate:review`
- `gate:human`

## Legacy / Example Labels

- **Lifecycle / Phase labels**: `needs-review`, `ready-for-dispatch`, `in-progress`, `blocked`
- **Area labels** (legacy examples): `area/backend`, `area/infrastructure`, `area/documentation`
- **Risk / Gate labels** (legacy examples): `high-risk`, `requires-infra-review`

## Rules (to be implemented later)

- Labels drive routing decisions.
- Certain label combinations can trigger specific gates.
- Phase labels should be kept in sync with internal state machine.

See `configs/labels.example.yaml` for the current structural definition.

Future work must keep label semantics clear and non-overloaded.