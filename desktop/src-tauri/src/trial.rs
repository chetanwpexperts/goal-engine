//! Native loopback-only client for the desktop trial.
use serde_json::{json, Value};
use std::{io::{BufRead, BufReader}, net::TcpListener, path::Path, process::{Child, Command, Stdio}, sync::{Mutex, mpsc}, time::{Duration, Instant}};
use tauri::{Manager, State, WebviewWindow};

struct Engine { child: Child, port: u16, token: Option<String> }
impl Drop for Engine {
    fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); }
}
#[derive(Default)]
pub struct Trial(Mutex<Option<Engine>>);
impl Trial {
    pub fn stop(&self) { if let Ok(mut slot) = self.0.lock() { *slot = None; } }
}
fn credential(name: &str) -> Result<keyring::Entry, String> {
    keyring::Entry::new("com.dbaba.desktop.trial", name).map_err(|_| "OS keychain unavailable.".into())
}
fn check_window(window: &WebviewWindow) -> Result<(), String> {
    if window.label() == "main" { Ok(()) } else { Err("Only the local desktop can do this.".into()) }
}
fn client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder().no_proxy().redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(10)).build().map_err(|_| "Could not create local client.".into())
}
async fn request(port: u16, method: reqwest::Method, path: &str, token: Option<&str>, key: Option<&str>, body: Option<&Value>) -> Result<Value, String> {
    let mut req = client()?.request(method, format!("http://127.0.0.1:{port}{path}"));
    if let Some(t) = token { req = req.header("X-DBaba-Setup", t); }
    if let Some(k) = key { req = req.bearer_auth(k); }
    if let Some(b) = body { req = req.json(b); }
    let res = req.send().await.map_err(|_| "Cannot reach local DBaba.".to_string())?;
    if !res.status().is_success() { return Err(format!("Local DBaba returned HTTP {}.", res.status().as_u16())); }
    res.json().await.map_err(|_| "Invalid local DBaba response.".into())
}
fn spawn_engine(dir: &Path) -> Result<(Engine, mpsc::Receiver<String>), String> {
    std::fs::create_dir_all(dir).map_err(|_| "Cannot create app data directory.")?;
    let listener = TcpListener::bind(("127.0.0.1", 0)).map_err(|_| "Cannot allocate local port.")?;
    let port = listener.local_addr().map_err(|_| "Cannot allocate local port.")?.port();
    drop(listener);
    let mut command = Command::new("dbaba");
    command.arg("serve").arg("--port").arg(port.to_string()).arg("--config").arg(dir.join("dbaba.json"))
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)]
    { use std::os::windows::process::CommandExt; command.creation_flags(0x08000000); }
    let child = command.spawn().map_err(|e| if e.kind() == std::io::ErrorKind::NotFound {
        "DBaba engine is not installed. Install it with pip install . from the DBaba repository, or use deploy/onprem/install.sh on Linux. Then restart this app."
    } else { "Could not start the local DBaba engine." })?;
    let mut engine = Engine { child, port, token: None };
    let stdout = engine.child.stdout.take().ok_or("Cannot read local engine startup.")?;
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Some(token) = line.strip_prefix("DBABA-SETUP-TOKEN ") {
                if !token.is_empty() && token.len() <= 4096 && token.bytes().all(|b| b.is_ascii_graphic()) {
                    let _ = tx.send(token.to_owned());
                }
            }
        }
    });
    Ok((engine, rx))
}
#[tauri::command]
pub async fn trial_start(window: WebviewWindow, app: tauri::AppHandle, trial: State<'_, Trial>) -> Result<Value, String> {
    check_window(&window)?;
    if trial.0.lock().map_err(|_| "Local engine lock failed.")?.is_some() { return Err("Local engine is already running.".into()); }
    let dir = app.path().app_data_dir().map_err(|_| "Cannot locate app data.")?;
    let (mut engine, rx) = spawn_engine(&dir)?;
    let deadline = Instant::now() + Duration::from_secs(60);
    let mut healthy = false;
    while Instant::now() < deadline {
        if engine.child.try_wait().map_err(|_| "Cannot check local engine.")?.is_some() { return Err("Local DBaba stopped during startup.".into()); }
        if let Ok(t) = rx.try_recv() { engine.token = Some(t); }
        let remaining = deadline.saturating_duration_since(Instant::now());
        if let Ok(Ok(h)) = tokio::time::timeout(remaining.min(Duration::from_secs(2)), request(engine.port, reqwest::Method::GET, "/api/health", None, None, None)).await {
            if h["ok"] == true { healthy = true; break; }
        }
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
    if !healthy { return Err("Local DBaba did not become healthy within 60 seconds.".into()); }
    if let Ok(t) = rx.try_recv() { engine.token = Some(t); }
    let key = credential("api-key")?.get_password().ok();
    let state = request(engine.port, reqwest::Method::GET, "/api/state", engine.token.as_deref(), key.as_deref(), None).await?;
    let done = state["setup_done"].as_bool().ok_or("Invalid setup state.")?;
    if !done && engine.token.is_none() { return Err("Local DBaba did not provide a setup token.".into()); }
    if done { engine.token = None; }
    *trial.0.lock().map_err(|_| "Local engine lock failed.")? = Some(engine);
    Ok(json!({"setup_done": done}))
}
#[tauri::command]
pub fn trial_stop(window: WebviewWindow, trial: State<'_, Trial>) -> Result<(), String> {
    check_window(&window)?; trial.stop(); Ok(())
}
fn database_body(input: &Value, save: bool) -> Result<Value, String> {
    let mut body = serde_json::Map::new();
    for (name, limit) in [("engine", 16), ("host", 253), ("database", 255), ("user", 255), ("password", 4096)] {
        let s = input[name].as_str().ok_or("Missing database field.")?;
        if s.len() > limit || (name != "password" && s.is_empty()) || s.contains('\0') { return Err("Invalid database field.".into()); }
        body.insert(name.into(), json!(s));
    }
    if !matches!(input["engine"].as_str(), Some("mysql" | "mariadb" | "postgresql")) { return Err("Unsupported database engine.".into()); }
    let port = input["port"].as_u64().filter(|p| (1..=65535).contains(p)).ok_or("Invalid database port.")?;
    body.insert("port".into(), json!(port));
    if save {
        let s = input["screen_password"].as_str().filter(|s| s.chars().count() >= 8 && s.len() <= 4096 && !s.contains('\0')).ok_or("Invalid screen password.")?;
        body.insert("screen_password".into(), json!(s));
    }
    Ok(Value::Object(body))
}
fn valid_id(id: &str) -> Result<(), String> {
    if id.is_empty() || id.len() > 128 || !id.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_') { return Err("Invalid identifier.".into()); }
    Ok(())
}
fn job_path(id: &str) -> Result<String, String> {
    valid_id(id)?;
    Ok(format!("/api/jobs/{id}"))
}
fn alert_path(id: &str) -> Result<String, String> {
    valid_id(id)?;
    Ok(format!("/api/alerts/{id}/ack"))
}
#[tauri::command]
pub async fn trial_call(window: WebviewWindow, trial: State<'_, Trial>, action: String, input: Option<Value>) -> Result<Value, String> {
    check_window(&window)?;
    let (port, token) = {
        let guard = trial.0.lock().map_err(|_| "Local engine lock failed.")?;
        let engine = guard.as_ref().ok_or("Start local DBaba first.")?;
        (engine.port, engine.token.clone())
    };
    let input = input.unwrap_or(Value::Null);
    let setup = matches!(action.as_str(), "detect" | "test" | "save");
    if setup && token.is_none() { return Err("Setup is already complete.".into()); }
    if !setup && token.is_some() { return Err("Finish setup first.".into()); }
    if action == "report_html" {
        let key = credential("api-key")?.get_password().map_err(|_| "API key unavailable in OS keychain.")?;
        let res = client()?.get(format!("http://127.0.0.1:{port}/api/report.html"))
            .bearer_auth(key).send().await.map_err(|_| "Cannot reach local DBaba.")?;
        if !res.status().is_success() { return Err(format!("Local DBaba returned HTTP {}.", res.status().as_u16())); }
        let data = res.bytes().await.map_err(|_| "Could not read HTML report.")?;
        let saved = tauri::async_runtime::spawn_blocking(move || -> Result<bool, String> {
            let Some(path) = rfd::FileDialog::new().set_file_name("dbaba-report.html")
                .add_filter("HTML report", &["html"]).save_file() else { return Ok(false); };
            let parent = path.parent().ok_or("Invalid save location.")?;
            let mut file = tempfile::NamedTempFile::new_in(parent).map_err(|_| "Could not create report file.")?;
            use std::io::Write;
            file.write_all(&data).and_then(|_| file.as_file().sync_all()).map_err(|_| "Could not write report.")?;
            file.persist(path).map_err(|_| "Could not save report.")?;
            Ok(true)
        }).await.map_err(|_| "Could not open save dialog.")??;
        return Ok(json!({"saved":saved}));
    }
    let (method, path, body) = match action.as_str() {
        "detect" => (reqwest::Method::POST, "/api/setup/detect".into(), None),
        "test" | "save" => (reqwest::Method::POST, format!("/api/setup/{action}"), Some(database_body(&input, action == "save")?)),
        "state" | "report" | "jobs" => (reqwest::Method::GET, format!("/api/{action}"), None),
        "scan" => (reqwest::Method::POST, "/api/report/scan".into(), None),
        "new_chat" => (reqwest::Method::POST, "/api/new-chat".into(), None),
        "message" => {
            let text = input["text"].as_str().filter(|s| !s.trim().is_empty() && s.len() <= 10000).ok_or("Enter a shorter message.")?;
            (reqwest::Method::POST, "/api/message".into(), Some(json!({"text":text})))
        },
        "ack" => (reqwest::Method::POST, alert_path(input["id"].as_str().ok_or("Missing alert ID.")?)?, None),
        "job" => {
            let id = input["id"].as_str().ok_or("Missing job ID.")?;
            let enabled = input["enabled"].as_bool().ok_or("Missing job setting.")?;
            let at = &input["at"];
            if !at.is_null() {
                let s = at.as_str().ok_or("Invalid job time.")?;
                let b = s.as_bytes();
                if b.len() != 5 || b[2] != b':' || ![b[0],b[1],b[3],b[4]].iter().all(u8::is_ascii_digit) || s[..2].parse::<u8>().unwrap_or(99)>23 || s[3..].parse::<u8>().unwrap_or(99)>59 { return Err("Invalid job time.".into()); }
            }
            (reqwest::Method::POST, job_path(id)?, Some(json!({"enabled":enabled,"at":at})))
        },
        _ => return Err("Unknown local action.".into())
    };
    let key = if setup { None } else { Some(credential("api-key")?.get_password().map_err(|_| "API key unavailable in OS keychain.")?) };
    let mut result = request(port, method, &path, if setup {token.as_deref()} else {None}, key.as_deref(), body.as_ref()).await?;
    if action == "save" && result["ok"] == true {
        let k = result["api_key"].as_str().filter(|k| !k.is_empty()).ok_or("Missing API key in setup response.")?;
        // The backend has accepted setup. Keep the UI in setup if either credential write fails.
        credential("api-key")?.set_password(k).map_err(|_| "Backend saved setup, but the API key could not be saved in the OS keychain. Setup is not complete on this desktop; contact your administrator for recovery.")?;
        credential("database-password")?.set_password(input["password"].as_str().unwrap()).map_err(|_| "Backend saved setup, but the database password could not be saved in the OS keychain. Setup is not complete on this desktop; check your keychain before restarting.")?;
        if let Some(engine) = trial.0.lock().map_err(|_| "Local engine lock failed.")?.as_mut() { engine.token = None; }
        result = json!({"ok":true,"warning":result["warning"]});
    }
    // Never return raw setup-save responses to the webview.
    if action == "save" { return Ok(json!({"ok":result["ok"] == true,"reason":result["reason"],"warning":result["warning"]})); }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn validation() {
        let body = json!({"engine":"mysql","host":"127.0.0.1","port":3306,"database":"sample","user":"sample","password":"sample"});
        assert!(database_body(&body,false).is_ok());
        assert!(database_body(&body,true).is_err());
        assert!(job_path("../x").is_err());
        assert!(alert_path("../x").is_err());
        assert_eq!(alert_path("a-1").unwrap(), "/api/alerts/a-1/ack");
        assert_eq!(job_path("scan").unwrap(),"/api/jobs/scan");
    }
}
