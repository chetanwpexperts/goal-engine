use crate::model::Ssh;
use std::{process::{Child, Command, Stdio}, net::{TcpListener,TcpStream}, time::{Duration,Instant}, path::Path};

pub struct Tunnel(pub Child);
impl Drop for Tunnel { fn drop(&mut self) { let _ = self.0.kill(); let _ = self.0.wait(); } }
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
    let mut c = command();
    c.arg("-o").arg(format!("UserKnownHostsFile={}",known_hosts.display()))
        .args(["-o","GlobalKnownHostsFile=none","-i"]).arg(&s.key_path)
        .args(["-N","-L",&format!("127.0.0.1:{port}:127.0.0.1:{remote}"),"-l",&s.user,"--",&s.host])
        .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    let mut tunnel = Tunnel(c.spawn().map_err(|_| "Could not start SSH. Install OpenSSH.")?);
    let end = Instant::now()+Duration::from_secs(12);
    while Instant::now()<end {
        if tunnel.0.try_wait().map_err(|_| "Could not check SSH.")?.is_some() { return Err("SSH stopped. Check your key, host fingerprint and server access.".into()); }
        if TcpStream::connect_timeout(&([127,0,0,1],port).into(),Duration::from_millis(150)).is_ok() {
            std::thread::sleep(Duration::from_millis(200));
            if tunnel.0.try_wait().map_err(|_| "Could not check SSH.")?.is_none() { return Ok((port,tunnel)); }
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    Err("SSH tunnel did not start in time.".into())
}
