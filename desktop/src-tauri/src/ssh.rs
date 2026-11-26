use crate::model::Ssh;
use std::{process::{Child, Command, Stdio}, net::{TcpListener,TcpStream}, time::{Duration,Instant}, path::Path, io::{BufRead, BufReader}, sync::{Arc, Mutex}, thread::JoinHandle};

pub struct Tunnel(pub Child, Arc<Mutex<String>>, Option<JoinHandle<()>>);
impl Tunnel {
    fn finish_stderr(&mut self) {
        if let Some(reader) = self.2.take() { let _ = reader.join(); }
    }
    pub fn stopped_reason(&mut self) -> String {
        self.finish_stderr();
        let last = self.1.lock().unwrap();
        if last.is_empty() {
            "SSH stopped. Check your key, host fingerprint and server access.".into()
        } else {
            format!("SSH stopped: {last}")
        }
    }
}
impl Drop for Tunnel {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
        self.finish_stderr();
    }
}
fn known_hosts_option(path: &Path) -> Result<String, String> {
    let path = path.to_str().ok_or("Use a known hosts path with valid text.")?;
    if path.contains('"') || path.chars().any(char::is_control) {
        return Err("Use a known hosts path without quotes or control characters.".into());
    }
    // OpenSSH parses -o as configuration syntax, unlike the single argument to -i.
    Ok(format!("UserKnownHostsFile=\"{path}\""))
}
fn tunnel_command(s: &Ssh, port: u16, remote: u16, known_hosts: &Path) -> Result<Command,String> {
    let mut c = command();
    c.arg("-o").arg(known_hosts_option(known_hosts)?)
        .args(["-o","GlobalKnownHostsFile=none","-i"]).arg(&s.key_path)
        .args(["-N","-L",&format!("127.0.0.1:{port}:127.0.0.1:{remote}"),"-l",&s.user,"--",&s.host])
        .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::piped());
    Ok(c)
}
fn capture_stderr<R: std::io::Read + Send + 'static>(reader: R, last: Arc<Mutex<String>>) -> JoinHandle<()> {
    std::thread::spawn(move || {
        let mut reader = BufReader::new(reader);
        let mut line = Vec::new();
        loop {
            line.clear();
            match reader.read_until(b'\n', &mut line) {
                Ok(0) | Err(_) => break,
                Ok(_) => {
                    let text = String::from_utf8_lossy(&line);
                    let text = text.trim();
                    if !text.is_empty() {
                        *last.lock().unwrap() = text.chars().filter(|c| !c.is_control()).take(2048).collect();
                    }
                }
            }
        }
    })
}
#[cfg(test)]
#[path = "../../../tests/desktop/ssh.rs"]
mod tests;
fn command() -> Command {
    let mut c = Command::new("ssh");
    c.args(["-F", if cfg!(windows) { "NUL" } else { "/dev/null" }, "-o", "StrictHostKeyChecking=yes",
        "-o", "BatchMode=yes", "-o", "PasswordAuthentication=no", "-o", "KbdInteractiveAuthentication=no",
        "-o", "ExitOnForwardFailure=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=2", "-o", "ForwardAgent=no", "-o", "ControlMaster=no"]);
    c
}
pub fn scan(s: &Ssh) -> Result<(String,String),String> {
    let out = Command::new("ssh-keyscan").args(["-T","5","-t","ed25519", &s.host]).output().map_err(|_| "OpenSSH tools are needed.")?;
    let raw = String::from_utf8(out.stdout).map_err(|_| "Invalid host key.")?;
    let rows: Vec<_> = raw.lines().filter(|l| !l.starts_with('#') && !l.trim().is_empty()).collect();
    if rows.len() != 1 { return Err("Could not find one Ed25519 host key. Ask your server administrator.".into()); }
    let fields: Vec<_> = rows[0].split_whitespace().collect();
    if fields.len()!=3 || fields[1]!="ssh-ed25519" || !fields[2].bytes().all(|b| b.is_ascii_alphanumeric() || b"+/=".contains(&b)) {
        return Err("Invalid host key.".into());
    }
    let line = format!("{} {} {}\n",s.host,fields[1],fields[2]);
    let file = tempfile::NamedTempFile::new().map_err(|_| "Could not check host key.")?;
    std::fs::write(file.path(), &line).map_err(|_| "Could not check host key.")?;
    let fp = Command::new("ssh-keygen").args(["-l","-E","sha256","-f"]).arg(file.path()).output().map_err(|_| "OpenSSH tools are needed.")?;
    if !fp.status.success() { return Err("Could not check host key.".into()); }
    Ok((line,String::from_utf8_lossy(&fp.stdout).trim().to_string()))
}
pub fn known(host: &str, path: &Path) -> bool {
    Command::new("ssh-keygen").args(["-F",host,"-f"]).arg(path).stdout(Stdio::null()).stderr(Stdio::null()).status().map(|s|s.success()).unwrap_or(false)
}
pub fn start(s: &Ssh, remote: u16, known_hosts: &Path) -> Result<(u16,Tunnel),String> {
    if !known(&s.host, known_hosts) { return Err("Verify the server fingerprint first.".into()); }
    let listener = TcpListener::bind(("127.0.0.1",0)).map_err(|_| "Could not reserve a local port.")?;
    let port = listener.local_addr().map_err(|_| "Could not reserve a local port.")?.port();
    drop(listener);
    let mut child = tunnel_command(s,port,remote,known_hosts)?.spawn().map_err(|_| "Could not start SSH. Install OpenSSH.")?;
    let stderr = child.stderr.take().expect("SSH stderr is piped");
    let last = Arc::new(Mutex::new(String::new()));
    let reader = capture_stderr(stderr, last.clone());
    let mut tunnel = Tunnel(child, last, Some(reader));
    let end = Instant::now()+Duration::from_secs(12);
    while Instant::now()<end {
        if tunnel.0.try_wait().map_err(|_| "Could not check SSH.")?.is_some() { return Err(tunnel.stopped_reason()); }
        if TcpStream::connect_timeout(&([127,0,0,1],port).into(),Duration::from_millis(150)).is_ok() {
            std::thread::sleep(Duration::from_millis(200));
            if tunnel.0.try_wait().map_err(|_| "Could not check SSH.")?.is_none() { return Ok((port,tunnel)); }
            return Err(tunnel.stopped_reason());
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    let _ = tunnel.0.kill();
    let _ = tunnel.0.wait();
    tunnel.finish_stderr();
    let last = tunnel.1.lock().unwrap();
    if last.is_empty() { Err("SSH tunnel did not start in time.".into()) }
    else { Err(format!("SSH tunnel did not start in time: {last}")) }
}
