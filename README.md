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

You need a remote called 'github' which points to
the repository you want to use as the mirror source on GitHub. For Widelands
that would be http://github.com/widelands/widelands.

You need to register SSH keys for the GitHub and Launchpad users.
TODO(sirver): Add a config.json sample.

You also need git-remote-bzr in your path. https://github.com/felipec/git-remote-bzr

Also you need pyformat and clang-format for merging.

To set up data/git_repo, just clone. then

~~~
git remote add bzr_origin bzr::/home/bunnybot/bunnybot/data/bzr_repo
~~~

For the bzr_repo:

~~~
mkdir data/bzr_repo && cd data/bzr_repo
bzr init-repository
~~~
