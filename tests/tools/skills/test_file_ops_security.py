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


def test_read_file_rejects_oversized_file(tmp_path, monkeypatch):
    """read_file 拒绝超过 MAX_READ_SIZE 的文件。"""
    import pytest

    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    from agentteam.tools.skills import file_ops
    from agentteam.tools.skills.file_ops import read_file

    # 临时把阈值调小,避免真实写入 11MB 文件
    monkeypatch.setattr(file_ops, "MAX_READ_SIZE", 100)
    f = tmp_path / "big.txt"
    f.write_text("a" * 200, encoding="utf-8")
    result = read_file.invoke({"path": str(f)})
    assert "文件过大" in result


def test_read_file_rejects_binary_file(tmp_path, monkeypatch):
    """read_file 拒绝包含 null 字节的二进制文件。"""
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    from agentteam.tools.skills.file_ops import read_file

    f = tmp_path / "bin.dat"
    with open(f, "wb") as fh:
        fh.write(b"\x00\x01\x02")
    result = read_file.invoke({"path": str(f)})
    assert "二进制" in result


def test_read_file_rejects_symlink(tmp_path, monkeypatch):
    """read_file 拒绝指向 workspace 外的符号链接。"""
    import os
    import pytest

    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    from agentteam.tools.skills.file_ops import read_file

    # 在 workspace 外创建目标文件(用 tmp_path.name 保证唯一,避免并发污染)
    outside = tmp_path.parent / f"{tmp_path.name}_outside_secret.txt"
    try:
        outside.write_text("secret", encoding="utf-8")
    except OSError:
        pytest.skip("无法在 workspace 外创建目标文件")

    link_path = tmp_path / "link.txt"
    try:
        os.symlink(outside, link_path)
    except OSError:
        outside.unlink(missing_ok=True)
        pytest.skip("当前环境不支持创建符号链接")

    try:
        try:
            result = read_file.invoke({"path": str(link_path)})
        except ValueError:
            # 抛 ValueError 也符合预期(与 .. 穿越一致的错误模式)
            return
        # 若返回字符串而非抛异常,则必须是错误字符串
        assert (
            "symlink" in result.lower()
            or "workspace" in result.lower()
            or "错误" in result
        ), f"应拒绝符号链接,实际返回: {result!r}"
    finally:
        outside.unlink(missing_ok=True)
