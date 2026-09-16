# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial Python project structure.
- `SubprocessRunner` runs commands with `subprocess`: `run` captures the
  output of short commands, `stream` passes each line of a long-running agent
  on as it is written, reading stdout and stderr together.
- `Client.launch_agent` and `Client.launch_agent_in_workspace` run a harness
  by name and report its outcome; what the agent writes goes to the
  `vuterm.agent` logger, one record per message.
- `Client.init`, `Client.prepare_new_workspace` and the workspace pool:
  numbered `wsNNNN` workspaces with a detached worktree per repository,
  reserved one agent at a time, reset on reuse, capped at
  `max_workspace_count`, and safe to reserve from several threads. A reset
  adds a worktree back when its folder is missing or is no longer a worktree,
  and a failed creation leaves nothing behind in the clones.
- The `git` and `gh` wrappers, `Git` and `GitHub`, raising `CommandError`
  with the status and stderr of a failed command.

[Unreleased]: https://github.com/open-shipyard/vuterm/commits/main
