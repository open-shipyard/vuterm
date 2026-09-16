# vuterm

A common CLI to manage any terminal based code agent

## Installation

```sh
pip install vuterm
```

## Usage

Agents run headless, so their CLI must be installed and signed in: `claude`
for Claude Code, `opencode` for OpenCode. Repositories are cloned and
pull requests opened with `git` and `gh`, as the `gh` user.

```python
import logging

import vuterm as vt

logging.basicConfig(level=logging.INFO)  # what the agent does, as it runs

client = vt.Client(working_dir="/some/path", repositories=["Org/repo"])

# In the current folder.
result = client.launch_agent(harness="claude", task="update the README")
print(result.success, result.response)  # the response is the agent's final message

# In a workspace with a fresh worktree of each repository, one agent at a time.
client.init()
result = client.launch_agent_in_workspace(harness="claude", task="update the README")
```

See [docs/specs](docs/specs) for how workspaces are created, reused and
reserved.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
