/**
 * 团队新建/编辑表单 Modal。
 *
 * 双模式:
 * - 结构化表单:团队信息 + 默认模型 + supervisor + 成员动态增删(Form.List),
 *   覆盖单层 supervisor+worker 的常见场景;
 * - JSON 高级模式:直接编辑提交给 API 的完整 JSON,支持嵌套 supervisor 树、
 *   TeamRef 子团队、审批策略、MCP servers 等高级字段。
 *
 * 提交:新建 POST /api/teams;编辑 PUT /api/teams/{name}(后端要求 body.name
 * 与 URL 一致,故编辑模式锁定团队名)。
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Divider,
  Form,
  Input,
  InputNumber,
  Modal,
  Segmented,
  Select,
  Switch,
  message,
} from "antd";
import { api, type AgentNode, type Team } from "../api/client";

interface WorkerRow {
  name: string;
  system_prompt: string;
  tools: string[];
  max_iterations: number;
}

interface FormValues {
  name: string;
  description: string;
  provider: string;
  model_name: string;
  temperature: number;
  streaming: boolean;
  skills: string[];
  leader_name: string;
  leader_prompt: string;
  workers: WorkerRow[];
}

const EMPTY_VALUES: FormValues = {
  name: "",
  description: "",
  provider: "qwen",
  model_name: "qwen-max",
  temperature: 0.7,
  streaming: true,
  skills: [],
  leader_name: "leader",
  leader_prompt: "",
  workers: [],
};

/** 判断 AgentNode 是否含结构化表单无法表达的复杂结构(嵌套 supervisor / TeamRef)。 */
function isComplexNode(node: AgentNode): boolean {
  for (const c of node.children || []) {
    if ("_type" in c) return true;
    if (c.role !== "worker") return true;
  }
  return false;
}

/** 后端团队对象(new schema)→ 表单值;复杂结构返回 null。 */
function teamToFormValues(team: Team): FormValues | null {
  const root = team.root;
  if (!root || root.role !== "supervisor" || isComplexNode(root)) return null;
  return {
    name: team.name,
    description: team.description || "",
    provider: team.default_model?.provider ?? "qwen",
    model_name: team.default_model?.name ?? "qwen-max",
    temperature: team.default_model?.temperature ?? 0.7,
    streaming: team.default_model?.streaming ?? true,
    skills: team.skills || [],
    leader_name: root.name,
    leader_prompt: root.system_prompt || "",
    workers: (root.children || []).map((c) => {
      const w = c as AgentNode;
      return {
        name: w.name,
        system_prompt: w.system_prompt || "",
        tools: w.tools || [],
        max_iterations: w.max_iterations ?? 10,
      };
    }),
  };
}

/** 表单值 → 提交 payload(旧 schema,后端自动转 root 树)。 */
function formToPayload(v: FormValues): Record<string, unknown> {
  return {
    name: v.name.trim(),
    description: v.description?.trim() ?? "",
    leader: {
      name: v.leader_name?.trim() || "leader",
      system_prompt: v.leader_prompt ?? "",
    },
    workers: (v.workers || []).map((w) => ({
      name: w.name.trim(),
      // role 为展示用职务标签,后端转换后丢弃;这里回退为成员名保证兼容旧 schema
      role: w.name.trim(),
      system_prompt: w.system_prompt ?? "",
      tools: w.tools ?? [],
      max_iterations: w.max_iterations ?? 10,
    })),
    default_model: {
      provider: v.provider?.trim() || "qwen",
      name: v.model_name?.trim() || "qwen-max",
      temperature: v.temperature ?? 0.7,
      streaming: v.streaming ?? true,
    },
    skills: v.skills ?? [],
  };
}

/** 任意 JSON payload(新/旧 schema)→ 表单值;复杂结构或非法返回 null。 */
function payloadToFormValues(p: Record<string, any>): FormValues | null {
  if (!p || typeof p !== "object" || !p.name) return null;
  const base = {
    name: p.name,
    description: p.description || "",
    provider: p.default_model?.provider ?? "qwen",
    model_name: p.default_model?.name ?? "qwen-max",
    temperature: p.default_model?.temperature ?? 0.7,
    streaming: p.default_model?.streaming ?? true,
    skills: p.skills || [],
  };
  if (p.root) {
    const root = p.root as AgentNode;
    if (root.role !== "supervisor" || isComplexNode(root)) return null;
    return {
      ...base,
      leader_name: root.name,
      leader_prompt: root.system_prompt || "",
      workers: (root.children || []).map((c) => {
        const w = c as AgentNode;
        return {
          name: w.name,
          system_prompt: w.system_prompt || "",
          tools: w.tools || [],
          max_iterations: w.max_iterations ?? 10,
        };
      }),
    };
  }
  if (p.leader) {
    return {
      ...base,
      leader_name: p.leader.name || "leader",
      leader_prompt: p.leader.system_prompt || "",
      workers: (p.workers || []).map((w: Record<string, any>) => ({
        name: w.name,
        system_prompt: w.system_prompt || "",
        tools: w.tools || [],
        max_iterations: w.max_iterations ?? 10,
      })),
    };
  }
  return null;
}

export interface TeamFormModalProps {
  open: boolean;
  /** 非 null = 编辑模式(锁定团队名)。 */
  editing: Team | null;
  onClose: () => void;
  /** 提交成功回调:teamName 为团队名。 */
  onSuccess: (teamName: string) => void;
}

export default function TeamFormModal({ open, editing, onClose, onSuccess }: TeamFormModalProps) {
  const [form] = Form.useForm<FormValues>();
  const editingComplex = useMemo(
    () => (editing ? teamToFormValues(editing) === null : false),
    [editing],
  );
  const [mode, setMode] = useState<"form" | "json">("form");
  const [jsonText, setJsonText] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // 打开时初始化:编辑回填 / 新建清空
  useEffect(() => {
    if (!open) return;
    if (editing) {
      const values = teamToFormValues(editing);
      if (values) {
        setMode("form");
        form.setFieldsValue(values);
      } else {
        // 嵌套树/TeamRef 等复杂团队:只能 JSON 模式编辑
        setMode("json");
        setJsonText(JSON.stringify(editing, null, 2));
        form.setFieldsValue(EMPTY_VALUES);
      }
    } else {
      setMode("form");
      setJsonText("");
      form.setFieldsValue(EMPTY_VALUES);
    }
  }, [open, editing, form]);

  // 表单 → JSON:切换时生成当前表单对应的完整 payload
  const switchToJson = () => {
    setJsonText(JSON.stringify(formToPayload(form.getFieldsValue()), null, 2));
    setMode("json");
  };

  // JSON → 表单:校验并回填;失败报错留在 JSON 模式
  const switchToForm = () => {
    try {
      const payload = JSON.parse(jsonText);
      const values = payloadToFormValues(payload);
      if (!values) {
        message.error("JSON 含嵌套 supervisor / TeamRef 等复杂结构,仅高级模式可编辑");
        return;
      }
      form.setFieldsValue(values);
      setMode("form");
    } catch {
      message.error("JSON 格式错误,无法切换到表单模式");
    }
  };

  const doSubmit = async (payload: Record<string, unknown>) => {
    setSubmitting(true);
    try {
      if (editing) {
        await api(`/api/teams/${editing.name}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        });
        message.success(`团队 ${payload.name} 已更新`);
      } else {
        await api("/api/teams", { method: "POST", body: JSON.stringify(payload) });
        message.success(`团队 ${payload.name} 注册成功`);
      }
      onSuccess(String(payload.name));
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleSubmit = async () => {
    if (mode === "json") {
      let payload: Record<string, unknown>;
      try {
        payload = JSON.parse(jsonText);
      } catch {
        message.error("JSON 格式错误");
        return;
      }
      if (editing && payload.name !== editing.name) {
        message.error(`编辑模式团队名不可修改(须为 "${editing.name}")`);
        return;
      }
      await doSubmit(payload);
      return;
    }
    const values = await form.validateFields();
    await doSubmit(formToPayload(values));
  };

  return (
    <Modal
      title={editing ? `编辑团队 · ${editing.name}` : "注册团队"}
      open={open}
      onCancel={onClose}
      onOk={handleSubmit}
      confirmLoading={submitting}
      okText={editing ? "保存" : "注册"}
      cancelText="取消"
      width={760}
      destroyOnHidden
    >
      {editingComplex && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="该团队含嵌套 supervisor / TeamRef 子团队等复杂结构,仅提供 JSON 高级模式编辑。"
        />
      )}

      <Segmented
        block
        style={{ marginBottom: 16 }}
        value={mode}
        onChange={(v) => {
          if (v === "json") switchToJson();
          else switchToForm();
        }}
        options={[
          { label: "结构化表单", value: "form", disabled: editingComplex },
          { label: "JSON 高级模式", value: "json" },
        ]}
      />

      <div style={{ display: mode === "form" ? "block" : "none" }}>
        <Form form={form} layout="vertical" requiredMark={false}>
          {/* 团队基本信息 */}
          <div style={{ display: "flex", gap: 12 }}>
            <Form.Item
              name="name"
              label="团队名"
              rules={[{ required: true, message: "请输入团队名" }]}
              style={{ flex: "0 0 220px" }}
            >
              <Input
                placeholder="如 dev_team"
                disabled={!!editing}
                title={editing ? "编辑模式团队名不可修改" : undefined}
              />
            </Form.Item>
            <Form.Item name="description" label="描述" style={{ flex: 1 }}>
              <Input placeholder="一句话说明团队职责" />
            </Form.Item>
          </div>

          {/* 默认模型 */}
          <Divider titlePlacement="left" orientationMargin={0} style={{ marginTop: 4, marginBottom: 12 }}>
            <span style={{ fontSize: 13 }}>默认模型(成员未单独指定模型时使用)</span>
          </Divider>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            <Form.Item name="provider" label="Provider" style={{ flex: "0 0 140px" }}>
              <Input placeholder="qwen" />
            </Form.Item>
            <Form.Item name="model_name" label="模型名" style={{ flex: "0 0 180px" }}>
              <Input placeholder="qwen-max" />
            </Form.Item>
            <Form.Item name="temperature" label="Temperature" style={{ flex: "0 0 120px" }}>
              <InputNumber min={0} max={2} step={0.1} style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item name="streaming" label="流式" valuePropName="checked">
              <Switch />
            </Form.Item>
          </div>

          {/* supervisor */}
          <Divider titlePlacement="left" orientationMargin={0} style={{ marginTop: 4, marginBottom: 12 }}>
            <span style={{ fontSize: 13 }}>主管(Supervisor,负责拆解任务与验收)</span>
          </Divider>
          <div style={{ display: "flex", gap: 12 }}>
            <Form.Item
              name="leader_name"
              label="名称"
              rules={[{ required: true, message: "请输入主管名称" }]}
              style={{ flex: "0 0 220px" }}
            >
              <Input placeholder="如 tech_lead" />
            </Form.Item>
            <Form.Item name="leader_prompt" label="System Prompt" style={{ flex: 1 }}>
              <Input.TextArea
                autoSize={{ minRows: 2, maxRows: 5 }}
                placeholder="你是技术主管,负责拆解需求、分配步骤、汇总产出。"
              />
            </Form.Item>
          </div>

          {/* 成员动态列表 */}
          <Divider titlePlacement="left" orientationMargin={0} style={{ marginTop: 4, marginBottom: 12 }}>
            <span style={{ fontSize: 13 }}>成员(Worker,可动态增删)</span>
          </Divider>
          <Form.List name="workers">
            {(fields, { add, remove }) => (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {fields.map((field) => (
                  <div
                    key={field.key}
                    style={{
                      border: "1px solid var(--at-border)",
                      borderRadius: 2,
                      padding: "10px 12px",
                      background: "var(--at-bg-deep)",
                    }}
                  >
                    <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
                      <Form.Item
                        name={[field.name, "name"]}
                        rules={[{ required: true, message: "成员名必填" }]}
                        style={{ flex: "0 0 180px", marginBottom: 8 }}
                      >
                        <Input placeholder="成员名,如 coder" />
                      </Form.Item>
                      <Form.Item
                        name={[field.name, "max_iterations"]}
                        label="迭代上限"
                        style={{ flex: "0 0 140px", marginBottom: 8 }}
                      >
                        <InputNumber min={1} max={50} style={{ width: "100%" }} />
                      </Form.Item>
                      <div style={{ flex: 1 }} />
                      <Button type="text" danger onClick={() => remove(field.name)}>
                        移除
                      </Button>
                    </div>
                    <Form.Item
                      name={[field.name, "system_prompt"]}
                      style={{ marginBottom: 8 }}
                    >
                      <Input.TextArea
                        autoSize={{ minRows: 2, maxRows: 5 }}
                        placeholder="System Prompt:职责与工具使用说明"
                      />
                    </Form.Item>
                    <Form.Item name={[field.name, "tools"]} style={{ marginBottom: 0 }}>
                      <Select
                        mode="tags"
                        tokenSeparators={[","]}
                        placeholder="工具(回车/逗号分隔),如 read_file、mcp:git:git_status;留空则不配工具"
                        style={{ width: "100%" }}
                      />
                    </Form.Item>
                  </div>
                ))}
                <Button
                  type="dashed"
                  onClick={() => add({ name: "", system_prompt: "", tools: [], max_iterations: 10 })}
                  style={{ width: "100%" }}
                >
                  + 新增成员
                </Button>
              </div>
            )}
          </Form.List>

          {/* skills */}
          <Divider titlePlacement="left" orientationMargin={0} style={{ marginTop: 16, marginBottom: 12 }}>
            <span style={{ fontSize: 13 }}>团队 Skills(对应 skills/ 目录 .md 文件名)</span>
          </Divider>
          <Form.Item name="skills" style={{ marginBottom: 0 }}>
            <Select
              mode="tags"
              tokenSeparators={[","]}
              placeholder="可选;回车/逗号分隔,如 read_file、search_web"
              style={{ width: "100%" }}
            />
          </Form.Item>
        </Form>
      </div>

      <div style={{ display: mode === "json" ? "block" : "none" }}>
        <Input.TextArea
          value={jsonText}
          onChange={(e) => setJsonText(e.target.value)}
          rows={18}
          style={{ fontFamily: "var(--at-font-mono)", fontSize: 13 }}
          placeholder="完整 Team JSON(支持新旧 schema;嵌套 supervisor 树、TeamRef、approval_policy、mcp_servers 等高级字段仅此模式可编辑)"
        />
        <div style={{ marginTop: 8, color: "var(--at-text-faint)", fontSize: 12 }}>
          提示:结构化表单与 JSON 双向同步;切换到表单时若含复杂结构会拒绝并保留 JSON 模式。
        </div>
      </div>
    </Modal>
  );
}
