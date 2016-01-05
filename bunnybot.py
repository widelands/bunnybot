#!/usr/bin/env python
# encoding: utf-8

from glob import glob
from launchpadlib.launchpad import Launchpad
import argparse
import json
import os
import pid
import re
import subprocess
import time
import urllib2

GREETING_LINE = "Hi, I am bunnybot (https://github.com/widelands/bunnybot)."


class ProcessFailed(Exception):
    """subprocess.CalledProcessError does not satisfy our needs, so we roll our
    own class here."""

    def __init__(self, command, stdout):
        self.command = command
        self.stdout = stdout

    def __str__(self):
        return "Running '%s' failed. Output:\n\n%s" % (
                self.command, self.stdout)


def retry_on_dns_failure(function):
    """
    Git push and pull seem to transiently fail on DNS failures for github.com
    (of all things). We hack around this by sleeping and retrying.
    """
    while True:
        try:
            function()
            break
        except ProcessFailed as e:
            if "Name or service not known" not in e.stdout:
                raise
            time.sleep(5)


def run_command(command, cwd=None, verbose=True):
    if verbose:
        print("-> %s%s" % (
            " ".join(command), "" if cwd is None else " [%s]" % cwd))
    process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=None,
            stderr=subprocess.STDOUT,
            stdout=subprocess.PIPE,
            )

    out, err = process.communicate()
    assert err is None

    if verbose:
        for line in out.splitlines():
            print "  %s" % line

    if process.returncode != 0:
        raise ProcessFailed(" ".join(command), out)
    return out


def build_greeting(branch):
    lines = [
        GREETING_LINE,
        "",
        "I am keeping the source branch lp:%s mirrored to https://github.com/widelands/widelands/tree/%s"
        % (branch.name, branch.slug),
        "",
        "You can give me commands by starting a line with @bunnybot <command>. I understand: ",
        " merge: Merges the source branch into the target branch, closing the "
        "merge proposal. I will use the proposed commit message if it is set."
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
        run_command(["bzr", "branch", "lp:%s" % self.name, self._path])
        self._revon = self._get_revno()

    def pull(self):
        # Clear out any unwanted old state.
        run_command(["bzr", "revert"], cwd=self._path)
        retry_on_dns_failure(
                lambda: run_command(["bzr", "pull"], cwd=self._path))
        self._revon = self._get_revno()

    def update(self):
        """Pulls or branches the branch if it does not yet exist."""
        if self.is_branched():
            return self.pull()
        return self.branch()

    def push(self):
        retry_on_dns_failure(
                lambda: run_command(["bzr", "push", ":parent"], cwd=self._path))

    def _get_revno(self):
        return int(run_command(["bzr", "revno"], cwd=self._path, verbose=False))

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

    def merge_source(self, source, commit_message):
        source_path = source._path
        run_command(
            ["bzr", "merge", os.path.relpath(source_path, self._path)],
            cwd=self._path)

        full_commit_message = "Merged lp:%s" % source.name
        if commit_message is not None:
            full_commit_message += ":\n"
            full_commit_message += commit_message
        else:
            full_commit_message += "."
        run_command(["bzr", "commit", "-m", full_commit_message], cwd=self._path)
        self.push()

    def update_git(self, git_repo):
        """Creates or updates a branch in git branch named 'slug' that track
        the bzr branch in the bzr_repo."""
        if self.slug not in git_remotes(git_repo):
            run_command(
                ["git", "remote", "add", self.slug,
                 "bzr::" + os.path.relpath(self._path, git_repo)],
                cwd=git_repo)
        run_command(["git", "fetch", self.slug], cwd=git_repo)
        if self.slug not in git_branches(git_repo):
            run_command(
                ["git", "branch", "--track", self.slug, "%s/master" % self.slug],
                cwd=git_repo)
        git_checkout_branch(git_repo, self.slug)
        retry_on_dns_failure(lambda: run_command(["git", "pull"], cwd=git_repo))
        retry_on_dns_failure(lambda: run_command(
            ["git", "push", "github", self.slug], cwd=git_repo))

    @property
    def travis_state(self):
        return self._travis_state

    def update_travis_state(self, old_travis_state):
        """Checks if there is a travis state available for this branch."""
        url = "https://api.travis-ci.org/repos/widelands/widelands/branches/%s" % self.slug
        try:
            data = urllib2.urlopen(url).read()
            d = json.loads(data)
            branch = d.get("branch", None)
            self._travis_state = {
                "state": branch["state"],
                "number": branch["number"],
                "id": branch["id"],
            }

            # No reason to report transient states
            if self._travis_state["state"] not in ("passed", "failed", "errored", "canceled"):
                self._travis_state["state"] = old_travis_state
        except urllib2.HTTPError as error:
            if error.code != 404:
                raise error

    def serialize(self):
        state = {}
        if self._travis_state:
            state['travis_state'] = {"state": self._travis_state['state']}
        return state


def git_remotes(git_repo):
    return set(line.strip() for line in run_command(
        ["git", "remote"],
        cwd=git_repo, verbose=False).splitlines())


def git_branches(git_repo):
    lines = run_command(
        ["git", "branch"],
        cwd=git_repo, verbose=False).splitlines()
    branches = set()
    for line in lines:
        if line.startswith("*"):
            line = line[2:]
        branches.add(line.strip())
    return branches


def git_delete_remote(git_repo, branch_name):
    run_command(
        ["git", "remote", "remove", branch_name],
        cwd=git_repo)


def git_delete_remote_branch(git_repo, branch_name):
    run_command(
        ["git", "push", "github", ":" + branch_name],
        cwd=git_repo)


def git_delete_local_branch(git_repo, branch_name):
    if branch_name == "master":
        raise RuntimeError("Cannot delete master branch.")

    git_checkout_branch(git_repo, "master")
    run_command(
        ["git", "branch", "-D", branch_name],
        cwd=git_repo)


def get_merge_proposals(project, bzr_repo):
    merge_proposals = [m for m in project.getMergeProposals()
                       if m.queue_status != u"Work in progress"]

    branches = {}
    for proposal in merge_proposals:
        for branch in (proposal.target_branch, proposal.source_branch):
            branches[branch.unique_name] = Branch(branch.unique_name, bzr_repo)
    return [MergeProposal(m, branches) for m in merge_proposals], branches


def read_config():
    p = argparse.ArgumentParser(
        description="Mergebot for the Widelands project")

    p.add_argument("--config",
                   type=str,
                   default="data/config.json",
                   help="The configuration file for the bot.")
    args = p.parse_args()
    return json.load(open(args.config, "r"))


class MergeProposal(object):
    def __init__(self, lp_object, branches):
        self.source_branch = branches[lp_object.source_branch.unique_name]
        self.target_branch = branches[lp_object.target_branch.unique_name]
        self._comments = [c.message_body for c in lp_object.all_comments]
        self._lp_object = lp_object

    def _merge(self):
        self.source_branch.update()
        self.target_branch.update()
        self.target_branch.merge_source(self.source_branch, self._lp_object.commit_message)

    def serialize(self):
        d = {}
        d['source_branch'] = self.source_branch.name
        d['target_branch'] = self.target_branch.name
        d['num_comments'] = len(self._comments)
        return d

    def new_comments(self, old_state):
        """Returns all new comments since this script ran the last time."""
        for proposal in old_state.get('merge_proposals', []):
            if (proposal['target_branch'] == self.target_branch.name and
                proposal['source_branch'] == self.source_branch.name):
                return self._comments[proposal['num_comments']:]
        return self._comments

    def handle(self, old_state, git_repo):
        self.source_branch.update()

        old_travis_state = old_state['branches'].get(
            self.source_branch.name, {}).get(
                "travis_state", {}).get("state", None)

        self.source_branch.update_travis_state(old_travis_state)
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
            if old_travis_state != current_travis_state:
                self._lp_object.createComment(
                    subject="Bunnybot says...",
                    content=build_travis_update(self.source_branch))

        for c in self.new_comments(old_state):
            if re.search("^@bunnybot.*merge", c, re.MULTILINE) is not None:
                self._merge()

    def report_exception(self, exception):
        lines = [
            "Bunnybot encountered an error while working on this merge proposal:",
            "",
            str(exception),
        ]
        self._lp_object.createComment(
                subject="Bunnybot says...",
                content="\n".join(lines))


def dump_state(json_file, merge_proposals, branches):
    state = {}
    state['merge_proposals'] = [m.serialize() for m in merge_proposals]
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
    run_command(["git", "checkout", branch_name], cwd=git_repo)


def update_git_master(trunk_name, bzr_repo, git_repo):
    trunk = Branch(trunk_name, bzr_repo)
    trunk.update()
    trunk.update_git(git_repo)

    # Merge trunk into master and push to github.
    git_checkout_branch(git_repo, "master")
    run_command(
        ["git", "merge", "--ff-only", trunk.slug],
        cwd=git_repo)
    run_command(["git", "push", "github", "master"], cwd=git_repo)


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
        except ProcessFailed as error:
            print(error)

        try:
            git_delete_remote_branch(git_repo, slug)
        except ProcessFailed as error:
            print(error)

        try:
            git_delete_local_branch(git_repo, slug)
        except ProcessFailed as error:
            print(error)

        # shutil.rmtree chokes on some of our filenames.
        run_command(["rm", "-rf", os.path.join(bzr_repo, slug)])


def main():
    config = read_config()

    old_state = load_state(config["state_file"])

    if not os.path.isdir(config["bzr_repo"]):
        run_command(["bzr", "init-repo", config["bzr_repo"]])
    lp = Launchpad.login_with("wideland's bunnybot",
                              "production",
                              credentials_file=config["launchpad_credentials"])
    project = lp.projects["widelands"]
    merge_proposals, branches = get_merge_proposals(
            project, config["bzr_repo"])
    for merge_proposal in merge_proposals:
        print "===> Working on %s -> %s" % (
                merge_proposal.source_branch.name,
                merge_proposal.target_branch.name)
        try:
            merge_proposal.handle(old_state, config["git_repo"])
        except Exception as e:
            merge_proposal.report_exception(e)
        print "\n\n"

    dump_state(config["state_file"], merge_proposals, branches)

    update_git_master(config["master_mirrors"], config["bzr_repo"],
                      config["git_repo"])
    delete_unmentioned_branches(
            branches, config["bzr_repo"], config["git_repo"])
    return 0


if __name__ == '__main__':
    try:
        with pid.PidFile(piddir="."):
            main()
    except pid.PidFileAlreadyLockedError:
        print "PID file exists. Cowardly refusing to work."
        pass
