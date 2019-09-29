use crate::errors::*;
use std::path::Path;
use std::process;

#[derive(Debug, PartialEq)]
pub enum Verbose {
    Yes,
    No,
}

#[derive(Debug)]
pub struct Output {
    pub stdout: String,
    pub stderr: String,
}

pub fn run_command(args: &[&str], cwd: &Path, verbose: Verbose) -> Result<Output> {
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
            println!("    {}", line.trim_end());
        }
        for line in output.stderr.lines() {
            println!("    {}", line.trim_end());
        }
    }

    if !res.status.success() {
        bail!(ErrorKind::ProcessFailed(output));
    }
    Ok(output)
}
