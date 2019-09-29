use chrono::prelude::*;
use crate::errors::*;
use crate::git;
use rand::{self, Rng};
use regex::Regex;
use reqwest;
use reqwest::header::{HeaderMap, HeaderValue, AUTHORIZATION};
use serde;
use serde_json;
use std::collections::HashMap;
use std::fs;
use std::io::Read;
use std::path::Path;
use crate::subprocess::{run_command, Verbose};

const API_BASE: &str = "https://api.launchpad.net";
const LP_API: &'static str = "https://api.launchpad.net/1.0/";
const TRAVIS_ROOT: &'static str = "https://api.travis-ci.org/repos/widelands/widelands/branches";
const APPVEYOR_ROOT: &'static str = "https://ci.appveyor.\
                                     com/api/projects/widelands-dev/widelands/branch";

lazy_static! {
    static ref SLUG_REGEX: Regex = Regex::new(r"[^A-Za-z0-9]").unwrap();
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Credentials {
    consumer_key: String,
    access_token: String,
    access_secret: String,
}

impl Credentials {
    pub fn load(data_dir: &Path) -> Result<Self> {
        let file = fs::File::open(&data_dir.join("launchpad_credentials.json"))
            .chain_err(|| "Could not find launchpad_credentials.json.")?;
        let this = serde_json::from_reader(file)
            .chain_err(|| "Could not parse launchpad_credentials.json.")?;
        Ok(this)
    }
}

#[derive(Deserialize, Debug)]
struct JsonCollection<T> {
    entries: Vec<T>,
}

#[derive(Deserialize, Debug)]
struct JsonMergeProposal {
    self_link: String,
    all_comments_collection_link: String,
    source_branch_link: String,
    target_branch_link: String,
    commit_message: Option<String>,
}

#[derive(Deserialize, Debug)]
struct JsonBranch {
    self_link: String,
    bzr_identity: String,
    unique_name: String,
}

#[derive(Deserialize, Debug)]
pub struct Comment {
    pub message_body: String,
}

#[derive(Debug)]
pub struct Branch {
    // For example: ~widelands-dev/widelands/trunk
    pub unique_name: String,
    pub slug: String,
}

#[derive(Debug, Deserialize)]
struct JsonTravisBuild {
    branch: JsonTravisBranch,
}

#[derive(Debug, Deserialize)]
struct JsonTravisBranch {
    state: String,
    number: String,
    id: i64,
}

#[derive(Debug, Default, Serialize, Deserialize, Clone)]
pub struct CiState {
    pub state: String,
    pub id: String,
    pub number: String,
}

impl CiState {
    pub fn is_transitional(&self) -> bool {
        for state in ["success", "passed", "failed", "errored", "canceled"].iter() {
            if self.state == *state {
                return false;
            }
        }
        true
    }
}

#[derive(Debug, Deserialize)]
struct JsonAppveyorBuild {
    build: JsonAppveyorBranch,
}

#[derive(Debug, Deserialize)]
struct JsonAppveyorBranch {
    status: String,
    #[serde(rename = "buildNumber")]
    build_number: i64,
    version: String,
}

pub fn slugify(branch: &str) -> String {
    SLUG_REGEX.replace_all(&branch, "_").to_string()
}

impl Branch {
    fn from_lp_api_link(url: &str) -> Self {
        assert!(url.starts_with(LP_API));
        Branch::from_unique_name(url.split_at(LP_API.len()).1)
    }

    pub fn from_unique_name(unique_name: &str) -> Self {
        let slug = slugify(&unique_name);
        Branch {
            unique_name: unique_name.to_string(),
            slug: slug,
        }
    }

    /// Returns true if the branch changed.
    pub fn update(&self, bzr_repo: &Path) -> Result<bool> {
        if !self.is_branched(bzr_repo) {
            self.branch(bzr_repo)?;
            return Ok(true);
        }
        self.pull(bzr_repo)
    }

    fn is_branched(&self, bzr_repo: &Path) -> bool {
        bzr_repo.join(Path::new(&self.slug)).exists()
    }

    fn branch(&self, bzr_repo: &Path) -> Result<()> {
        run_command(
            &[
                "bzr",
                "branch",
                &format!("lp:{}", self.unique_name),
                &self.slug,
            ],
            bzr_repo,
            Verbose::Yes,
        )?;
        Ok(())
    }

    /// Revert the changes in the branch (if any).
    fn revert(&self, bzr_repo: &Path) -> Result<()> {
        run_command(&["bzr", "revert"], &bzr_repo.join(&self.slug), Verbose::Yes)?;
        run_command(
            &["bzr", "clean-tree", "--unknown", "--detritus", "--force"],
            &bzr_repo.join(&self.slug),
            Verbose::Yes,
        )?;
        Ok(())
    }

    /// Returns true if the branch changed.
    fn pull(&self, bzr_repo: &Path) -> Result<bool> {
        let before = self.revno(bzr_repo)?;
        self.revert(bzr_repo)?;
        run_command(
            &["bzr", "pull", "--overwrite"],
            &bzr_repo.join(&self.slug),
            Verbose::Yes,
        )?;
        Ok(before != self.revno(bzr_repo)?)
    }

    fn revno(&self, bzr_repo: &Path) -> Result<i32> {
        assert!(self.is_branched(bzr_repo));
        let output =
            run_command(&["bzr", "revno"], &bzr_repo.join(&self.slug), Verbose::No)?.stdout;
        let revno = output.trim().parse().unwrap();
        Ok(revno)
    }

    pub fn update_git(&self, git_repo: &Path) -> Result<()> {
        run_command(
            &["git", "config", "remote-bzr.branches", &self.slug],
            git_repo,
            Verbose::Yes,
        )?;
        run_command(&["git", "fetch", "bzr_origin"], git_repo, Verbose::Yes)?;

        if !git::branches(git_repo)?.contains(&self.slug) {
            run_command(
                &[
                    "git",
                    "branch",
                    "--track",
                    &self.slug,
                    &format!("bzr_origin/{}", self.slug),
                ],
                git_repo,
                Verbose::Yes,
            )?;
        }
        git::checkout_branch(git_repo, &self.slug)?;
        run_command(&["git", "pull"], git_repo, Verbose::Yes)?;
        run_command(
            &["git", "push", "github", &self.slug, "--force"],
            git_repo,
            Verbose::Yes,
        )?;
        Ok(())
    }

    pub fn travis_state(&self) -> Result<CiState> {
        let url = format!("{}/{}", TRAVIS_ROOT, self.slug);
        let result = get::<JsonTravisBuild>(&url)?;
        Ok(CiState {
            state: result.branch.state,
            number: result.branch.number,
            id: result.branch.id.to_string(),
        })
    }

    pub fn appveyor_state(&self) -> Result<CiState> {
        let url = format!("{}/{}", APPVEYOR_ROOT, self.slug);
        let result = get::<JsonAppveyorBuild>(&url)?;
        Ok(CiState {
            state: result.build.status,
            number: result.build.build_number.to_string(),
            id: result.build.version,
        })
    }

    fn push(&self, bzr_repo: &Path) -> Result<()> {
        let path = bzr_repo.join(&self.slug);
        run_command(
            &["bzr", "push", ":parent", "--overwrite"],
            &path,
            Verbose::Yes,
        )?;
        Ok(())
    }

    fn fix_formatting(&self, bzr_repo: &Path, commit: bool) -> Result<()> {
        const FORMATTING: &'static str = "utils/fix_formatting.py";
        let path = bzr_repo.join(&self.slug);
        if !path.join(FORMATTING).exists() {
            println!("Did not find {}. Not trying to run it.", FORMATTING);
            return Ok(());
        }
        run_command(&[FORMATTING], &path, Verbose::Yes)?;
        if !commit {
            return Ok(());
        }

        let result = run_command(
            &["bzr", "commit", "-m", "Fix formatting."],
            &path,
            Verbose::Yes,
        );
        match result {
            Ok(_) => Ok(()),
            Err(Error(ErrorKind::ProcessFailed(output), _)) => {
                // If there is nothing to commit, the codecheck didn't change anything. This is no
                // error.
                if output.stdout.find("No changes to commit.").is_none() {
                    Err(ErrorKind::ProcessFailed(output).into())
                } else {
                    Ok(())
                }
            }
            Err(err) => Err(err),
        }
    }

    fn merge_source(
        &self,
        bzr_repo: &Path,
        source: &Branch,
        commit_message: &Option<String>,
    ) -> Result<()> {
        let target_path = bzr_repo.join(&self.slug);
        run_command(
            &["bzr", "merge", &format!("../{}", source.slug)],
            &target_path,
            Verbose::Yes,
        )?;

        self.fix_formatting(bzr_repo, false)?;

        let mut full_commit_message = format!("Merged lp:{}", source.unique_name);
        if let Some(ref commit_message) = *commit_message {
            full_commit_message.push_str(":\n");
            full_commit_message.push_str(commit_message);
        } else {
            full_commit_message.push_str(".");
        }
        run_command(
            &["bzr", "commit", "-m", &full_commit_message],
            &target_path,
            Verbose::Yes,
        )?;
        self.push(bzr_repo)?;
        Ok(())
    }
}

#[derive(Debug)]
pub struct MergeProposal {
    pub source_branch: Branch,
    pub target_branch: Branch,
    pub comments: Vec<Comment>,
    commit_message: Option<String>,
    self_link: String,
    source_branch_link: String,
    target_branch_link: String,
}

impl MergeProposal {
    fn from_json(json: JsonMergeProposal) -> Result<Self> {
        let comments = get::<JsonCollection<Comment>>(&json.all_comments_collection_link)?.entries;

        let merge_proposal = MergeProposal {
            source_branch: Branch::from_lp_api_link(&json.source_branch_link),
            source_branch_link: json.source_branch_link,
            target_branch: Branch::from_lp_api_link(&json.target_branch_link),
            target_branch_link: json.target_branch_link,
            commit_message: json.commit_message,
            comments: comments,
            self_link: json.self_link,
        };
        Ok(merge_proposal)
    }

    pub fn add_comment(&self, credentials: &Credentials, comment: &str) -> Result<()> {
        let source_branch_json = get::<JsonBranch>(&self.source_branch_link)?;
        let target_branch_json = get::<JsonBranch>(&self.target_branch_link)?;

        // This subject is what Launchpad currently uses for sending out their email. We want to
        // use the same, so that threads are not broken in email clients, but Launchpad offers no
        // API.
        let subject = format!(
            "[Merge] {} into {}",
            source_branch_json.bzr_identity, target_branch_json.bzr_identity
        );

        let mut values = HashMap::new();
        values.insert("ws.op", "createComment");
        values.insert("subject", &subject);
        values.insert("content", comment);
        post(&self.self_link, credentials, values)
            .chain_err(|| ErrorKind::Http(self.self_link.clone()))
    }

    pub fn merge(&self, bzr_repo: &Path) -> Result<()> {
        self.target_branch.update(bzr_repo)?;
        let result =
            self.target_branch
                .merge_source(bzr_repo, &self.source_branch, &self.commit_message);
        if result.is_err() {
            // Ignoring errors. We just try to clean up.
            let _ = self.target_branch.revert(bzr_repo);
        }
        result
    }
}

fn get<D>(url: &str) -> Result<D>
where
    D: serde::Deserialize,
{
    let mut response = reqwest::get(url).chain_err(|| ErrorKind::Http(url.to_string()))?;
    if response.status() != reqwest::StatusCode::OK {
        bail!(ErrorKind::Http(url.to_string()));
    }

    let mut json = String::new();
    response.read_to_string(&mut json).unwrap();
    let result =
        serde_json::from_str(&json).chain_err(|| format!("Invalid JSON object: {}", &json))?;
    Ok(result)
}

fn post(url: &str, credentials: &Credentials, fields: HashMap<&str, &str>) -> reqwest::Result<()> {
    let client = reqwest::Client::new();

    let mut headers = HeaderMap::new();
    headers.insert(
        AUTHORIZATION,
        HeaderValue::from_str(&build_oauth_str(credentials)).unwrap(),
    );

    client.post(url).form(&fields).headers(headers).send()?;
    Ok(())
}

fn build_oauth_str(credentials: &Credentials) -> String {
    let mut rng = rand::thread_rng();
    let nonce: u64 = rng.gen();
    let utc: DateTime<Utc> = Utc::now();
    format!(
        "OAuth realm=\"{}/\", \
         oauth_consumer_key=\"{}\", \
         oauth_token=\"{}\", \
         oauth_signature_method=\"PLAINTEXT\", \
         oauth_signature=\"%26{}\", \
         oauth_timestamp=\"{}\", \
         oauth_nonce=\"{}\", \
         oauth_version=\"1.0\"",
        API_BASE,
        credentials.consumer_key,
        credentials.access_token,
        credentials.access_secret,
        utc.timestamp(),
        nonce
    )
}

pub fn get_merge_proposals(name: &str) -> Result<Vec<MergeProposal>> {
    let url = format!(
        "{}{}?ws.op=getMergeProposals&status=Needs review",
        LP_API, name
    );
    let mut entries = Vec::new();
    for json_entry in get::<JsonCollection<JsonMergeProposal>>(&url)?.entries {
        entries.push(MergeProposal::from_json(json_entry)?);
    }
    Ok(entries)
}
