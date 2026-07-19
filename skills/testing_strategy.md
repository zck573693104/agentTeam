# Testing Strategy Skill

当任务涉及编写或评审测试时,按以下策略执行:

## 1. 测试金字塔
- **单元测试**(70%):纯函数 / 单类,隔离依赖,毫秒级
- **集成测试**(20%):多组件协作,真实 DB / API mock
- **端到端测试**(10%):用户视角,真实环境,慢但可信

## 2. TDD 循环
1. RED:写失败测试,确认测试框架能捕获失败
2. GREEN:写最小实现让测试通过
3. REFACTOR:重构实现,保持测试通过
4. COMMIT:小步提交,每个 RED-GREEN 一个 commit

## 3. 边界用例必覆盖
- 空集合 / 空字符串 / None
- 单元素 / 极大集合
- 负数 / 零 / 超大数
- 并发场景(多线程 / 多请求)
- 时区 / 时间边界(跨天、跨月)

## 4. 测试命名
- 用 `test_<scenario>_<expected_behavior>` 格式
- 例:`test_cancel_completed_run_returns_false`
- 避免 `test1`、`test_cancel` 等模糊命名

## 5. 断言原则
- 每个测试只断言一个行为(单一职责)
- 断言具体值,避免 `assert result is not None`
- 用 `pytest.raises` 验证异常类型 + 异常消息
- 测试不依赖执行顺序(无共享可变状态)

## 6. 不该测试的
- 不要测试第三方库的行为(信任上游)
- 不要测试 trivial 的 getter/setter
- 不要 mock 所有依赖(集成测试要真实)
