use errors::*;
use std::fs;
use std::path::PathBuf;

lazy_static! {
    static ref PIDFILE: PathBuf = PathBuf::from(".bunnybot.pid");
}

pub struct Pidfile;

impl Pidfile {
    pub fn new() -> Result<Self> {
        let open_result = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&*PIDFILE);
        if open_result.is_err() {
            bail!(ErrorKind::PidFileExists);
        }
        Ok(Pidfile {})
    }
}

impl Drop for Pidfile {
    fn drop(&mut self) {
        // We must never panic in drop.
        let _ = fs::remove_file(&*PIDFILE);
    }
}
