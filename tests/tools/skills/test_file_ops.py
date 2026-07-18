from agentteam.tools.skills.file_ops import list_dir, read_file, write_file


def test_read_file(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    f = tmp_path / "a.txt"
    f.write_text("hello world", encoding="utf-8")
    assert read_file.invoke({"path": str(f)}) == "hello world"


def test_write_file_creates(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    f = tmp_path / "out.txt"
    result = write_file.invoke({"path": str(f), "content": "abc"})
    assert f.read_text(encoding="utf-8") == "abc"
    assert "3 characters" in result


def test_write_file_overwrites(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    f = tmp_path / "out.txt"
    f.write_text("old", encoding="utf-8")
    write_file.invoke({"path": str(f), "content": "new"})
    assert f.read_text(encoding="utf-8") == "new"


def test_list_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "b.txt").write_text("x")
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    names = list_dir.invoke({"path": str(tmp_path)}).split("\n")
    assert names == ["a.txt", "b.txt", "sub"]


def test_read_file_missing_raises(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        read_file.invoke({"path": str(tmp_path / "nope.txt")})
