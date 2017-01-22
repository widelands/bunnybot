use reqwest;

error_chain! {
    errors {
        PidFileExists {
            description("Another bunnybot is already running.")
        }

        Http(url: String) {
            description("HTTP request failed.")
            display("HTTP request for {} failed", url)
        }
    }
}
