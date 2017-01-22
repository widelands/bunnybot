use reqwest;
use errors::*;
use serde_json;
use serde;

const LP_API: &'static str = "https://api.launchpad.net/1.0/";

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
struct Branch {
    // For example: ~widelands-dev/widelands/trunk
    unique_name: String,
}

impl Branch {
    pub fn from_lp_api_link(url: &str) -> Self {
        assert!(url.starts_with(LP_API));
        Branch { unique_name: url.split_at(LP_API.len()).1.to_string() }
    }
}

#[derive(Debug)]
pub struct MergeProposal {
    source_branch: Branch,
    target_branch: Branch,
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
    let response = reqwest::get(url).chain_err(|| ErrorKind::Http(url.to_string()))?;
    if *response.status() != reqwest::StatusCode::Ok {
        bail!(ErrorKind::Http(url.to_string()));
    }

    let result = serde_json::from_reader(response).chain_err(|| "Invalid JSON object.")?;
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
