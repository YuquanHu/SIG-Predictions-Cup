# SIG Predictions Cup trading bot

Read `plan.md` before any work. It defines the stages; build one stage at a time and stop after each so the user can test. `DRY_RUN` defaults to True and no orders are placed until the live stage.

## Code quality review (standing instruction)

After writing or changing code, and before reporting it as done, review the changes against
`claude skills/thermo-nuclear-code-quality-review/SKILL.md`. Read that file in full each session.
Fix the structural problems it flags (spaghetti branching, needless wrappers, logic in the wrong
module, duplicated helpers, vague data shapes), keeping behavior the same, and re-run the affected
script. In the final message, say briefly what the review found and changed.
