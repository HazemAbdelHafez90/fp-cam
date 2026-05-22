import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("CAM_AUTH_PASSWORD", "let-me-in")
    monkeypatch.setenv("CAM_SESSION_SECRET", "test-session-secret")
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("CAM_AUTH_COOKIE_SECURE", raising=False)

    import cam_app

    importlib.reload(cam_app)
    return TestClient(cam_app.app)


def test_root_redirects_to_login_without_session(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_api_returns_401_without_session(client, monkeypatch):
    import cam_app

    def fail_if_called():
        raise AssertionError("Canto should not be called without auth")

    monkeypatch.setattr(cam_app.canto, "get_folder_tree", fail_if_called)
    response = client.get("/api/projects")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_login_page_is_public(client):
    response = client.get("/login")

    assert response.status_code == 200
    assert "CAM" in response.text
    assert "password" in response.text.lower()


def test_static_app_shell_redirects_without_session(client):
    response = client.get("/static/index.html", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_stylesheet_is_public(client):
    response = client.get("/static/colors_and_type.css")

    assert response.status_code == 200


def test_static_font_encoded_traversal_redirects_without_session(client):
    response = client.get("/static/fonts/%2e%2e/index.html", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_wrong_password_is_rejected(client):
    response = client.post("/login", data={"password": "wrong-password"}, follow_redirects=False)

    assert response.status_code == 401
    assert "Invalid password" in response.text
    assert "cam_session" not in response.cookies


def test_correct_password_sets_cookie_and_redirects(client):
    response = client.post("/login", data={"password": "let-me-in"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "cam_session" in response.cookies
    assert response.cookies["cam_session"]


def test_authenticated_root_loads_index(client):
    login = client.post("/login", data={"password": "let-me-in"}, follow_redirects=False)

    assert login.status_code == 303
    assert "cam_session" in login.cookies

    response = client.get("/", cookies=login.cookies)

    assert response.status_code == 200
    assert "Consent Asset Matcher" in response.text


def test_authenticated_api_reaches_route(client, monkeypatch):
    import cam_app

    monkeypatch.setattr(cam_app.canto, "get_folder_tree", lambda: [])
    login = client.post("/login", data={"password": "let-me-in"}, follow_redirects=False)

    assert login.status_code == 303
    assert "cam_session" in login.cookies

    response = client.get("/api/projects", cookies=login.cookies)

    assert response.status_code == 200
    assert response.json() == []


def test_logout_clears_cookie(client):
    login = client.post("/login", data={"password": "let-me-in"}, follow_redirects=False)

    assert login.status_code == 303
    assert "cam_session" in login.cookies

    response = client.get("/logout", cookies=login.cookies, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    set_cookie = response.headers.get("set-cookie", "")
    assert "cam_session=" in set_cookie
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()


def test_logout_clears_cookie_without_session(client):
    response = client.get("/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    set_cookie = response.headers.get("set-cookie", "")
    assert "cam_session=" in set_cookie
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()
