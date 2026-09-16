# Workspaces

A workspace is a folder at `vt_working_dir/workspaces/wsNNNN` holding one
worktree for each repository in `vt_working_dir/repos`. See
[core.md](core.md) for how workspaces are created and used.

## Reservation lifecycle

`launch_agent_in_workspace` reserves a workspace before launching the agent.
The reservation is released when the agent exits, whether it completed
successfully or errored.

A workspace is reserved while it is being created, and free once
`prepare_new_workspace` returns it: the next `launch_agent_in_workspace` may
take it, reset it and run an agent in it. Anything done in a free workspace
can be discarded at any time.

## Default branch

A repository's default branch is the one `origin/HEAD` points to, which
`git clone` sets:

    git symbolic-ref refs/remotes/origin/HEAD

It is never hardcoded as `main`.

## Creation

Worktrees are checked out with a detached HEAD at `origin/<default>`. Git does
not allow the same branch in two worktrees, so checking out the default branch
itself would conflict with the clone in `vt_working_dir/repos` and with other
workspaces.

For each repository, vt_cli fetches the remote and then adds the worktree:

    git -C vt_working_dir/repos/<repo> fetch origin
    git -C vt_working_dir/repos/<repo> worktree add --detach <workspace>/<repo> origin/<default>

## Reuse

After picking a free workspace and before launching the agent in it, vt_cli
makes sure the workspace is in the same condition as a newly created one.

For each worktree, vt_cli fetches the remote, so the agent starts from the
latest remote commit, and then restores it:

    git -C vt_working_dir/repos/<repo> fetch origin
    git reset --hard
    git clean -ffd
    git checkout --detach origin/<default>

`git clean -ffd` intentionally keeps ignored files, such as installed
dependencies, virtual environments and build output, so the next agent does not
have to reinstall them.

Branches created by previous agents are left in place.

## Workspace limit

When every workspace is reserved and `max_workspace_count` workspaces already
exist, `launch_agent_in_workspace` raises a custom vuterm exception.

## Reservation tracking

Reservations are tracked in an in-memory collection. They are not shared
between processes and do not survive a restart. This is an accepted
limitation.

## Concurrency

The client is synchronous and single-threaded. Running agents concurrently is
the user's responsibility, e.g. by calling the client from several threads.

Reservations must be thread safe: two threads never reserve the same
workspace, and concurrent calls never create more than `max_workspace_count`
workspaces.
