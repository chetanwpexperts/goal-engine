use super::*;
#[test]
fn paths_with_spaces_are_single_arguments() {
    let root = tempfile::tempdir().unwrap();
    let hosts = root.path().join("Application Support").join("known_hosts");
    let key = root.path().join("SSH Keys").join("id_ed25519");
    let s = Ssh { host: "server.example".into(), user: "operator".into(), key_path: key.to_str().unwrap().into() };
    let c = tunnel_command(&s, 12345, 8765, &hosts).unwrap();
    let args: Vec<_> = c.get_args().map(|a| a.to_string_lossy().into_owned()).collect();
    assert!(args.windows(2).any(|p| p[0] == "-o" && p[1] == format!("UserKnownHostsFile=\"{}\"", hosts.display())));
    assert!(args.windows(2).any(|p| p[0] == "-i" && p[1] == s.key_path));
    assert!(args.windows(2).any(|p| p[0] == "-o" && p[1] == "StrictHostKeyChecking=yes"));
}
#[test]
fn captures_last_nonempty_stderr_line() {
    let last = Arc::new(Mutex::new(String::new()));
    capture_stderr(std::io::Cursor::new(b"first line\nHost key verification failed.\n\n"), last.clone()).join().unwrap();
    assert_eq!(*last.lock().unwrap(), "Host key verification failed.");
}
