#!/usr/bin/env python
# encoding: utf-8

from launchpadlib.launchpad import Launchpad
import argparse
import datetime
import json
import os
import pytz
import re
import subprocess

# NOCOM(#sirver): we need to do this to be clean
#  find . -name '*.BASE' -print0 | xargs -0 rm -rv
#  find . -name '*.OTHER' -print0 | xargs -0 rm -rv
#  find . -name '*.THIS' -print0 | xargs -0 rm -rv
#  find . -name '*.moved' -print0 | xargs -0 rm -rv
#  find . -name '*.pyc' -print0 | xargs -0 rm -rv
#  find . -name '*~?~' -print0 | xargs -0 rm -rv
#  find . -name '.DS_Store' -print0 | xargs -0 rm -rv


class Branch(object):
    def __init__(self, name, bzr_repo):
        """Create a target branch from a unique_name like '~widelands-dev/widelands/trunk."""
        self._name = name
        self._bzr_repo = bzr_repo
        self._revno = None

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
        """Returns True if the branch has been branched already in the shared repo."""
        return os.path.isdir(self._path)

    def merge_source(self, source):
        print("-> Merging %s into %s" % (source.name, self.name))
        source_path = source._path
        if subprocess.call(["bzr", "merge", os.path.relpath(source_path,
                                                            self._path)],
                           cwd=self._path) != 0:
            # NOCOM(#sirver): This should capture the output and give some idea of the trouble
            return False

        if subprocess.call(["bzr", "commit", "-m", "Merged lp:%s" % source.name],
                           cwd=self._path) != 0:
            # NOCOM(#sirver): This should capture the output and give some idea of the trouble
            return False
        self.push()


def run_bzr(args, cwd=None):
    print("-> bzr %s%s" % (args, "" if cwd is None else " [%s]" % cwd))
    args = args.split(" ")
    subprocess.check_call(["bzr"] + args, cwd=cwd)

def get_merge_requests(project, bzr_repo):
    merge_proposals = [m for m in project.getMergeProposals()
                       if m.queue_status != u"Work in Progress"]

    branches = {}
    for proposal in merge_proposals:
        for branch in (proposal.target_branch, proposal.source_branch):
            branches[branch.unique_name] = Branch(branch.unique_name, bzr_repo)
    return [MergeRequest(m, branches) for m in merge_proposals]


def parse_args():
    p = argparse.ArgumentParser(
        description="Mergebot for the Widelands project")

    p.add_argument("--bzr-repo",
                   type=str,
                   default="bzr_repo",
                   help="The directory for the bzr data.")
    p.add_argument("--state-file",
                   type=str,
                   default="state.json",
                   help="File to load last known state from.")
    return p.parse_args()


class MergeRequest(object):
    def __init__(self, lp_object, branches):
        self._source_branch = branches[lp_object.source_branch.unique_name]
        self._target_branch = branches[lp_object.target_branch.unique_name]
        self._comments = [c.message_body for c in lp_object.all_comments]

    def merge(self):
        self._target_branch.update()
        self._source_branch.update()
        self._target_branch.merge_source(self._source_branch)

    def serialize(self):
        d = {}
        d['source_branch'] = self._source_branch.name
        d['target_branch'] = self._target_branch.name
        d['num_comments'] = len(self._comments)
        return d

    def new_comments(self, old_state):
        """Returns all new comments since this script ran the last time."""
        # NOCOM(#sirver): rename to merge proposals everywhere
        for proposal in old_state.get('merge_requests', []):
            if (proposal['target_branch'] == self._target_branch.name and
                    proposal['source_branch'] == self._source_branch.name):
                return self._comments[proposal['num_comments']:]
        return self._comments

def dump_state(json_file, merge_requests):
    state = {}
    state['merge_requests'] = [m.serialize() for m in merge_requests]
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

def main():
    args = parse_args()

    old_state = load_state(args.state_file)

    if not os.path.isdir(args.bzr_repo):
        run_bzr("init-repo %s" % args.bzr_repo)
    lp = Launchpad.login_anonymously("widelands mergebot", "production")
    project = lp.projects["widelands"]
    merge_requests = get_merge_requests(project, args.bzr_repo)

    for merge_request in merge_requests:
        for c in merge_request.new_comments(old_state):
            if re.search("^@bunnybot.*merge", c, re.MULTILINE) is not None:
                # NOCOM(#sirver): this should report errors
                merge_request.merge()

    # NOCOM(#sirver): delete branches that are never mentioned.
    dump_state(args.state_file, merge_requests)


if __name__ == '__main__':
    import sys
    sys.exit(main())
