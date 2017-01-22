use reqwest;
use errors::*;
use serde_json;
use serde;
use std::path::Path;
use regex::Regex;
use std::process;
use std::io::Read;

const LP_API: &'static str = "https://api.launchpad.net/1.0/";
const TRAVIS_ROOT: &'static str = "https://api.travis-ci.org/repos/widelands/widelands/branches";
const APPVEYOR_ROOT: &'static str = "https://ci.appveyor.com/api/projects/widelands-dev/widelands/branch";

lazy_static! {
    static ref SLUG_REGEX: Regex = Regex::new(r"[^A-Za-z0-9]").unwrap();
}

#[derive(Deserialize, Debug)]
struct JsonCollection<T> {
    entries: Vec<T>,
}

// NOCOM(#sirver): should not be pub
#[derive(Deserialize, Debug)]
pub struct JsonMergeProposal {
    self_link: String,
    source_branch_link: String,
    target_branch_link: String,
    commit_message: Option<String>,
}

#[derive(Deserialize, Debug)]
pub struct JsonBranch {
    self_link: String,
    unique_name: String,
}

#[derive(Debug)]
pub struct Revisions {
    // NOCOM(#sirver): what if it was not there?
    pub before: i32,
    pub after: i32,
}

#[derive(Debug)]
pub enum WasUpdated {
    No,
    Yes(Revisions),
}

#[derive(Debug)]
pub struct Branch {
    // For example: ~widelands-dev/widelands/trunk
    pub unique_name: String,
    pub slug: String,
}

#[derive(Debug)]
struct Output {
    stdout: String,
    stderr: String,
}

#[derive(Debug,PartialEq)]
enum Verbose {
    Yes,
    No,
}

#[derive(Debug,Deserialize)]
struct JsonTravisBuild {
    branch: JsonTravisBranch,
}

#[derive(Debug,Deserialize)]
pub struct JsonTravisBranch {
    finished_at: String,
    started_at: String,
    state: String,
}

#[derive(Debug,Deserialize)]
struct JsonAppveyorBuild {
    build: JsonAppveyorBranch,
}

#[derive(Debug,Deserialize)]
pub struct JsonAppveyorBranch {
    created: String,
    finished: Option<String>,
    status: String,
}

fn run_command(args: &[&str], cwd: &Path, verbose: Verbose) -> Result<Output> {
    let command = args.join(" ");
    if verbose == Verbose::Yes {
        println!("-> {} [{}]", command, cwd.to_string_lossy());
    }

    let mut command = process::Command::new(args[0]);
    command.args(&args[1..]);
    command.current_dir(cwd);
    // This should always run - or the binary was not found, which is indeed fatal.
    let res = command.output().unwrap();
    let output = Output {
        stdout: String::from_utf8(res.stdout).unwrap_or_else(|_| "...garbage...".into()),
        stderr: String::from_utf8(res.stderr).unwrap_or_else(|_| "...garbage...".into()),
    };

    if verbose == Verbose::Yes {
        for line in output.stdout.lines() {
            println!("    {}", line.trim_right());
        }
        for line in output.stderr.lines() {
            println!("    {}", line.trim_right());
        }
    }

    if !res.status.success() {
        bail!(ErrorKind::ProcessFailed);
    }
    Ok(output)
}

fn git_branches(git_repo: &Path) -> Result<Vec<String>> {
    let output = run_command(&["git", "branch"], git_repo, Verbose::No)?.stdout;
    let mut branches = Vec::new();
    for mut line in output.lines() {
        if line.starts_with("*") {
            line = &line[2..];
        }
        branches.push(line.trim().to_string());
    }
    Ok(branches)
}

fn git_checkout_branch(git_repo: &Path, branch: &str) -> Result<()> {
    run_command(&["git", "checkout", branch], git_repo, Verbose::No)?;
    Ok(())
}

impl Branch {
    pub fn from_lp_api_link(url: &str) -> Self {
        assert!(url.starts_with(LP_API));
        let unique_name = url.split_at(LP_API.len()).1.to_string();
        let slug = SLUG_REGEX.replace_all(&unique_name, "_").to_string();
        Branch {
            unique_name: unique_name,
            slug: slug,
        }
    }

    pub fn update(&self, bzr_repo: &Path) -> Result<WasUpdated> {
        if !self.is_branched(bzr_repo) {
            self.branch(bzr_repo)?;
            return Ok(WasUpdated::Yes(Revisions {
                before: 0,
                after: self.revno(bzr_repo)?,
            }));
        }
        self.pull(bzr_repo)
    }

    fn is_branched(&self, bzr_repo: &Path) -> bool {
        bzr_repo.join(Path::new(&self.slug)).exists()
    }

    fn branch(&self, bzr_repo: &Path) -> Result<()> {
        run_command(&["bzr", "branch", &format!("lp:{}", self.unique_name), &self.slug],
                    bzr_repo,
                    Verbose::Yes)?;
        Ok(())
    }

    fn pull(&self, bzr_repo: &Path) -> Result<WasUpdated> {
        let before = self.revno(bzr_repo)?;
        run_command(&["bzr", "revert"], &bzr_repo.join(&self.slug), Verbose::Yes)?;
        run_command(&["bzr", "pull", "--overwrite"],
                    &bzr_repo.join(&self.slug),
                    Verbose::Yes)?;
        let after = self.revno(bzr_repo)?;
        Ok(if before != after {
            WasUpdated::Yes(Revisions {
                before: before,
                after: after,
            })
        } else {
            WasUpdated::No
        })
    }

    fn revno(&self, bzr_repo: &Path) -> Result<i32> {
        assert!(self.is_branched(bzr_repo));
        let output =
            run_command(&["bzr", "revno"], &bzr_repo.join(&self.slug), Verbose::No)?.stdout;
        let revno = output.trim().parse().unwrap();
        Ok(revno)
    }

    pub fn update_git(&self, git_repo: &Path) -> Result<()> {
        run_command(&["git", "config", "remote-bzr.branches", &self.slug],
                    git_repo,
                    Verbose::Yes)?;
        run_command(&["git", "fetch", "bzr_origin"], git_repo, Verbose::Yes)?;

        if !git_branches(git_repo)?.contains(&self.slug) {
            run_command(&["git",
                          "branch",
                          "--track",
                          &self.slug,
                          &format!("bzr_origin/{}", self.slug)],
                        git_repo,
                        Verbose::Yes)?;
        }
        git_checkout_branch(git_repo, &self.slug)?;
        run_command(&["git", "pull"], git_repo, Verbose::Yes)?;
        run_command(&["git", "push", "github", &self.slug, "--force"],
                    git_repo,
                    Verbose::Yes)?;
        Ok(())
    }

    pub fn travis_state(&self) -> Result<JsonTravisBranch> {
        let url = format!("{}/{}", TRAVIS_ROOT, self.slug);
        let result = get::<JsonTravisBuild>(&url)?;
        Ok(result.branch)
    }

    pub fn appveyor_state(&self) -> Result<JsonAppveyorBranch> {
        let url = format!("{}/{}", APPVEYOR_ROOT, self.slug);
        let result = get::<JsonAppveyorBuild>(&url)?;
        Ok(result.build)
    }
}

#[derive(Debug)]
pub struct MergeProposal {
    pub source_branch: Branch,
    pub target_branch: Branch,
    commit_message: Option<String>,
}

impl MergeProposal {
    pub fn from_json(json: JsonMergeProposal) -> Self {
        MergeProposal {
            source_branch: Branch::from_lp_api_link(&json.source_branch_link),
            target_branch: Branch::from_lp_api_link(&json.target_branch_link),
            commit_message: json.commit_message,
        }
    }
}

// NOCOM(#sirver): this is still somewhat awkward... what to return when?
fn get<D>(url: &str) -> Result<D>
    where D: serde::Deserialize
{
    let mut response = reqwest::get(url).chain_err(|| ErrorKind::Http(url.to_string()))?;
    if *response.status() != reqwest::StatusCode::Ok {
        bail!(ErrorKind::Http(url.to_string()));
    }

    let mut json = String::new();
    response.read_to_string(&mut json).unwrap();
    let result = serde_json::from_str(&json).chain_err(|| "Invalid JSON object.")?;
    Ok(result)
}

pub fn get_merge_proposals(name: &str) -> Result<Vec<MergeProposal>> {
    let url = format!("{}{}?ws.op=getMergeProposals&status=Needs review",
                      LP_API,
                      name);
    let entries = get::<JsonCollection<JsonMergeProposal>>(&url)
        ?
        .entries
        .into_iter()
        .map(MergeProposal::from_json)
        .collect();
    Ok(entries)
}
