error_chain! {
    errors {
        PidFileExists {
            description("Another bunnybot is already running.")
        }
    }
}
