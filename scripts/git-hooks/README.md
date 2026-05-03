# Git hooks

Lightweight discipline for keeping internal references out of the public repo.

## Install

```sh
bash scripts/git-hooks/install.sh
```

Symlinks `scripts/git-hooks/pre-push` into `.git/hooks/pre-push`. One-time per clone; idempotent.

## What `pre-push` does

Scans the **net diff** of every push for internal references. Fails the push if any added line matches a banned pattern. Current banned patterns (see `pre-push` for the source-of-truth regex list, kept in sync with `CLAUDE.md` § *Public-vs-Local Branch Discipline*):

- Absolute paths: `/Users/<name>/`
- Tilde-home refs: `~/Projects/`, `~/.claude*`
- Cross-repo refs: `ai-box/conductor/`
- Internal track names: `conductor/tracks/<track-name>_<YYYYMMDD>/`
- Internal hostnames: `srv870083`, `tail89def3.ts.net`
- Vault env names: `AIOPS_VAULT_API_KEY`, `VAULT_API_KEY`
- Vault tier/bucket refs: `nawfal-{dev,public,self,vault,prod-*,eng-*}/L<N>`

The scan is on **net diff** (push range from remote to local), not per-commit — what reviewers will see in the GitHub UI is what's checked.

## Bypass

For the rare acknowledged case (e.g. importing pre-existing history whose diffs contain now-banned patterns, or shipping ARIS docs whose headers reference repo paths from before this discipline existed):

```sh
PERSISTENCE_OS_ALLOW_INTERNAL_REFS=1 git push ...
```

## Updating the banned-pattern list

Edit `BANNED_PATTERNS` in `pre-push`. Mirror the human-readable list in `CLAUDE.md` § *Public-vs-Local Branch Discipline*.
