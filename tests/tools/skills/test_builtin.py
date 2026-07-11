def test_register_builtin_skills():
    from agentteam.tools.registry import ToolRegistry
    from agentteam.tools.skills import register_builtin_skills

    reg = ToolRegistry()
    register_builtin_skills(reg)

    names = set(reg.list_names())
    assert {"read_file", "write_file", "list_dir"}.issubset(names)
