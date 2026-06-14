# Label System

**Status:** Implemented via GitHub workflow labels.

Signposter routes work using GitHub issue labels. Canonical structural
definitions live in `configs/labels.example.yaml`.

## Workflow state labels

- `state:ready`
- `state:active`
- `state:blocked`
- `state:failed`
- `state:done`
- `state:merged`

## Phase, risk, role, area, and gate labels

Planner, scheduler, lifecycle, and gate surfaces use the `phase:*`, `risk:*`,
`role:*`, `area:*`, and `gate:*` vocabulary described in `configs/labels.example.yaml`
and summarized in `docs/workflow.md`.

Legacy examples such as `needs-review` or `ready-for-dispatch` may still appear
on older issues. New roadmaps should prefer the `state:*` and structured label
vocabulary above.
