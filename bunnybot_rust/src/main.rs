#![recursion_limit = "1024"]

#[cfg(target_os = "linux")]
extern crate scheduler;
#[macro_use]
extern crate error_chain;
#[macro_use]
extern crate serde_derive;
extern crate bunnybot;
extern crate reqwest;
extern crate clap;

use bunnybot::errors::*;
use bunnybot::pidfile::Pidfile;
use std::path::Path;

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

    let _pidfile = Pidfile::new()?;
    set_nice_level()?;

    let merge_proposals = bunnybot::launchpad::get_merge_proposals("~widelands-dev/widelands/trunk")?;
    for m in merge_proposals {
        println!("===> Working on {} -> {}",
                 m.source_branch.unique_name,
                 m.target_branch.unique_name);

        // NOCOM(#sirver): this is the slowest part here.
        let state = m.source_branch.travis_state();
        println!("#sirver state: {:#?}", state);

        let state = m.source_branch.appveyor_state();
        println!("#sirver state: {:#?}", state);


        let mut update = always_update;
        if let bunnybot::launchpad::WasUpdated::Yes(_) = m.source_branch.update(&bzr_repo)? {
            update = true;
        }
        if update {
            m.source_branch.update_git(&git_repo)?
        }
        println!("\n");
    }
    Ok(())
}

quick_main!(run);
