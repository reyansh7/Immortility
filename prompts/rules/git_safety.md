# Git safety

- Read-only git (`status`, `diff`, `log`, `show`) does not need confirmation.
- Mutating git (`add`, `commit`, `checkout`, `stash`, `fetch`, non-force `push`) needs user confirmation.
- Never `git reset --hard`, force-push, branch-delete, stash drop/clear, or `commit --amend` unless the user confirmed a destructive action.
- Never issue destructive git from the coding loop, planner, reviewer, or hooks.
- Do not use `git -C` / `--git-dir` to escape the allowed repo boundary.
