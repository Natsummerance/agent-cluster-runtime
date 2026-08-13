import { useCallback, useEffect, useState } from 'react';
import {
  Button,
  Card,
  Col,
  Form,
  Input,
  InputNumber,
  message,
  Modal,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { Link, useParams } from 'react-router-dom';
import * as api from '../api/endpoints';
import { ApiError } from '../api/client';
import { apiErrorMessage } from '../store/appStore';
import { useProjectStore } from '../store/projectStore';
import PageHeader from '../components/PageHeader';
import StatusTag from '../components/StatusTag';
import type { AxisStatus, TaskEntry } from '../api/types';

const MODEL_OPTIONS = ['codex', 'chat', 'responses', 'anthropic', 'deterministic'].map((m) => ({
  value: m,
  label: m,
}));

const STATUS_OPTIONS = ['running', 'waiting_approval', 'completed', 'failed', 'aborted'].map(
  (value) => ({ value, label: value }),
);

const ASSIGNEE_OPTIONS = ['PM', 'DEV', 'QA'].map((value) => ({ value, label: value }));

const AXIS_STATUS_META: Record<AxisStatus, { color: string; label: string }> = {
  ok: { color: 'green', label: '正常' },
  warn: { color: 'orange', label: '预警' },
  critical: { color: 'red', label: '危险' },
};

function axisTag(status?: AxisStatus) {
  if (!status) return <Tag>无数据</Tag>;
  const meta = AXIS_STATUS_META[status];
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

export default function ProjectSessions() {
  const { pid = '' } = useParams();
  const tasks = useProjectStore((s) => s.tasks[pid] ?? []);
  const dashboard = useProjectStore((s) => s.dashboard[pid]);
  const filters = useProjectStore((s) => s.filters);
  const loading = useProjectStore((s) => s.loading);
  const loadDashboard = useProjectStore((s) => s.loadDashboard);
  const loadTasks = useProjectStore((s) => s.loadTasks);
  const setFilter = useProjectStore((s) => s.setFilter);
  const assignTask = useProjectStore((s) => s.assignTask);
  const [modalOpen, setModalOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm();

  const applyFilters = useCallback(() => {
    if (pid) void loadTasks(pid);
  }, [pid, loadTasks]);

  useEffect(() => {
    if (!pid) return;
    setFilter({ status: undefined, assignee: undefined, q: undefined });
    void loadDashboard(pid);
    void loadTasks(pid);
    // 仅在项目切换时执行；筛选变化由 applyFilters 显式触发
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  const handleCreate = useCallback(async () => {
    try {
      const values = await form.validateFields();
      setCreating(true);
      const result = await api.createSession(pid, {
        goal: values.goal.trim(),
        model: values.model,
        flow: values.flow || undefined,
        budget: values.budget,
        deterministic: values.deterministic,
        yes: values.yes,
      });
      message.success(`会话 ${result.session_id} 已创建`);
      setModalOpen(false);
      form.resetFields();
      void loadTasks(pid);
      void loadDashboard(pid);
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) return;
      message.error(apiErrorMessage(err));
    } finally {
      setCreating(false);
    }
  }, [form, pid, loadTasks, loadDashboard]);

  const handleAssign = useCallback(
    async (sid: string, value: string | undefined) => {
      if (!value) return;
      try {
        await assignTask(pid, sid, value);
        message.success(`已指派给 ${value}`);
      } catch (err) {
        message.error(apiErrorMessage(err));
      }
    },
    [pid, assignTask],
  );

  const handleFork = useCallback(
    async (sid: string) => {
      try {
        const result = await api.forkSession(sid, { project_id: pid });
        message.success(`已派生新会话 ${result.session_id}`);
        void loadTasks(pid);
        void loadDashboard(pid);
      } catch (err) {
        const code =
          err instanceof ApiError &&
          err.payload &&
          typeof err.payload === 'object' &&
          'code' in (err.payload as object)
            ? String((err.payload as { code?: string }).code)
            : undefined;
        if (code === 'fork_conflict') {
          message.warning('源会话仍在运行中，无法派生（fork_conflict）：请先结束或取消该会话');
        } else {
          message.error(apiErrorMessage(err));
        }
      }
    },
    [pid, loadTasks, loadDashboard],
  );

  const builtInAssignee = new Set(ASSIGNEE_OPTIONS.map((o) => o.value));
  const assigneeOptions = [
    ...ASSIGNEE_OPTIONS,
    ...Array.from(
      new Set(tasks.map((t) => t.assignee).filter((v): v is string => !!v && !builtInAssignee.has(v))),
    ).map((value) => ({ value, label: value })),
  ];

  const columns = [
    { title: '会话 ID', dataIndex: 'session_id', key: 'session_id', render: (v: string) => <Typography.Text className="mono">{v}</Typography.Text> },
    {
      title: '目标',
      dataIndex: 'goal',
      key: 'goal',
      ellipsis: true,
      render: (v?: string) => <span title={v ?? ''}>{v ?? '-'}</span>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (v?: string) => <StatusTag status={v} />,
    },
    { title: '运行时状态', dataIndex: 'runtime_status', key: 'runtime_status', render: (v?: string) => v ?? '-' },
    {
      title: '指派',
      key: 'assignee',
      width: 150,
      render: (_: unknown, record: TaskEntry) => (
        <Select
          size="small"
          style={{ width: 120 }}
          placeholder="未指派"
          options={assigneeOptions}
          value={record.assignee || undefined}
          onChange={(value) => void handleAssign(record.session_id, value as string | undefined)}
          data-testid={`assignee-select-${record.session_id}`}
        />
      ),
    },
    { title: '工作区', dataIndex: 'workspace', key: 'workspace', ellipsis: true, render: (v?: string) => v ?? '-' },
    {
      title: '隔离',
      dataIndex: 'worktree',
      key: 'worktree',
      render: (v?: boolean) => (v ? <Tag color="blue">worktree</Tag> : '-'),
    },
    { title: '更新时间', dataIndex: 'updated_at', key: 'updated_at', render: (v?: string) => v?.slice(0, 19) ?? '-' },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, record: TaskEntry) => (
        <Space>
          <Link to={`/projects/${pid}/sessions/${record.session_id}`}>
            <Button size="small" type="primary">打开</Button>
          </Link>
          <Link to={`/audit?session_id=${record.session_id}`}>
            <Button size="small">审计</Button>
          </Link>
          <Button size="small" onClick={() => void handleFork(record.session_id)} data-testid={`fork-${record.session_id}`}>
            派生
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div data-testid="project-sessions-page">
      <PageHeader
        title="项目任务面板"
        description={<Typography.Text className="mono" type="secondary">项目 {pid}</Typography.Text>}
        actions={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)} data-testid="create-session-btn">
            新建会话
          </Button>
        }
      />
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} md={8}>
          <Card data-testid="axis-cost">
            <Statistic
              title="成本轴"
              value={dashboard ? `${dashboard.cost.used} / ${dashboard.cost.limit}` : '-'}
              suffix={dashboard ? `（${Math.round(dashboard.cost.ratio * 100)}%）` : ''}
            />
            <Space>
              {axisTag(dashboard?.cost.status)}
              <Typography.Text type="secondary">
                {dashboard ? `预估 ${dashboard.cost.estimated_usd} USD` : ''}
              </Typography.Text>
            </Space>
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card data-testid="axis-progress">
            <Statistic
              title="进度轴"
              value={dashboard ? `${Math.round(dashboard.progress.score * 100)}%` : '-'}
              suffix={dashboard ? ` 阶段 ${dashboard.progress.phases.done}/${dashboard.progress.phases.total}` : ''}
            />
            <Space>{axisTag(dashboard?.progress.status)}</Space>
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card data-testid="axis-health">
            <Statistic
              title="健康轴"
              value={dashboard ? `${Math.round(dashboard.health.score * 100)}%` : '-'}
            />
            <Space>{axisTag(dashboard?.health.status)}</Space>
          </Card>
        </Col>
      </Row>
      <Card data-testid="task-filters" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Select
            placeholder="状态"
            allowClear
            style={{ width: 180 }}
            options={STATUS_OPTIONS}
            value={filters.status}
            onChange={(value) => {
              setFilter({ status: value || undefined });
              applyFilters();
            }}
            data-testid="filter-status"
          />
          <Input
            placeholder="指派"
            allowClear
            style={{ width: 160 }}
            value={filters.assignee}
            onChange={(e) => setFilter({ assignee: e.target.value || undefined })}
            onPressEnter={applyFilters}
            data-testid="filter-assignee"
          />
          <Input
            placeholder="关键词"
            allowClear
            style={{ width: 200 }}
            value={filters.q}
            onChange={(e) => setFilter({ q: e.target.value || undefined })}
            onPressEnter={applyFilters}
            data-testid="filter-q"
          />
          <Button onClick={applyFilters}>筛选</Button>
        </Space>
      </Card>
      <Card>
        <Table<TaskEntry>
          rowKey="session_id"
          columns={columns}
          dataSource={tasks}
          loading={loading}
          pagination={false}
          locale={{ emptyText: '暂无任务，点击右上角新建会话' }}
          data-testid="sessions-table"
        />
      </Card>
      <Modal
        title="新建会话"
        open={modalOpen}
        onOk={handleCreate}
        confirmLoading={creating}
        onCancel={() => setModalOpen(false)}
        okText="创建"
        cancelText="取消"
        destroyOnClose
        width={640}
        data-testid="create-session-modal"
      >
        <Form form={form} layout="vertical" initialValues={{ deterministic: false, yes: false }}>
          <Form.Item
            name="goal"
            label="会话目标"
            rules={[{ required: true, message: '请输入会话目标' }]}
          >
            <Input.TextArea rows={3} placeholder="描述本次开发/构建目标…" data-testid="session-goal-input" />
          </Form.Item>
          <Form.Item name="model" label="模型">
            <Select allowClear placeholder="默认由后端决定" options={MODEL_OPTIONS} data-testid="session-model-select" />
          </Form.Item>
          <Form.Item name="flow" label="流程文件">
            <Input placeholder="例如 examples/flows/build-product.yaml" data-testid="session-flow-input" />
          </Form.Item>
          <Form.Item name="budget" label="Token 预算">
            <InputNumber min={0} step={1000} style={{ width: '100%' }} placeholder="可选" data-testid="session-budget-input" />
          </Form.Item>
          <Space size="large">
            <Form.Item name="deterministic" label="确定性模式" valuePropName="checked">
              <Switch data-testid="session-deterministic-switch" />
            </Form.Item>
            <Form.Item name="yes" label="自动审批（--yes）" valuePropName="checked">
              <Switch data-testid="session-yes-switch" />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </div>
  );
}
