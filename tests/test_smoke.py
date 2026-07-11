def test_package_importable():
    import agentteam
    assert agentteam.__version__ == "0.1.0"
