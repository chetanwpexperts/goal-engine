use super::*;
#[test]
fn storage_roundtrip() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("connections.json");
    assert!(load(&path).unwrap().is_empty());
    let row = Connection { id: Uuid::new_v4(), label: "Local".into(), port: 8765, ssh: None, seen: BTreeSet::from(["alert-1".into()]) };
    save(&path, &[row.clone()]).unwrap();
    let rows = load(&path).unwrap();
    assert_eq!(rows[0].id, row.id);
    assert_eq!(rows[0].seen, row.seen);
    assert!(!std::fs::read_to_string(path).unwrap().contains("api_key"));
}
#[test]
fn url_rules() {
    for (s, expected) in [
        ("http://127.0.0.1:8765/chat", true),
        ("http://127.0.0.1:8766/", false),
        ("https://127.0.0.1:8765/", false),
        ("http://127.0.0.1.example.org:8765/", false),
        ("http://127.0.0.1:8765@other.test/", false),
        ("http://user@127.0.0.1:8765/", false),
        ("file:///etc/passwd", false),
        ("javascript:alert(1)", false),
    ] { assert_eq!(same_origin(&Url::parse(s).unwrap(), 8765), expected, "{s}"); }
    assert!(!external(&Url::parse("file:///etc/passwd").unwrap()));
}
#[test]
fn alerts_are_optional_and_deduplicated() {
    assert!(serde_json::from_str::<ApiState>("{}").unwrap().alerts.is_empty());
    let mut a = Alert { id:"a".into(), level:"warning".into(), title:"Test".into(), text:"Test".into(), at:"2026-01-01T00:00:00Z".into(), acknowledged:false };
    assert!(eligible(&a, &BTreeSet::new()));
    assert!(!eligible(&a, &BTreeSet::from(["a".into()])));
    a.acknowledged = true;
    assert!(!eligible(&a, &BTreeSet::new()));
}
