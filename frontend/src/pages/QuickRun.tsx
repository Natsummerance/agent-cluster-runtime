import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Divider,
  Form,
  Input,
  message,
  Radio,
  Row,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
} from 'antd';
import {
  CodeOutlined,
  FolderOpenOutlined,
  PlayCircleOutlined,
  RocketOutlined,
  ThunderboltOutlined,
  ToolOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useAppStore, apiErrorMessage } from '../store/appStore';
import * as api from '../api/endpoints';
import { useIntl } from '../i18n';
import PageHeader from '../components/PageHeader';

const QUICK_PROMPTS = [
  { label: '🧪 修复缺陷并测试', prompt: '排查当前代码库中的异常报错，定位根因，修改代码并运行测试确认修复。' },
  { label: '✨ 新增功能模块', prompt: '根据需求设计并实现新功能模块，保持现有工程架构风格，并编写对应的单元测试。' },
  { label: '📝 补齐单元测试', prompt: '为核心业务模块补齐自动化测试用例，覆盖正常分支与边界异常情况，确保测试全绿。' },
  { label: '⚡ 性能优化与重构', prompt: '梳理关键路径性能瓶颈，消除冗余计算与脆弱代码，保持接口兼容与测试通过。' },
];

export default function QuickRun() {
  const intl = useIntl();
  const navigate = useNavigate();
  const projects = useAppStore((s) => s.projects);
  const refreshProjects = useAppStore((s) => s.refreshProjects);
  const connected = useAppStore((s) => s.connected);
  const createProject = useAppStore((s) => s.createProject);

  const [form] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);
  const [selectedWorkflow, setSelectedWorkflow] = useState('agile-dev');

  useEffect(() => {
    void refreshProjects();
  }, [refreshProjects]);

  const handleBrowseFolder = useCallback(async () => {
    try {
      const electronApi = (window as unknown as { agentCluster?: { selectDirectory?: () => Promise<string | null> } })
        .agentCluster;
      if (electronApi?.selectDirectory) {
        const path = await electronApi.selectDirectory();
        if (path) {
          form.setFieldValue('workspace', path);
          // 自动推断项目名
          const baseName = path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || 'my-project';
          if (!form.getFieldValue('projectName')) {
            form.setFieldValue('projectName', baseName);
          }
        }
      } else {
        message.info(
          intl.formatMessage({
            id: 'quickrun.webSelectHint',
            defaultMessage: 'Web 模式下请直接在输入框中粘贴或输入工作区绝对路径',
          }),
        );
      }
    } catch (err) {
      message.error(apiErrorMessage(err));
    }
  }, [form, intl]);

  const handleSelectExistingProject = useCallback(
    (projectId: string) => {
      const matched = projects.find((p) => p.id === projectId);
      if (matched) {
        form.setFieldsValue({
          projectName: matched.name,
          workspace: matched.workspace,
        });
      }
    },
    [projects, form],
  );

  const handleSubmit = useCallback(async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);

      const workspace = values.workspace.trim();
      const projectName = values.projectName.trim();
      const goal = values.goal.trim();
      const model = values.model || 'deepseek-chat';
      const autoApprove = values.autoApprove ?? true;

      // 1. 查找或创建 Project
      let targetProject = projects.find(
        (p) => p.workspace.toLowerCase() === workspace.toLowerCase() || p.name.toLowerCase() === projectName.toLowerCase(),
      );

      if (!targetProject) {
        targetProject = await createProject({ name: projectName, workspace });
      }

      // 2. 确定流程路径
      const flowPath = selectedWorkflow === 'agile-dev' ? 'workflows/agile-dev.yaml' : undefined;

      // 3. 创建并启动 Session
      const sessionResult = await api.createSession(targetProject.id, {
        goal,
        model,
        flow: flowPath,
        yes: autoApprove,
      });

      message.success(
        intl.formatMessage(
          { id: 'quickrun.started', defaultMessage: '任务已启动：{sid}' },
          { sid: sessionResult.session_id },
        ),
      );

      // 4. 跳转至会话详情
      navigate(`/projects/${encodeURIComponent(targetProject.id)}/sessions/${encodeURIComponent(sessionResult.session_id)}`);
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) return;
      message.error(apiErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }, [form, projects, createProject, selectedWorkflow, intl, navigate]);

  return (
    <div style={{ maxWidth: 1000, margin: '0 auto' }} data-testid="quick-run-page">
      <PageHeader
        title="🚀 极速任务启动"
        description={intl.formatMessage({
          id: 'quickrun.description',
          defaultMessage: '开箱即用：选择本地工作区，输入一句话目标，立即调度 AI 研发集群完成编码与测试自验。',
        })}
      />

      {connected === false && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message={intl.formatMessage({
            id: 'quickrun.disconnected',
            defaultMessage: '未检测到后端服务，请确认 agent-cluster serve 已启动并在右上方点击重试',
          })}
        />
      )}

      <Form
        form={form}
        layout="vertical"
        initialValues={{
          projectName: '',
          workspace: '',
          goal: '',
          model: 'deepseek-chat',
          autoApprove: true,
        }}
      >
        <Card
          title={
            <Space>
              <FolderOpenOutlined style={{ color: '#0284c7' }} />
              <span>1. 工作区与项目目录</span>
            </Space>
          }
          style={{ marginBottom: 16 }}
        >
          {projects.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <Typography.Text type="secondary" style={{ marginRight: 8, fontSize: 13 }}>
                快速选取已有项目：
              </Typography.Text>
              <Space wrap size={[6, 6]}>
                {projects.slice(0, 5).map((p) => (
                  <Tag
                    key={p.id}
                    style={{ cursor: 'pointer' }}
                    onClick={() => handleSelectExistingProject(p.id)}
                  >
                    {p.name} ({p.workspace})
                  </Tag>
                ))}
              </Space>
            </div>
          )}

          <Row gutter={16}>
            <Col xs={24} md={16}>
              <Form.Item
                name="workspace"
                label="工作区绝对路径 (Workspace Path)"
                rules={[{ required: true, message: '请输入或选择项目工作区本地路径' }]}
                tooltip="AI 代理将在该目录下执行代码修改、语法校验、测试命令与 Git 操作"
              >
                <Input
                  placeholder="例如：C:\projects\my-app 或 /Users/name/projects/my-app"
                  addonAfter={
                    <Button type="text" size="small" icon={<FolderOpenOutlined />} onClick={handleBrowseFolder}>
                      浏览选择
                    </Button>
                  }
                  onChange={(e) => {
                    const val = e.target.value;
                    if (val && !form.getFieldValue('projectName')) {
                      const base = val.replace(/[\\/]+$/, '').split(/[\\/]/).pop();
                      if (base) form.setFieldValue('projectName', base);
                    }
                  }}
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item
                name="projectName"
                label="项目识别名称 (Project Name)"
                rules={[{ required: true, message: '请输入项目名称' }]}
              >
                <Input placeholder="例如：my-app" />
              </Form.Item>
            </Col>
          </Row>
        </Card>

        <Card
          title={
            <Space>
              <CodeOutlined style={{ color: '#0284c7' }} />
              <span>2. 任务目标与指令</span>
            </Space>
          }
          style={{ marginBottom: 16 }}
        >
          <div style={{ marginBottom: 12 }}>
            <Typography.Text type="secondary" style={{ marginRight: 8, fontSize: 13 }}>
              常用目标模板：
            </Typography.Text>
            <Space wrap size={[6, 6]}>
              {QUICK_PROMPTS.map((item) => (
                <Tag
                  key={item.label}
                  color="blue"
                  style={{ cursor: 'pointer' }}
                  onClick={() => form.setFieldValue('goal', item.prompt)}
                >
                  {item.label}
                </Tag>
              ))}
            </Space>
          </div>

          <Form.Item
            name="goal"
            label="详细需求说明 (Task Goal)"
            rules={[{ required: true, message: '请用自然语言描述本次开发任务的目标' }]}
          >
            <Input.TextArea
              rows={4}
              placeholder="例如：排查用户登录模块中的 Token 过期处理缺陷，添加自动化重试机制，并确保单元测试通过。"
              showCount
              maxLength={2000}
            />
          </Form.Item>
        </Card>

        <Card
          title={
            <Space>
              <ToolOutlined style={{ color: '#0284c7' }} />
              <span>3. 模型与执行模式</span>
            </Space>
          }
          style={{ marginBottom: 24 }}
        >
          <Row gutter={24}>
            <Col xs={24} md={12}>
              <Form.Item
                name="model"
                label="调度模型 (AI Model Backend)"
                rules={[{ required: true }]}
              >
                <Select
                  options={[
                    { value: 'deepseek-chat', label: '🔥 DeepSeek-V3 (deepseek-chat) —— 推荐首选' },
                    { value: 'deepseek-reasoner', label: '🧠 DeepSeek-R1 (deepseek-reasoner) —— 深度推理' },
                    { value: 'codex', label: '💻 Codex CLI (本地当前配置)' },
                    { value: 'deterministic', label: '⚡ 确定性离线模拟 (Deterministic Mock)' },
                  ]}
                />
              </Form.Item>
            </Col>

            <Col xs={24} md={12}>
              <div style={{ marginBottom: 8 }}>
                <Typography.Text strong>工作流模式 (Workflow Mode)</Typography.Text>
              </div>
              <Radio.Group
                value={selectedWorkflow}
                onChange={(e) => setSelectedWorkflow(e.target.value)}
                style={{ width: '100%' }}
              >
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Radio value="agile-dev">
                    <Space align="start">
                      <ThunderboltOutlined style={{ color: '#0284c7', marginTop: 4 }} />
                      <div>
                        <Typography.Text strong>极速敏捷开发 (推荐)</Typography.Text>
                        <br />
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          PM 规划 $\to$ Dev 极速编码 $\to$ QA 自动化测试验证 $\to$ 交付验收闭环
                        </Typography.Text>
                      </div>
                    </Space>
                  </Radio>
                  <Radio value="full-cluster">
                    <Space align="start">
                      <PlayCircleOutlined style={{ color: '#64748b', marginTop: 4 }} />
                      <div>
                        <Typography.Text strong>15 节点企业组织模拟</Typography.Text>
                        <br />
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          含需求评审会、架构评审会、代码评审会与门禁（适合全流程演练）
                        </Typography.Text>
                      </div>
                    </Space>
                  </Radio>
                </Space>
              </Radio.Group>
            </Col>
          </Row>

          <Divider style={{ margin: '16px 0' }} />

          <Row align="middle" justify="space-between">
            <Col>
              <Typography.Text strong>自动放行安全操作 (Auto Approve)</Typography.Text>
              <br />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                开启后工作区内的文件读写与测试命令将自动放行，无需频繁人工点击确认
              </Typography.Text>
            </Col>
            <Col>
              <Form.Item name="autoApprove" valuePropName="checked" noStyle>
                <Switch defaultChecked />
              </Form.Item>
            </Col>
          </Row>
        </Card>

        <div style={{ textAlign: 'center', paddingBottom: 32 }}>
          <Button
            type="primary"
            size="large"
            icon={<RocketOutlined />}
            loading={submitting}
            onClick={handleSubmit}
            style={{
              height: 48,
              paddingLeft: 40,
              paddingRight: 40,
              fontSize: 16,
              fontWeight: 600,
              borderRadius: 8,
              boxShadow: '0 4px 14px 0 rgba(2, 132, 199, 0.39)',
            }}
          >
            {submitting ? '正在启动集群任务...' : '🚀 启动敏捷开发任务'}
          </Button>
        </div>
      </Form>
    </div>
  );
}
