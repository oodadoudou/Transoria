# AGENTS.md

Follow the durable workflow in `tasks/workflow.md`.

For every task, start with `git status --short --branch`, confirm whether the
worktree is clean or dirty, then read:

1. `CLAUDE.md`
2. `tasks/workflow.md`
3. `tasks/lessons.md`
4. `tasks/todo.md`
5. `tasks/project-current-handover.md`
6. Relevant active docs under `docs/`

Do not use `tasks/agent-lab-handover.md` unless the user explicitly says the
separate Agent Lab fork is in scope.

Keep implementation, tests, active docs, and release-note copy aligned with the
current code. Code and tests remain the source of truth when old notes or docs
disagree.
