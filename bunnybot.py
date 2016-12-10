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
import sys
import time
import urllib2

BZR_REPO_NAME = "bzr_origin"

def to_stdout(string):
    print(string.encode("ascii", "replace"))

class ProcessFailed(Exception):
    """subprocess.CalledProcessError does not satisfy our needs, so we roll our
    own class here."""

    def __init__(self, command, stdout):
        self.command = command
        self.stdout = stdout

    def __repr__(self):
        return "Running '%s' failed. Output:\n\n%s" % (
            self.command, self.stdout)


def retry_on_dns_failure(function):
    """
    Git push and pull seem to transiently fail on DNS failures for github.com
    (of all things). We hack around this by sleeping and retrying.
    """
    while True:
        try:
            return function()
            break
        except ProcessFailed as e:
            if "Name or service not known" not in e.stdout:
                raise
            to_stdout("Error: %r" % (e))
            time.sleep(5)


def read_url(url):
    while True:
        try:
            return urllib2.urlopen(url, timeout = 60.).read()
        except urllib2.URLError as error:
            if getattr(error, "code", 0) == 404:
                return
            if getattr(error.reason, "errno", 0) == -2:
                to_stdout("Transient error for %s: %s." % (url, error))
                time.sleep(5)
            else:
                raise


def run_command(command, cwd=None, verbose=True):
    if verbose:
        to_stdout("-> %s%s" % (
            " ".join(command), "" if cwd is None else " [%s]" % cwd))
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=None,
        stderr=subprocess.STDOUT,
        stdout=subprocess.PIPE)

    out, err = process.communicate()
    assert err is None

    if verbose:
        for line in out.splitlines():
            to_stdout("  %s" % line)

    if process.returncode != 0:
        raise ProcessFailed(" ".join(command), out)
    return out


def build_ci_update(branch):
    lines = [
        "Continuous integration builds have changed state:",
        "",
        "Travis build %s. State: %s. Details: %s." % (
            branch.travis_state['number'], branch.travis_state['state'],
            'https://travis-ci.org/widelands/widelands/builds/%s' %
            branch.travis_state['id']),
        "Appveyor build %s. State: %s. Details: %s." % (
            branch.appveyor_state['number'], branch.appveyor_state['state'],
            'https://ci.appveyor.com/project/widelands-dev/widelands/build/%s'
            % branch.appveyor_state['id']),
    ]
    return "\n".join(lines)


class Branch(object):
    def __init__(self, name, bzr_repo):
        """Create a target branch from a unique_name like
        '~widelands-dev/widelands/trunk."""
        self._name = name
        self._bzr_repo = bzr_repo
        self._revno = None
        self._appveyor_state = {}
        self._travis_state = {}

    @property
    def name(self):
        return self._name

    @property
    def slug(self):
        return re.sub(r"[^A-Za-z0-9]", "_", self._name)

    def branch(self):
        run_command(["bzr", "branch", "lp:%s" % self.name, self._path])

    def pull(self):
        now = self.revno()
        # Clear out any unwanted old state.
        run_command(["bzr", "revert"], cwd=self._path)
        retry_on_dns_failure(
                lambda: run_command(["bzr", "pull", "--overwrite"], cwd=self._path))
        return now != self.revno()

    def update(self):
        """Pulls or branches the branch if it does not yet exist. Returns True if something was updated."""
        if self.is_branched():
            return self.pull()
        self.branch()
        return True

    def push(self):
        retry_on_dns_failure(
                lambda: run_command(["bzr", "push", ":parent", "--overwrite"], cwd=self._path))

    def revno(self):
        if not self.is_branched:
            return 0
        return int(run_command(["bzr", "revno"],
                               cwd=self._path,
                               verbose=False))

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

        self.fix_formatting()

        full_commit_message = "Merged lp:%s" % source.name
        if commit_message is not None:
            full_commit_message += ":\n"
            full_commit_message += commit_message
        else:
            full_commit_message += "."
        run_command(["bzr", "commit", "-m", full_commit_message],
                    cwd=self._path)
        self.push()

    def update_git(self, git_repo):
        """Creates or updates a branch in git branch named 'slug' that track
        the bzr branch in the bzr_repo."""
        run_command(["git", "config", "remote-bzr.branches", self.slug], cwd=git_repo)
        run_command(["git", "fetch", BZR_REPO_NAME], cwd=git_repo)
        if self.slug not in git_branches(git_repo):
            run_command(
                ["git", "branch", "--track", self.slug,
                 "%s/%s" % (BZR_REPO_NAME, self.slug)],
                cwd=git_repo)
        git_checkout_branch(git_repo, self.slug)
        retry_on_dns_failure(lambda: run_command(["git", "pull"], cwd=git_repo))
        retry_on_dns_failure(lambda: run_command(
            ["git", "push", "github", self.slug, "--force"], cwd=git_repo))

    @property
    def travis_state(self):
        return self._travis_state

    @property
    def appveyor_state(self):
        return self._appveyor_state

    def update_travis_state(self, old_travis_state):
        """Checks if there is a travis state available for this branch."""
        self._travis_state["state"] = old_travis_state

        url = "https://api.travis-ci.org/repos/widelands/widelands/branches/%s" % self.slug
        try:
            data = read_url(url)
        except Exception as e:
            print "Error while fetching information from travis: %r" % (e)
            return
        if data is None:
            return
        d = json.loads(data)
        branch = d.get("branch", None)
        self._travis_state = {
            "state": branch["state"],
            "number": branch["number"],
            "id": branch["id"],
        }

        # No reason to report transient states
        if self._travis_state["state"] not in ("passed", "failed", "errored",
                                               "canceled"):
            self._travis_state["state"] = old_travis_state

    def update_appveyor_state(self, old_appveyor_state):
        """Checks if there is a appveyor state available for this branch."""
        url = "https://ci.appveyor.com/api/projects/widelands-dev/widelands/branch/%s" % self.slug
        data = read_url(url)
        if data is None:
            return
        d = json.loads(data)
        branch = d.get("build", None)
        self._appveyor_state = {
            "state": branch["status"],
            "number": branch["buildNumber"],
            "id": branch["version"],
        }

        # No reason to report transient states
        if self._appveyor_state["state"] not in ("success", "failed",
                                                 "errored", "canceled"):
            self._appveyor_state["state"] = old_appveyor_state

    def serialize(self):
        state = {}
        if self._travis_state:
            state['travis_state'] = {"state": self._travis_state['state']}
        if self._appveyor_state:
            state['appveyor_state'] = {"state": self._appveyor_state['state']}
        return state

    def fix_formatting(self):
        run_command(["utils/fix_formatting.py"], cwd=self._path)
        try:
            run_command(["bzr", "commit", "-m", "Fix formatting."], cwd=self._path)
        except ProcessFailed as error:
            if "No changes to commit." not in error.stdout:
                to_stdout("Process failed: %r" % error)

def git_branches(git_repo):
    lines = run_command(
        ["git", "branch"],
        cwd=git_repo,
        verbose=False).splitlines()
    branches = set()
    for line in lines:
        if line.startswith("*"):
            line = line[2:]
        branches.add(line.strip())
    return branches


def git_delete_remote_branch(git_repo, branch_name):
    run_command(["git", "push", "github", ":" + branch_name], cwd=git_repo)


def git_delete_local_branch(git_repo, branch_name):
    if branch_name == "master":
        raise RuntimeError("Cannot delete master branch.")

    git_checkout_branch(git_repo, "master")
    run_command(["git", "branch", "-D", branch_name], cwd=git_repo)


def get_merge_proposals(project, bzr_repo):
    merge_proposals = [m for m in project.getMergeProposals()
                       if m.queue_status != u"Work in progress"]

    branches = {}
    for proposal in merge_proposals:
        for branch in (proposal.target_branch, proposal.source_branch):
            branches[branch.unique_name] = Branch(branch.unique_name, bzr_repo)
    return [MergeProposal(m, branches) for m in merge_proposals], branches


def parse_args():
    p = argparse.ArgumentParser(
        description="Mergebot for the Widelands project")

    p.add_argument("--config",
                   type=str,
                   default="data/config.json",
                   help="The configuration file for the bot.")
    p.add_argument(
        "--always-update",
        action="store_true",
        default=False,
        help="Update git branches, even if it seems bzr has not changed.")
    return p.parse_args()


class MergeProposal(object):
    def __init__(self, lp_object, branches):
        self.source_branch = branches[lp_object.source_branch.unique_name]
        self.target_branch = branches[lp_object.target_branch.unique_name]
        self._comments = [c.message_body for c in lp_object.all_comments]
        self._lp_object = lp_object

    def _merge(self):
        self.target_branch.update()
        self.target_branch.merge_source(self.source_branch,
                                        self._lp_object.commit_message)

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

    def handle(self, old_state, git_repo, always_update):
        was_updated = self.source_branch.update()

        sys.stdout.write("ALIVE 1!\n"); sys.stdout.flush()
        old_travis_state = old_state['branches'].get(
            self.source_branch.name, {}).get(
                "travis_state", {}).get("state", None)
        sys.stdout.write("ALIVE 2!\n"); sys.stdout.flush()
        self.source_branch.update_travis_state(old_travis_state)
        sys.stdout.write("ALIVE 3!\n"); sys.stdout.flush()

        sys.stdout.write("ALIVE 4!\n"); sys.stdout.flush()
        old_appveyor_state = old_state['branches'].get(
            self.source_branch.name, {}).get(
                "appveyor_state", {}).get("state", None)
        sys.stdout.write("ALIVE 5!\n"); sys.stdout.flush()
        self.source_branch.update_appveyor_state(old_appveyor_state)
        sys.stdout.write("ALIVE 6!\n"); sys.stdout.flush()

        sys.stdout.write("ALIVE 7!\n"); sys.stdout.flush()
        if always_update or was_updated:
            sys.stdout.write("ALIVE! 8\n"); sys.stdout.flush()
            self.source_branch.update_git(git_repo)
            sys.stdout.write("ALIVE! 9\n"); sys.stdout.flush()
        sys.stdout.write("ALIVE! 10\n"); sys.stdout.flush()

        # Check for changes to the travis state, given we know anything about
        # the travis state.
        sys.stdout.write("ALIVE! 11\n"); sys.stdout.flush()
        current_travis_state = self.source_branch.travis_state.get(
            "state", None)
        sys.stdout.write("ALIVE! 12\n"); sys.stdout.flush()
        current_appveyor_state = self.source_branch.appveyor_state.get(
            "state", None)
        sys.stdout.write("ALIVE! 13\n"); sys.stdout.flush()
        if current_travis_state is not None and current_appveyor_state is not None:
            if old_travis_state != current_travis_state or old_appveyor_state != current_appveyor_state:
                self.create_comment(build_ci_update(self.source_branch))
        sys.stdout.write("ALIVE! 14\n"); sys.stdout.flush()

        sys.stdout.write("ALIVE! 15\n"); sys.stdout.flush()
        for c in self.new_comments(old_state):
            if re.search("^@bunnybot.*merge", c, re.MULTILINE) is not None:
                self._merge()
        sys.stdout.write("ALIVE! 16\n"); sys.stdout.flush()

    def report_exception(self, exception):
        lines = [
            "Bunnybot encountered an error while working on this merge proposal:",
            "",
            str(exception),
        ]
        self.create_comment("\n".join(lines))
        print "Creating comment: %r" % (lines)

    def create_comment(self, content):
        # TODO(sirver): This subject is what Launchpad currently uses for sending out their email. We want
        # to use the same, so that threads are not broken in email clients, but Launchpad offers no API.
        subject = "[Merge] %s into %s" % (
            self._lp_object.source_branch.bzr_identity,
            self._lp_object.target_branch.bzr_identity, )
        self._lp_object.createComment(subject=subject, content=content)


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

def fix_trunk_formatting(trunk_name, bzr_repo):
    trunk = Branch(trunk_name, bzr_repo)
    trunk.update()
    trunk.fix_formatting()
    trunk.push()

def update_git_master(trunk_name, bzr_repo, git_repo):
    trunk = Branch(trunk_name, bzr_repo)
    trunk.update()
    trunk.update_git(git_repo)

    # Merge trunk into master and push to github.
    git_checkout_branch(git_repo, "master")
    run_command(["git", "merge", "--ff-only", trunk.slug], cwd=git_repo)
    run_command(["git", "push", "github", "master", "--force"], cwd=git_repo)

def update_build19(branch_name, bzr_repo, git_repo):
    # TODO(sirver): this might make issues if a tag 'build19" also exists in
    # master. Use a different branch name?
    b19 = Branch(branch_name, bzr_repo)
    b19.update()
    b19.update_git(git_repo)

    # Merge b19 into master and push to github.
    git_checkout_branch(git_repo, "build19")
    run_command(["git", "merge", "--ff-only", b19.slug], cwd=git_repo)
    run_command(["git", "push", "github", "build19", "--force"], cwd=git_repo)

def delete_unmentioned_branches(branches, bzr_repo, git_repo):
    branches_slugs = set(b.slug for b in branches.values())

    # Keep the build 19 branch around.
    branches_slugs.add("_widelands_dev_widelands_b19")

    checked_out_bzr_branches = set(
        os.path.basename(d) for d in glob(os.path.join(bzr_repo, "*"))
        if os.path.isdir(d))

    for slug in (checked_out_bzr_branches - branches_slugs):
        to_stdout("Deleting %s which is not mentioned anymore." % slug)
        # Ignore errors - most likely some branches where not really there.
        try:
            git_delete_remote_branch(git_repo, slug)
        except ProcessFailed as error:
            to_stdout("Process failed: %r" % error)

        try:
            git_delete_local_branch(git_repo, slug)
        except ProcessFailed as error:
            to_stdout("Process failed: %r" % error)

        # shutil.rmtree chokes on some of our filenames.
        run_command(["rm", "-rf", os.path.join(bzr_repo, slug)])


def main():
    os.nice(10)  # Run at a really low priority.
    args = parse_args()
    config = json.load(open(args.config, "r"))

    old_state = load_state(config["state_file"])

    if not os.path.isdir(config["bzr_repo"]):
        run_command(["bzr", "init-repo", config["bzr_repo"]])
        run_command(
            ["git", "remote", "add", BZR_REPO_NAME,
             "bzr::file://" + os.path.abspath(config["bzr_repo"])],
            cwd=config["git_repo"])
    lp = Launchpad.login_with("wideland's bunnybot",
                              "production",
                              credentials_file=config["launchpad_credentials"])
    project = lp.projects["widelands"]
    merge_proposals, branches = get_merge_proposals(
        project, config["bzr_repo"])
    for merge_proposal in merge_proposals:
        to_stdout("===> Working on %s -> %s" % (
            merge_proposal.source_branch.name,
            merge_proposal.target_branch.name))
        try:
            merge_proposal.handle(old_state, config["git_repo"],
                                  args.always_update)
        except Exception as e:
            merge_proposal.report_exception(e)
        to_stdout("\n\n")

    dump_state(config["state_file"], merge_proposals, branches)

    fix_trunk_formatting(config["master_mirrors"], config["bzr_repo"])
    update_git_master(config["master_mirrors"], config["bzr_repo"],
                      config["git_repo"])
    #  update_build19("~widelands-dev/widelands/b19", config["bzr_repo"],
                      #  config["git_repo"])
    delete_unmentioned_branches(
        branches, config["bzr_repo"], config["git_repo"])
    return 0


if __name__ == '__main__':
    try:
        with pid.PidFile(piddir="."):
            main()
    except pid.PidFileAlreadyLockedError:
        to_stdout("PID file exists. Cowardly refusing to work.")
        pass
