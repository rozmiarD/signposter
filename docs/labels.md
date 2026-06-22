# Label System

**Status:** Implemented via GitHub workflow labels.

Signposter routes work using GitHub issue labels. Workflow state labels are
defined by the lifecycle model in `docs/state-machine.md` and
`docs/workflow.md`.

## Workflow state labels

- `state:ready`
- `state:active`
- `state:blocked`
- `state:failed`
- `state:done`
- `state:merged`

## Phase, risk, role, area, and gate labels

Planner, scheduler, lifecycle, and gate surfaces use the `phase:*`, `risk:*`,
`role:*`, `area:*`, and `gate:*` vocabulary in operator manifests and issue
labels. `configs/labels.example.yaml` shows older routing examples such as
`area:*`, `high-risk`, and `requires-infra-review`; it is not the canonical
source for `state:*` workflow labels.

Legacy examples such as `needs-review` or `ready-for-dispatch` may still appear
on older issues. New roadmaps should prefer the `state:*` and structured label
vocabulary above.
