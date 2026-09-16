vuterm cli should provide Python utilities to run many code agents that work at the terminal, like Claude Code, Codex, OpenCode with an unified interface.

It should also allow to work with Github repo, branches, PR, worktrees.

Onboarding a new vendor cli should require only implementing a single Port



```python
import vuterm as vt


# works with Github repos Org/repo User/repo
vt_cli = vt.Client(
    working_dir="/some/path",
    repositories=["repo1", "repo2"],
)


"""
init will
fetch all the repos into vt_working_dir/repos

"""
vt_cli.init()


"""
this will create a new subfolder at vt_working_dir/workspaces, eg vt_working_dir/workspaces/ws0001

vt_working dir is the config received at client instantiation, "working_dir" parameter

create inside it a new worktree for each one of the repos at vt_working_dir/repos

return a Path to the new location

worspaces should follow incremental number sufixes as ws0001, ws0002
"""
ws_path = vt_cli.prepare_new_workspace()
```


```python
import vuterm as vt


vt_cli = vt.Client(
    working_dir="/some/path", repositories=["repo1", "repo2"], max_workspace_count=100
)


# will run "claude -p" in the current folder
results = vt_cli.launch_agent(harness="claude", task="update the README")

# results should allow knowing if the agent completed normally or errored out


"""

this will search for an available workspace.
Workspace are reserved by vt_cli before launching an agent. Booked workspaces are tracked in an in-memory collection.
if all workspaces are occupied, vt_cli should create a new one, up to max_workspace_count
then it should cd into that workspace folder and start claude in it, so claude has access to one worktree for each repo in a single multi-repo session

"""

results = vt_cli.launch_agent_in_workspace(harness="claude", task="update the README")
```


Other tools

vt_cli should also wrap git and gh clients, and exposing Python functions for:
- clone a repo
- creating a local branch
- git reset --hard
- pushing to remote
- creating a PR
- deleting a local branch


## Results

`launch_agent` and `launch_agent_in_workspace` return a result that reports
whether the agent completed successfully or errored, whether it was stopped
at its timeout, and its response: the agent's final message, or none if it
gave none. Anything else (exit code, cost, session id) is out of scope for now.

Each harness decides what the response is, since every CLI reports it its own
way: Claude Code's is the text of its `result` event; OpenCode sends no such
event, so its response is the text of its last step, not text written before
a tool call.


## Agent runs

Agents run headless and non-interactive, e.g. `claude -p`. After an agent is
launched and until it exits, vt_cli does not interact with it: no input on
stdin, no answers to prompts and no pseudo-terminal. vt_cli only reads its
output while it runs and its exit status when it finishes.

Both launch methods take a `timeout`, in seconds, three hours by default,
counted from when the agent starts: reserving and resetting a workspace does
not count. An agent still running then is killed together with its process
group, as at any other end of a run, and the result reports it as timed out,
not successful, instead of raising. A process the agent started in a session
or group of its own is not in that group and is not killed; on Windows only
the agent itself is. With a timeout of `None` an agent runs for as long as it
takes.


## Harness port

Harnesses such as Claude Code, Codex and OpenCode integrate through a single
port. Onboarding a new vendor CLI means implementing that port and nothing
else.

The port does no I/O. For each run, the harness creates a session that:

- provides the command line that runs the agent headless on the task
- turns each output line into a log message, or skips it
- turns the exit status and the lines it has seen into the result, response
  included

vt_cli runs the command and feeds each output line to the session as soon as
the agent writes it, so logs are available while the agent runs.

