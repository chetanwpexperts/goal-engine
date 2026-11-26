use serde::{Deserialize, Serialize};
use std::{collections::BTreeSet, path::Path};
use url::Url;
use uuid::Uuid;

#[derive(Clone, Serialize, Deserialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct Connection {
    pub id: Uuid,
    pub label: String,
    pub port: u16,
    pub ssh: Option<Ssh>,
    #[serde(default)]
    pub seen: BTreeSet<String>,
}
#[derive(Clone, Serialize, Deserialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct Ssh { pub host: String, pub user: String, pub key_path: String }

fn token(s: &str, host: bool) -> bool {
    !s.is_empty() && s.len() <= 253 && !s.starts_with('-') &&
        s.bytes().all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b) || (host && b == b':'))
}
impl Connection {
    pub fn validate(&self) -> Result<(), String> {
        if self.label.trim().is_empty() || self.label.len() > 120 || self.port == 0 {
            return Err("Enter a name and a valid port.".into());
        }
        if let Some(s) = &self.ssh {
            if !token(&s.host, true) || !token(&s.user, false) ||
                s.key_path.len() > 4096 || !Path::new(&s.key_path).is_absolute() ||
                s.key_path.chars().any(char::is_control) {
                return Err("Enter a valid SSH host, user and absolute key path.".into());
            }
        }
        Ok(())
    }
}
pub fn base(port: u16) -> Url {
    Url::parse(&format!("http://127.0.0.1:{port}/")).unwrap()
}
pub fn same_origin(url: &Url, port: u16) -> bool {
    url.scheme() == "http" && url.host_str() == Some("127.0.0.1") &&
        url.port_or_known_default() == Some(port) && url.username().is_empty() && url.password().is_none()
}
pub fn external(url: &Url) -> bool {
    matches!(url.scheme(), "http" | "https" | "mailto") &&
        url.username().is_empty() && url.password().is_none()
}
pub fn load(path: &Path) -> Result<Vec<Connection>, String> {
    if !path.exists() { return Ok(vec![]); }
    let rows: Vec<Connection> = serde_json::from_slice(&std::fs::read(path).map_err(|_| "Could not read connections.")?)
        .map_err(|_| "Could not read saved connections.")?;
    let mut ids = BTreeSet::new();
    for row in &rows {
        row.validate()?;
        if !ids.insert(row.id) { return Err("Duplicate connection.".into()); }
    }
    Ok(rows)
}
pub fn save(path: &Path, rows: &[Connection]) -> Result<(), String> {
    let parent = path.parent().ok_or("Missing settings folder.")?;
    std::fs::create_dir_all(parent).map_err(|_| "Could not create settings folder.")?;
    let mut f = tempfile::NamedTempFile::new_in(parent).map_err(|_| "Could not save connections.")?;
    serde_json::to_writer(&mut f, rows).map_err(|_| "Could not save connections.")?;
    f.as_file().sync_all().map_err(|_| "Could not save connections.")?;
    f.persist(path).map_err(|_| "Could not replace connections.")?;
    Ok(())
}
#[derive(Deserialize)]
pub struct Alert { pub id: String, pub level: String, pub title: String, pub text: String, pub at: String, pub acknowledged: bool }
#[derive(Deserialize)]
pub struct ApiState { #[serde(default)] pub alerts: Vec<Alert> }
pub fn eligible(alert: &Alert, seen: &BTreeSet<String>) -> bool {
    !alert.id.is_empty() && !alert.acknowledged && matches!(alert.level.as_str(), "warning" | "critical") && !seen.contains(&alert.id)
}
#[cfg(test)]
#[path = "../../../..//tests/desktop/model.rs"]
mod tests;
