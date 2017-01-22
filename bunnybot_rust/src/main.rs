#![recursion_limit = "1024"]

#[cfg(target_os = "linux")]
extern crate scheduler;
#[macro_use]
extern crate error_chain;
#[macro_use]
extern crate serde_derive;
extern crate bunnybot;
extern crate reqwest;

use bunnybot::errors::*;
use bunnybot::pidfile::Pidfile;
use std::collections::HashMap;
use std::io::Read;

#[cfg(target_os = "linux")]
fn set_nice_level() -> Result<()> {
    scheduler.set_self_priority(10)?;
}

#[cfg(not(target_os = "linux"))]
fn set_nice_level() -> Result<()> {
    Ok(())
}

fn run() -> Result<()> {

    let _pidfile = Pidfile::new()?;
    set_nice_level()?;

    let merge_proposals = bunnybot::launchpad::get_merge_proposals("~widelands-dev/widelands/trunk")?;
        // .unwrap();
    println!("#sirver merge_proposals: {:#?}", merge_proposals);

    // let mut map = HashMap::new();
    // map.insert("ws.op", "getMergeProposals");

    // let client = reqwest::Client::new().unwrap();
    // let mut res = client.post("https://api.launchpad.net/1.0/~widelands-dev/widelands/trunk")
    // .json(&map)
    // .send().unwrap();
    // let mut s = String::new();
    // res.read_to_string(&mut s).unwrap();
    // println!("#sirver s: {:#?}", s);
    Ok(())
}

quick_main!(run);
