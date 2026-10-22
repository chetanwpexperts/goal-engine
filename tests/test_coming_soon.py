"""Before launch: everyone (and every bot) sees the coming-soon page; only the private preview link gets in."""


def test_closed_site_shows_coming_soon_and_blocks_api(client, ml_mode, monkeypatch):
    monkeypatch.setenv("SITE_MODE", "closed")
    monkeypatch.setenv("PREVIEW_KEY", "k" * 24)
    home = client.get("/")
    assert home.status_code == 200 and "Jald aa raha hai" in home.text and "noindex" in home.headers["x-robots-tag"]
    assert client.get("/privacy").text.count("Jald aa raha hai") == 1
    r = client.post("/api/message", json={"text": "hello"})
    assert r.status_code == 503
    assert "Disallow: /" in client.get("/robots.txt").text
    assert client.get("/api/health").status_code == 200                      # deploy check still works
    assert client.post("/api/connector/poll", headers={"Authorization": "Bearer gec_x"}).status_code == 401   # connectors still answer

    client.get("/preview?key=wrong", follow_redirects=False)
    assert client.post("/api/message", json={"text": "hello"}).status_code == 503
    assert client.get(f"/preview?key={'k' * 24}", follow_redirects=False).status_code == 303
    assert "Jald aa raha hai" not in client.get("/").text
    assert client.post("/api/message", json={"text": "meri website slow hai"}).status_code == 200
    client.get("/preview?off=1")
    assert "Jald aa raha hai" in client.get("/").text


def test_no_preview_key_means_nobody_gets_in(client, monkeypatch):
    monkeypatch.setenv("SITE_MODE", "closed")
    monkeypatch.delenv("PREVIEW_KEY", raising=False)
    client.get("/preview?key=", follow_redirects=False)
    assert "Jald aa raha hai" in client.get("/").text
