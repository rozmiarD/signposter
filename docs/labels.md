# Label System (Skeleton)

**Status:** Example definitions only.

Labels are the primary signaling mechanism between GitHub and Signposter.

## Categories

- **Lifecycle / Phase labels**: `needs-review`, `ready-for-dispatch`, `in-progress`, `blocked`
- **Area labels**: `area/backend`, `area/infrastructure`, `area/documentation`
- **Risk / Gate labels**: `high-risk`, `requires-infra-review`

## Rules (to be implemented later)

- Labels drive routing decisions.
- Certain label combinations can trigger specific gates.
- Phase labels should be kept in sync with internal state machine.

See `configs/labels.example.yaml` for the current structural definition.

Future work must keep label semantics clear and non-overloaded.