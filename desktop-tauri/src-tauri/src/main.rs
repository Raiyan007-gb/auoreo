// EXPERIMENTAL Tauri v2 shell for AUOREO (F7 spike, docs/spikes/tauri.md); Electron in
// desktop/ stays the default. Same contract as desktop/main.cjs: spawn `auoreo serve --port 0`
// with a per-launch AUOREO_SERVER_PASSWORD, wait for the `server listening on <url>` line, then
// open a window on `<url>/sessions` (no Tauri IPC is exposed to the remote page).
//
// Gap vs Electron (see the decision doc): Electron sets an HttpOnly `auoreo_token` cookie
// before loading the page; this scaffold has no confirmed Tauri v2 API for that, so the
// window is unauthenticated until a one-time login exchange (or a cookie API) is added.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::{mpsc, Mutex};
use std::time::Duration;

use rand::RngCore;
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

const START_TIMEOUT: Duration = Duration::from_secs(60);

struct Server(Mutex<Option<Child>>);

fn token() -> String {
    let mut bytes = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut bytes);
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// `$AUOREO_BINARY`, else `auoreo` on PATH (a bundled sidecar would go in `externalBin`).
fn binary() -> String {
    std::env::var("AUOREO_BINARY").unwrap_or_else(|_| "auoreo".to_string())
}

/// First `http(s)://...` after `server listening on ` (mirrors desktop/lib.cjs parseListening).
fn parse_listening(line: &str) -> Option<String> {
    let rest = line.split("server listening on ").nth(1)?;
    let url: String = rest.chars().take_while(|c| !c.is_whitespace() && *c != '(' && *c != ')').collect();
    let url = url.trim_end_matches(['.', ',', ';']).to_string();
    (url.starts_with("http://") || url.starts_with("https://")).then_some(url)
}

fn start_server(password: &str) -> Result<(Child, String), String> {
    let mut child = Command::new(binary())
        .args(["serve", "--port", "0"])
        .env("AUOREO_SERVER_PASSWORD", password)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("cannot start auoreo: {e}"))?;
    let (tx, rx) = mpsc::channel::<String>();
    for pipe in [child.stdout.take().map(|p| Box::new(p) as Box<dyn std::io::Read + Send>),
                 child.stderr.take().map(|p| Box::new(p) as Box<dyn std::io::Read + Send>)]
        .into_iter()
        .flatten()
    {
        let tx = tx.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(pipe).lines().map_while(Result::ok) {
                println!("[auoreo] {line}");
                if let Some(url) = parse_listening(&line) {
                    let _ = tx.send(url);
                }
            }
        });
    }
    match rx.recv_timeout(START_TIMEOUT) {
        Ok(url) => Ok((child, url)),
        Err(_) => {
            let _ = child.kill();
            Err(format!("server did not start within {}s", START_TIMEOUT.as_secs()))
        }
    }
}

fn main() {
    let password = token();
    let (child, url) = start_server(&password).unwrap_or_else(|e| panic!("{e}"));
    let target: tauri::Url = format!("{url}/sessions").parse().expect("server url");
    let app = tauri::Builder::default()
        .manage(Server(Mutex::new(Some(child))))
        .setup(move |app| {
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(target.clone()))
                .title("AUOREO")
                .inner_size(1280.0, 840.0)
                .min_inner_size(720.0, 480.0)
                .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the Tauri app");
    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(mut child) = handle.state::<Server>().0.lock().ok().and_then(|mut g| g.take()) {
                let _ = child.kill(); // SIGKILL; Electron sends SIGTERM first (std has no portable SIGTERM)
                let _ = child.wait();
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::parse_listening;

    #[test]
    fn parses_the_discovery_line() {
        assert_eq!(
            parse_listening("server listening on http://127.0.0.1:5123 (2 workers; x)").as_deref(),
            Some("http://127.0.0.1:5123")
        );
        assert_eq!(parse_listening("server listening on http://127.0.0.1:9."), Some("http://127.0.0.1:9".into()));
        assert_eq!(parse_listening("nothing here"), None);
    }
}
