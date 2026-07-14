"""search_web stub 技能单元测试。"""
from agentteam.tools.skills.search_web import search_web


def test_search_web_returns_text():
    """search_web 返回字符串。"""
    result = search_web.invoke({"query": "Python 异步编程"})
    assert isinstance(result, str)
    assert len(result) > 0


def test_search_web_echoes_query():
    """返回结果包含查询关键词。"""
    result = search_web.invoke({"query": "LangGraph 入门"})
    assert "LangGraph" in result


def test_search_web_respects_max_results():
    """max_results 控制返回条目数。"""
    result_1 = search_web.invoke({"query": "test", "max_results": 1})
    result_3 = search_web.invoke({"query": "test", "max_results": 3})
    # max_results=1 的结果条目数应少于 max_results=3
    count_1 = result_1.count("[结果")
    count_3 = result_3.count("[结果")
    assert count_1 == 1
    assert count_3 == 3


def test_search_web_registered_in_builtin_skills():
    """search_web 被注册到 register_builtin_skills。"""
    from agentteam.tools.registry import ToolRegistry
    from agentteam.tools.skills import register_builtin_skills

    reg = ToolRegistry()
    register_builtin_skills(reg)
    names = reg.list_names()
    assert "search_web" in names
