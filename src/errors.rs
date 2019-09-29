use subprocess::Output;

error_chain! {
    errors {
        PidFileExists {
            description("Another bunnybot is already running.")
        }

        Http(url: String) {
            description("HTTP request failed.")
            display("HTTP request for {} failed", url)
        }

        ProcessFailed(output: Output) {
            description("Process failed.")
            display("Output:\nstdout:\n{}\nstderr:\n{}\n", output.stdout, output.stderr)
        }

    }
}
