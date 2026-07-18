"""BUG-15: file_ops 路径穿越保护测试。

验证 read_file / write_file / list_dir 拒绝 workspace 外的路径,
同时允许 workspace 内的相对路径(包括子目录)正常工作。
"""
from pathlib import Path


def test_read_file_rejects_path_traversal(tmp_path, monkeypatch):
    """read_file 拒绝 .. 路径穿越。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)
    from agentteam.tools.skills.file_ops import read_file

    # 尝试读取 workspace 之外的文件
    try:
        read_file.invoke({"path": str(Path("..") / "secret.txt")})
        assert False, "应抛 ValueError"
    except ValueError as e:
        assert "workspace" in str(e).lower() or "outside" in str(e).lower()


def test_write_file_rejects_path_traversal(tmp_path, monkeypatch):
    """write_file 拒绝 .. 路径穿越。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)
    from agentteam.tools.skills.file_ops import write_file

    try:
        write_file.invoke({"path": str(Path("..") / "evil.txt"), "content": "hacked"})
        assert False, "应抛 ValueError"
    except ValueError:
        pass


def test_list_dir_rejects_path_traversal(tmp_path, monkeypatch):
    """list_dir 拒绝 .. 路径穿越。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)
    from agentteam.tools.skills.file_ops import list_dir

    try:
        list_dir.invoke({"path": str(Path(".."))})
        assert False, "应抛 ValueError"
    except ValueError:
        pass


def test_read_file_allows_workspace_paths(tmp_path, monkeypatch):
    """workspace 内路径正常工作。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)
    # 创建一个文件
    (tmp_path / "test.txt").write_text("hello", encoding="utf-8")
    from agentteam.tools.skills.file_ops import read_file

    result = read_file.invoke({"path": "test.txt"})
    assert "hello" in result


def test_read_file_allows_subdirectory(tmp_path, monkeypatch):
    """workspace 子目录路径正常工作。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "test.txt").write_text("nested", encoding="utf-8")
    from agentteam.tools.skills.file_ops import read_file

    result = read_file.invoke({"path": "sub/test.txt"})
    assert "nested" in result
