#!/usr/bin/env python
# encoding: utf-8

from launchpadlib.launchpad import Launchpad
import argparse
import json

def get_merge_proposal(project, source_branch, target_branch):
    for p in project.getMergeProposals():
        if p.queue_status == u"Work in progress":
            continue
        if (p.source_branch.unique_name == source_branch and
               p.target_branch.unique_name == target_branch):
           return p
    raise RuntimeError("Did not find merge proposal!")

def parse_args():
    p = argparse.ArgumentParser(
        description="Mergebot for the Widelands project")

    p.add_argument("--comment",
                   type=str,
                   help="The JSON file describing the comment to make.")
    p.add_argument("--credentials",
            type=str,
            help="The Credentials file for login into Launchpad.")
    return p.parse_args()

def main():
    args = parse_args()
    comment = json.load(open(args.comment, "r"))

    lp = Launchpad.login_with("wideland's bunnybot",
                              "production",
                              credentials_file=args.credentials)
    project = lp.projects["widelands"]
    proposal = get_merge_proposal(
            project,
            comment["source_branch"], comment["target_branch"])

    # TODO(sirver): This subject is what Launchpad currently uses for sending out their email. We want
    # to use the same, so that threads are not broken in email clients, but Launchpad offers no API.
    subject = "[Merge] %s into %s" % (
        proposal.source_branch.bzr_identity,
        proposal.target_branch.bzr_identity, )
    proposal.createComment(subject=subject, content=comment['comment'])
    return 0

if __name__ == '__main__':
    main()
