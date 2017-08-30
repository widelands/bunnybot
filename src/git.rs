use subprocess::{run_command, Verbose};
use errors::*;
use std::path::Path;

pub fn branches(git_repo: &Path) -> Result<Vec<String>> {
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

pub fn checkout_branch(git_repo: &Path, branch: &str) -> Result<()> {
    run_command(&["git", "checkout", branch], git_repo, Verbose::No)?;
    Ok(())
}

pub fn delete_remote_branch(git_repo: &Path, branch: &str) -> Result<()> {
    run_command(
        &["git", "push", "github", &format!(":{}", branch)],
        git_repo,
        Verbose::No,
    )?;
    Ok(())
}

pub fn delete_local_branch(git_repo: &Path, branch: &str) -> Result<()> {
    if branch == "master" {
        bail!("Cannot delete master branch.");
    }
    checkout_branch(git_repo, "master")?;
    run_command(&["git", "branch", "-D", branch], git_repo, Verbose::No)?;
    Ok(())
}
