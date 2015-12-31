# Widelands's BunnyBot

This is the merge bot for Widelands. It is meant to make daily development
easier. This includes automating rote task like merging branches, but also
giving us access to continuous integration on Launchpad through mirroring certain branches to
GitHub.

## Setting up

Bunnybot does most of its work through running bzr and git directly.
Consequently, it needs bzr and git configured for its credentials. This is
usually best done by running it as a separate user and manually setting up git
and bzr.

TODO(sirver): Add a config.json sample.
