from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from alayout import agentbuild
from alayout.agentbuild import (AgentError, AgentTools, SigningKey, agent_apk, build_agent,
                                signing_keys, signing_override)

PACKAGE = "com.example.app"


def fake_tools(tmp_path: Path) -> AgentTools:
    """Tools whose files exist (jar()/sdk look-ups are real), but which are never really run."""
    build_tools = tmp_path / "build-tools"
    build_tools.mkdir()
    for name in ("d8.jar", "apksigner.jar", "aapt2", "zipalign"):
        (build_tools / name).write_text("x", encoding="utf-8")
    android_jar = tmp_path / "android.jar"
    android_jar.write_text("x", encoding="utf-8")
    return AgentTools(java=Path("java"), javac=Path("javac"), aapt2=build_tools / "aapt2",
                      zipalign=build_tools / "zipalign", android_jar=android_jar)


def make_run(captured: list[list[str]]):
    """A runner that fakes each tool's side effect so build_agent can finish without an SDK."""
    def run(args, timeout):  # noqa: ANN001
        args = [str(a) for a in args]
        captured.append(args)
        if "-genkeypair" in args:
            Path(args[args.index("-keystore") + 1]).write_text("ks", encoding="utf-8")
        elif "--output" in args:  # d8
            Path(args[args.index("--output") + 1], "classes.dex").write_bytes(b"dex")
        elif "link" in args:  # aapt2
            with zipfile.ZipFile(args[args.index("-o") + 1], "w") as z:
                z.writestr("AndroidManifest.xml", "x")
        elif "sign" in args:  # apksigner
            Path(args[-1]).replace(args[args.index("--out") + 1])
        elif str(args[0]).endswith(("zipalign", "zipalign.exe")):
            Path(args[-1]).write_bytes(Path(args[-2]).read_bytes())
        return b""
    return run


def test_agent_apk_lives_in_a_per_package_folder(tmp_path):
    apk = agent_apk(PACKAGE, tmp_path, "debug")
    assert apk == tmp_path / PACKAGE / "agent-debug.apk"


def test_signing_override_returns_none_without_a_keystore():
    assert signing_override(None) is None


def test_signing_override_splits_store_and_key_passwords():
    key = signing_override("my.keystore", alias="release", key_password="keypw",
                            store_password="storepw")
    assert key == SigningKey(Path("my.keystore"), "release", "storepw", "keypw", "custom")


def test_signing_override_store_password_defaults_to_key_password():
    key = signing_override("my.keystore", key_password="shared")
    assert key.store_password == "shared" and key.key_password == "shared"


def test_signing_keys_prefers_an_explicit_override():
    override = SigningKey(Path("o.ks"), "a", "s", "k", "custom")
    assert signing_keys(override=override) == [override]


def test_signing_keys_env_keystore_splits_passwords(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    env = {"ALAYOUT_KEYSTORE": "env.ks", "ALAYOUT_KEY_ALIAS": "al",
           "ALAYOUT_KEY_PASSWORD": "kp", "ALAYOUT_KEY_STORE_PASSWORD": "sp"}
    keys = signing_keys(cache=tmp_path, env=env)
    assert keys == [SigningKey(Path("env.ks"), "al", "sp", "kp", "custom")]


def test_signing_keys_env_store_password_defaults_to_key_password(tmp_path):
    env = {"ALAYOUT_KEYSTORE": "env.ks", "ALAYOUT_KEY_PASSWORD": "kp"}
    assert signing_keys(cache=tmp_path, env=env)[0].store_password == "kp"


def test_signing_keys_default_order_ends_with_the_alayout_key(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))  # no ~/.android
    keys = signing_keys(cache=tmp_path, env={})
    assert keys[-1] == SigningKey(tmp_path / "alayout.keystore", "alayout", "alayout",
                                  "alayout", "alayout")


def test_build_agent_reuses_a_fresh_apk_without_re_signing(tmp_path):
    out = agent_apk(PACKAGE, tmp_path, "custom")
    out.parent.mkdir(parents=True)
    out.write_bytes(b"already signed")  # newer than the agent sources
    key = SigningKey(tmp_path / "x.ks", "a", "s", "k", "custom")

    def boom(args, timeout):  # noqa: ANN001
        raise AssertionError("a cached agent must not be rebuilt or re-signed")

    assert build_agent(PACKAGE, key=key, tools=fake_tools(tmp_path), cache=tmp_path,
                       run=boom) == out


def test_build_agent_signs_into_the_package_folder_with_split_passwords(tmp_path):
    keystore = tmp_path / "release.ks"
    keystore.write_text("ks", encoding="utf-8")  # already exists -> keytool is not invoked
    key = SigningKey(keystore, "release", "storepw", "keypw", "custom")
    captured: list[list[str]] = []

    out = build_agent(PACKAGE, key=key, tools=fake_tools(tmp_path), cache=tmp_path,
                      run=make_run(captured))

    assert out == tmp_path / PACKAGE / "agent-custom.apk"
    assert out.is_file()
    sign = next(a for a in captured if "sign" in a)
    assert "pass:storepw" in sign[sign.index("--ks-pass") + 1]
    assert "pass:keypw" in sign[sign.index("--key-pass") + 1]
