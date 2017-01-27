#![recursion_limit = "1024"]

#[cfg(target_os = "linux")]
extern crate scheduler;
#[macro_use]
extern crate error_chain;
#[macro_use]
extern crate serde_derive;
extern crate serde_json;
extern crate bunnybot;
extern crate reqwest;
extern crate clap;

use std::collections::{HashMap, HashSet};
use bunnybot::git;
use bunnybot::errors::*;
use bunnybot::pidfile::Pidfile;
use bunnybot::launchpad;
use bunnybot::subprocess::{run_command, Verbose};
use std::fs;
use std::path::Path;

#[derive(Debug,Serialize,Deserialize,Default)]
struct CiState {
    state: String,
}

#[derive(Debug,Serialize,Deserialize,Default)]
struct BranchState {
    appveyor_state: CiState,
    travis_state: CiState,
}

#[derive(Debug,Serialize,Deserialize)]
struct MergeProposalState {
    num_comments: usize,
    source_branch: String,
    target_branch: String,
}

#[derive(Debug,Serialize,Deserialize)]
struct State {
    branches: HashMap<String, BranchState>,
    merge_proposals: Vec<MergeProposalState>,
}

impl State {
    pub fn load(data_dir: &Path) -> Result<Self> {
        let file = fs::File::open(&data_dir.join("state.json")).chain_err(|| "Could not find state.json.")?;
        let this = serde_json::from_reader(file).chain_err(|| "Could not parse state.json.")?;
        Ok(this)
    }

    pub fn save(&self, data_dir: &Path) -> Result<()> {
        let mut file = fs::File::create(&data_dir.join("state.json")).chain_err(|| "Could not open state.json.")?;
        serde_json::to_writer_pretty(&mut file, self).chain_err(|| "Could not write state.json")?;
        Ok(())
    }

    pub fn find_or_insert_merge_proposal_state(&mut self,
                                               mp: &launchpad::MergeProposal)
                                               -> &mut MergeProposalState {
        let mut index = None;
        for (idx, item) in self.merge_proposals.iter().enumerate() {
            if item.source_branch == mp.source_branch.unique_name &&
               item.target_branch == mp.target_branch.unique_name {
                index = Some(idx);
                break;
            }
        }
        if index.is_none() {
            self.merge_proposals.push(MergeProposalState {
                num_comments: 0,
                source_branch: mp.source_branch.unique_name.clone(),
                target_branch: mp.target_branch.unique_name.clone(),
            });
            index = Some(self.merge_proposals.len() - 1);
        }
        self.merge_proposals.get_mut(index.unwrap()).unwrap()
    }

    pub fn remove_mentions_of(&mut self, slug: &str) {
        self.merge_proposals.retain(|m| launchpad::slugify(&m.source_branch) != slug);
        let new_branches =
            self.branches.drain().filter(|&(ref k, _)| launchpad::slugify(&k) != slug).collect();
        self.branches = new_branches;
    }
}

fn delete_unmentioned_branches(slugs: &HashSet<String>,
                               state: &mut State,
                               bzr_repo: &Path,
                               git_repo: &Path)
                               -> Result<()> {
    let mut checked_out_branches = HashSet::new();
    for path in fs::read_dir(bzr_repo).unwrap() {
        let path = path.unwrap().path();
        if !path.is_dir() || path.file_name().unwrap() == ".bzr" {
            continue;
        }
        checked_out_branches.insert(path.file_name().unwrap().to_string_lossy().to_string());
    }

    for slug in checked_out_branches.difference(&slugs) {
        println!("Deleting {} which is not mentioned anymore.", slug);

        // Ignore errors - most likely some branches where not really there.
        let _ = git::delete_remote_branch(git_repo, slug)
            .map_err(|err| println!("Ignored error while deleting remote branch: {}", err));
        let _ = git::delete_local_branch(git_repo, slug)
            .map_err(|err| println!("Ignored error while deleting local branch: {}", err));
        let _ = fs::remove_dir_all(&bzr_repo.join(slug))
            .map_err(|err| println!("Ignored error while deleting bzr dir: {}", err));
        state.remove_mentions_of(&slug);
    }
    Ok(())
}

fn update_git_master(bzr_repo: &Path, git_repo: &Path) -> Result<()> {
    let trunk = launchpad::Branch::from_unique_name("~widelands-dev/widelands/trunk");
    trunk.update(bzr_repo)?;
    trunk.update_git(git_repo)?;

    // Merge trunk into master and push to github.
    git::checkout_branch(git_repo, "master")?;
    run_command(&["git", "merge", "--ff-only", &trunk.slug],
                git_repo,
                Verbose::Yes)?;
    run_command(&["git", "push", "github", "master", "--force"],
                git_repo,
                Verbose::Yes)?;
    Ok(())
}

#[cfg(target_os = "linux")]
fn set_nice_level() -> Result<()> {
    scheduler.set_self_priority(10)?;
}

#[cfg(not(target_os = "linux"))]
fn set_nice_level() -> Result<()> {
    Ok(())
}

fn parse_args() -> clap::ArgMatches<'static> {
    clap::App::new("Mergebot for the Widelands project")
        .version("1.0")
        .arg(clap::Arg::with_name("data_dir")
            .long("data_dir")
            .help("Data directory.")
            .takes_value(true)
            .default_value("data"))
        .arg(clap::Arg::with_name("always_update")
            .long("always_update")
            .help("Update git branches, even if it seems bzr has not changed."))
        .get_matches()
}

fn run() -> Result<()> {
    let args = parse_args();
    let always_update = args.occurrences_of("always_update") > 0;
    let data_dir = Path::new(args.value_of("data_dir").unwrap());
    let bzr_repo = data_dir.join(Path::new("bzr_repo"));
    let git_repo = data_dir.join(Path::new("git_repo"));

    let mut state = State::load(&data_dir)?;

    let _pidfile = Pidfile::new()?;
    set_nice_level()?;

    let mut branches_slug = HashSet::<String>::new();

    let merge_proposals = bunnybot::launchpad::get_merge_proposals("~widelands-dev/widelands/trunk")?;
    for m in merge_proposals {
        println!("===> Working on {} -> {}",
                 m.source_branch.unique_name,
                 m.target_branch.unique_name);
        branches_slug.insert(m.target_branch.slug.clone());
        branches_slug.insert(m.source_branch.slug.clone());

        // NOCOM(#sirver): this is the slowest part here.
        let travis_state = m.source_branch.travis_state()?;
        let appveyor_state = m.source_branch.appveyor_state()?;

        let mut update = always_update;
        if let bunnybot::launchpad::WasUpdated::Yes(_) = m.source_branch.update(&bzr_repo)? {
            update = true;
        }
        if update {
            m.source_branch.update_git(&git_repo)?;
        }

        // Update branch state.
        {
            let mut branch_state = state.branches
                .entry(m.source_branch.unique_name.clone())
                .or_insert(BranchState::default());
            branch_state.travis_state.state = travis_state.state;
            branch_state.appveyor_state.state = appveyor_state.status;
        }

        // Update merge proposal state.
        {
            let merge_proposal_state = state.find_or_insert_merge_proposal_state(&m);
            merge_proposal_state.num_comments = m.comments.len();
        }

        state.save(&data_dir).unwrap();
        println!("\n");
    }
    state.save(&data_dir).unwrap();

    update_git_master(&bzr_repo, &git_repo)?;
    delete_unmentioned_branches(&branches_slug, &mut state, &bzr_repo, &git_repo)?;

    Ok(())
}

quick_main!(run);
