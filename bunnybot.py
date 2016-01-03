#!/usr/bin/env python
# encoding: utf-8

from glob import glob
from launchpadlib.launchpad import Launchpad
import argparse
import json
import os
import re
import subprocess
import urllib2

# NOCOM(#sirver): we need to do this to be clean
#  find . -name '*.BASE' -print0 | xargs -0 rm -rv
#  find . -name '*.OTHER' -print0 | xargs -0 rm -rv
#  find . -name '*.THIS' -print0 | xargs -0 rm -rv
#  find . -name '*.moved' -print0 | xargs -0 rm -rv
#  find . -name '*.pyc' -print0 | xargs -0 rm -rv
#  find . -name '*~?~' -print0 | xargs -0 rm -rv
#  find . -name '.DS_Store' -print0 | xargs -0 rm -rv

GREETING_LINE = "Hi, I am bunnybot (https://github.com/widelands/bunnybot)."


def build_greeting(branch):
    lines = [
        GREETING_LINE,
        "",
        "I am keeping the source branch lp:%s mirrored to https://github.com/widelands/widelands/tree/%s"
        % (branch.name, branch.slug),
        "",
        "You can give me commands by starting a line with @bunnybot <command>. I understand: ",
        " merge: Merges the source branch into the target branch, closing the pull request."
    ]
    return "\n".join(lines)


def build_travis_update(branch):
    return "Travis build %s has changed state to: %s. Details: %s." % (
        branch.travis_state['number'], branch.travis_state['state'],
        'https://travis-ci.org/widelands/widelands/builds/%s' %
        branch.travis_state['id'])


class Branch(object):
    def __init__(self, name, bzr_repo):
        """Create a target branch from a unique_name like
        '~widelands-dev/widelands/trunk."""
        self._name = name
        self._bzr_repo = bzr_repo
        self._revno = None
        self._travis_state = {}

    @property
    def name(self):
        return self._name

    @property
    def slug(self):
        return re.sub(r"[^A-Za-z0-9]", "_", self._name)

    def branch(self):
        run_bzr("branch lp:%s %s" % (self.name, self._path))
        self._revon = self._get_revno()

    def pull(self):
        run_bzr("pull", cwd=self._path)
        self._revon = self._get_revno()

    def update(self):
        """Pulls or branches the branch if it does not yet exist."""
        if self.is_branched():
            return self.pull()
        return self.branch()

    def push(self):
        run_bzr("push :parent", cwd=self._path)

    def _get_revno(self):
        return int(subprocess.check_output(["bzr", "revno"], cwd=self._path))

    @property
    def revno(self):
        assert self.is_branched()
        if self._revno is None:
            self._revno = self._get_revno()
        return self._revno

    @property
    def _path(self):
        return os.path.join(self._bzr_repo, self.slug)

    def is_branched(self):
        """Returns True if the branch has been branched already in the shared
        repo."""
        return os.path.isdir(self._path)

    def merge_source(self, source):
        print("-> Merging %s into %s" % (source.name, self.name))
        source_path = source._path
        subprocess.check_call(
            ["bzr", "merge", os.path.relpath(source_path, self._path)],
            cwd=self._path)
        subprocess.check_call(
            ["bzr", "commit", "-m", "Merged lp:%s" % source.name],
            cwd=self._path)
        self.push()

    def update_git(self, git_repo):
        """Creates or updates a branch in git branch named 'slug' that track
        the bzr branch in the bzr_repo."""
        if self.slug not in git_remotes(git_repo):
            subprocess.check_call(
                ["git", "remote", "add", self.slug,
                 "bzr::" + os.path.relpath(self._path, git_repo)],
                cwd=git_repo)
        subprocess.check_call(["git", "fetch", self.slug], cwd=git_repo)
        if self.slug not in git_branches(git_repo):
            subprocess.check_call(
                ["git", "branch", "--track", self.slug, "%s/master" % self.slug
                 ],
                cwd=git_repo)
        git_checkout_branch(git_repo, self.slug)
        subprocess.check_call(["git", "pull"], cwd=git_repo)
        subprocess.check_call(
            ["git", "push", "github", self.slug],
            cwd=git_repo)

    @property
    def travis_state(self):
        return self._travis_state

    def update_travis_state(self):
        """Checks if there is a travis state available for this branch."""
        url = "https://api.travis-ci.org/repos/widelands/widelands/branches/%s" % self.slug
        try:
            data = urllib2.urlopen(url).read()
            d = json.loads(data)
            self._travis_state = d.get("branch", None)
        except urllib2.HTTPError as error:
            if error.code != 404:
                raise error

    def serialize(self):
        state = {}
        if self._travis_state:
            state['travis_state'] = {"state": self._travis_state['state']}
        return state


def git_remotes(git_repo):
    return set(line.strip() for line in subprocess.check_output(
        ["git", "remote"],
        cwd=git_repo).splitlines())


def git_branches(git_repo):
    lines = subprocess.check_output(
        ["git", "branch"],
        cwd=git_repo).splitlines()
    branches = set()
    for line in lines:
        if line.startswith("*"):
            line = line[2:]
        branches.add(line.strip())
    return branches


def git_delete_remote(git_repo, branch_name):
    subprocess.check_call(
        ["git", "remote", "remove", branch_name],
        cwd=git_repo)


def git_delete_remote_branch(git_repo, branch_name):
    subprocess.check_call(
        ["git", "push", "github", ":" + branch_name],
        cwd=git_repo)


def git_delete_local_branch(git_repo, branch_name):
    if branch_name == "master":
        raise RuntimeError("Cannot delete master branch.")

    git_checkout_branch(git_repo, "master")
    subprocess.check_call(
        ["git", "branch", "-D", branch_name],
        cwd=git_repo)


def run_bzr(args, cwd=None):
    print("-> bzr %s%s" % (args, "" if cwd is None else " [%s]" % cwd))
    args = args.split(" ")
    subprocess.check_call(["bzr"] + args, cwd=cwd)


def get_merge_requests(project, bzr_repo):
    merge_proposals = [m for m in project.getMergeProposals()
                       if m.queue_status != u"Work in progress"]

    branches = {}
    for proposal in merge_proposals:
        for branch in (proposal.target_branch, proposal.source_branch):
            branches[branch.unique_name] = Branch(branch.unique_name, bzr_repo)
    return [MergeRequest(m, branches) for m in merge_proposals], branches


def read_config():
    p = argparse.ArgumentParser(
        description="Mergebot for the Widelands project")

    p.add_argument("--config",
                   type=str,
                   default="data/config.json",
                   help="The configuration file for the bot.")
    args = p.parse_args()
    return json.load(open(args.config, "r"))


class MergeRequest(object):
    def __init__(self, lp_object, branches):
        self.source_branch = branches[lp_object.source_branch.unique_name]
        self.target_branch = branches[lp_object.target_branch.unique_name]
        self._comments = [c.message_body for c in lp_object.all_comments]
        self._lp_object = lp_object

    def _merge(self):
        self.source_branch.update()
        self.target_branch.update()
        self.target_branch.merge_source(self.source_branch)

    def serialize(self):
        d = {}
        d['source_branch'] = self.source_branch.name
        d['target_branch'] = self.target_branch.name
        d['num_comments'] = len(self._comments)
        return d

    def new_comments(self, old_state):
        """Returns all new comments since this script ran the last time."""
        # NOCOM(#sirver): rename to merge proposals everywhere
        for proposal in old_state.get('merge_requests', []):
            if (proposal['target_branch'] == self.target_branch.name and
                proposal['source_branch'] == self.source_branch.name):
                return self._comments[proposal['num_comments']:]
        return self._comments

    def handle(self, old_state, git_repo):
        self.source_branch.update()
        self.source_branch.update_travis_state()
        self.source_branch.update_git(git_repo)

        # Post the greeting if it was not yet posted.
        found_greeting = False
        for c in self._comments:
            if GREETING_LINE in c:
                found_greeting = True
                break
        if not found_greeting:
            self._lp_object.createComment(
                subject="Bunnybot says...",
                content=build_greeting(self.source_branch))

        # Check for changes to the travis state, given we know anything about
        # the travis state.
        current_travis_state = self.source_branch.travis_state.get(
                "state", None)
        if current_travis_state is not None:
            old_travis_state = old_state['branches'].get(
                self.source_branch.name, {}).get(
                    "travis_state", {}).get("state", None)
            if old_travis_state != current_travis_state:
                self._lp_object.createComment(
                    subject="Bunnybot says...",
                    content=build_travis_update(self.source_branch))

        for c in self.new_comments(old_state):
            if re.search("^@bunnybot.*merge", c, re.MULTILINE) is not None:
                # NOCOM(#sirver): this should report errors
                self._merge()


def dump_state(json_file, merge_requests, branches):
    state = {}
    state['merge_requests'] = [m.serialize() for m in merge_requests]
    branch_state = {
        branch.name: branch.serialize()
        for branch in branches.values()
    }
    state['branches'] = branch_state
    with open(json_file, "w") as json_file:
        json.dump(state,
                  json_file,
                  sort_keys=True,
                  indent=4,
                  separators=(',', ': '))


def load_state(json_file):
    if not os.path.exists(json_file):
        return {}
    with open(json_file, "r") as json_file:
        return json.load(json_file)


def git_checkout_branch(git_repo, branch_name):
    subprocess.check_call(["git", "checkout", branch_name], cwd=git_repo)


def update_git_master(trunk_name, bzr_repo, git_repo):
    trunk = Branch(trunk_name, bzr_repo)
    trunk.update()
    trunk.update_git(git_repo)

    # Merge trunk into master and push to github.
    git_checkout_branch(git_repo, "master")
    subprocess.check_call(
        ["git", "merge", "--ff-only", trunk.slug],
        cwd=git_repo)
    subprocess.check_call(["git", "push", "github", "master"], cwd=git_repo)


def delete_unmentioned_branches(branches, bzr_repo, git_repo):
    branches_slugs = set(b.slug for b in branches.values())
    checked_out_bzr_branches = set(
            os.path.basename(d) for d in glob(os.path.join(bzr_repo, "*")) if
            os.path.isdir(d))

    for slug in (checked_out_bzr_branches - branches_slugs):
        print "Deleting %s which is not mentioned anymore." % slug
        # Ignore errors - most likely some branches where not really there.
        try:
            git_delete_remote(git_repo, slug)
        except subprocess.CalledProcessError as error:
            print(error)

        try:
            git_delete_remote_branch(git_repo, slug)
        except subprocess.CalledProcessError as error:
            print(error)

        try:
            git_delete_local_branch(git_repo, slug)
        except subprocess.CalledProcessError as error:
            print(error)

        # shutil.rmtree chokes on some of our filenames.
        subprocess.check_call(["rm", "-rf", os.path.join(bzr_repo, slug)])


def main():
    config = read_config()

    old_state = load_state(config["state_file"])

    if not os.path.isdir(config["bzr_repo"]):
        run_bzr("init-repo %s" % config["bzr_repo"])
    lp = Launchpad.login_with("wideland's bunnybot",
                              "production",
                              credentials_file=config["launchpad_credentials"])
    project = lp.projects["widelands"]
    merge_requests, branches = get_merge_requests(project, config["bzr_repo"])
    for merge_request in merge_requests:
        merge_request.handle(old_state, config["git_repo"])

    dump_state(config["state_file"], merge_requests, branches)

    update_git_master(config["master_mirrors"], config["bzr_repo"],
                      config["git_repo"])
    delete_unmentioned_branches(
            branches, config["bzr_repo"], config["git_repo"])
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
