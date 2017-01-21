#![recursion_limit = "1024"]

#[macro_use]
extern crate error_chain;
extern crate bunnybot;

use bunnybot::errors::*;
use bunnybot::pidfile::Pidfile;

fn run() -> Result<()> {
    let _pidfile = Pidfile::new()?;

    println!("Hello, world!");
    Ok(())
}

quick_main!(run);
