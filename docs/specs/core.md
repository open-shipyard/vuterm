vuterm cli should provide Python utilities to run many code agents that work at the terminal, like Claude Code, Codex, OpenCode with an unified interface.

It should also allow to work with Github repo, branches, PR, worktrees.

Onboarding a new vendor cli should require only implementing a single Port



```python
import vuterm as vt


# works with Github repos Org/repo User/repo
vt_cli = vt.Cient(
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


vt_cli = vt.Cient(
    working_dir="/some/path",
    repositories=["repo1", "repo2"],
    max_workspace_count=100
)


# will run "claude -p" in the current folder
results = vt_cli.launch_agent(
    harness="claude",
    task="update the README"
)

# results should allow knowing if the agent completed normally or errored out



"""

this will search for an available workspace.
Workspace are reserved by vt_cli before launching an agent. Booked workspaces are tracked in an in-memory collection.
if all workspaces are occupied, vt_cli should create a new one, up to max_workspace_count
then it should cd into that workspace folder and start claude in it, so claude has access to one worktree for each repo in a single multi-repo session

"""

results = vt_cli.launch_agent_in_workspace(
    harness="claude",
    task="update the README"
)

```


Other tools

vt_cli should also wrap git and gh clients, and exposing Python functions for:
- clone a repo
- creating a local branch
- git reset --hard
- pushing to remote
- creating a PR
- deleting a local branch

