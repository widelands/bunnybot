-- Watcher file for use with shell_grunt2.
-- https://github.com/SirVer/shell_grunt2

return {
  {
    commands = {
      {
        name = "Cargo check",
        command = "cargo check --color=always",
      },
      {
        name = "Cargo build [debug]",
        command = "cargo build --color=always",
      },
      {
        name = "Cargo clippy",
        command = "cargo clippy --color=always",
      },
    },
    should_run = function(p)
      if p:find("target") ~= nil then return false end
      return p:ext() == "rs" or p:ext() == "toml"
    end,
    redirect_stderr = "/tmp/cargo.err",
    start_delay = 50,
  },
}
