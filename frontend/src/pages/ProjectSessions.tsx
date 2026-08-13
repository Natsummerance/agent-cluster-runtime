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
import { useIntl } from '../i18n';
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

const AXIS_STATUS_ID: Record<AxisStatus, string> = {
  ok: 'dashboard.axis.ok',
  warn: 'dashboard.axis.warn',
  critical: 'dashboard.axis.critical',
};

const AXIS_STATUS_COLOR: Record<AxisStatus, string> = {
  ok: 'green',
  warn: 'orange',
  critical: 'red',
};

export default function ProjectSessions() {
  const intl = useIntl();
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

  const axisTag = (axisStatus?: AxisStatus) => {
    if (!axisStatus)
      return <Tag>{intl.formatMessage({ id: 'dashboard.axis.noData', defaultMessage: 'No data' })}</Tag>;
    return (
      <Tag color={AXIS_STATUS_COLOR[axisStatus]}>
        {intl.formatMessage({ id: AXIS_STATUS_ID[axisStatus], defaultMessage: axisStatus })}
      </Tag>
    );
  };

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
      message.success(
        intl.formatMessage(
          { id: 'ps.created', defaultMessage: 'Session {id} created' },
          { id: result.session_id },
        ),
      );
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
  }, [form, pid, loadTasks, loadDashboard, intl]);

  const handleAssign = useCallback(
    async (sid: string, value: string | undefined) => {
      if (!value) return;
      try {
        await assignTask(pid, sid, value);
        message.success(
          intl.formatMessage({ id: 'ps.assigned', defaultMessage: 'Assigned to {name}' }, { name: value }),
        );
      } catch (err) {
        message.error(apiErrorMessage(err));
      }
    },
    [pid, assignTask, intl],
  );

  const handleFork = useCallback(
    async (sid: string) => {
      try {
        const result = await api.forkSession(sid, { project_id: pid });
        message.success(
          intl.formatMessage(
            { id: 'ps.forked', defaultMessage: 'Forked new session {id}' },
            { id: result.session_id },
          ),
        );
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
          message.warning(
            intl.formatMessage({
              id: 'ps.forkConflict',
              defaultMessage:
                'Source session is still running and cannot be forked (fork_conflict); end or cancel it first',
            }),
          );
        } else {
          message.error(apiErrorMessage(err));
        }
      }
    },
    [pid, loadTasks, loadDashboard, intl],
  );

  const builtInAssignee = new Set(ASSIGNEE_OPTIONS.map((o) => o.value));
  const assigneeOptions = [
    ...ASSIGNEE_OPTIONS,
    ...Array.from(
      new Set(tasks.map((t) => t.assignee).filter((v): v is string => !!v && !builtInAssignee.has(v))),
    ).map((value) => ({ value, label: value })),
  ];

  const columns = [
    { title: intl.formatMessage({ id: 'ps.col.sessionId', defaultMessage: 'Session ID' }), dataIndex: 'session_id', key: 'session_id', render: (v: string) => <Typography.Text className="mono">{v}</Typography.Text> },
    {
      title: intl.formatMessage({ id: 'common.goal', defaultMessage: 'Goal' }),
      dataIndex: 'goal',
      key: 'goal',
      ellipsis: true,
      render: (v?: string) => <span title={v ?? ''}>{v ?? '-'}</span>,
    },
    {
      title: intl.formatMessage({ id: 'common.status', defaultMessage: 'Status' }),
      dataIndex: 'status',
      key: 'status',
      render: (v?: string) => <StatusTag status={v} />,
    },
    { title: intl.formatMessage({ id: 'ps.col.runtimeStatus', defaultMessage: 'Runtime status' }), dataIndex: 'runtime_status', key: 'runtime_status', render: (v?: string) => v ?? '-' },
    {
      title: intl.formatMessage({ id: 'ps.col.assignee', defaultMessage: 'Assignee' }),
      key: 'assignee',
      width: 150,
      render: (_: unknown, record: TaskEntry) => (
        <Select
          size="small"
          style={{ width: 120 }}
          placeholder={intl.formatMessage({ id: 'ps.assignee.placeholder', defaultMessage: 'Unassigned' })}
          options={assigneeOptions}
          value={record.assignee || undefined}
          onChange={(value) => void handleAssign(record.session_id, value as string | undefined)}
          data-testid={`assignee-select-${record.session_id}`}
        />
      ),
    },
    { title: intl.formatMessage({ id: 'common.workspace', defaultMessage: 'Workspace' }), dataIndex: 'workspace', key: 'workspace', ellipsis: true, render: (v?: string) => v ?? '-' },
    {
      title: intl.formatMessage({ id: 'ps.col.isolation', defaultMessage: 'Isolation' }),
      dataIndex: 'worktree',
      key: 'worktree',
      render: (v?: boolean) => (v ? <Tag color="blue">worktree</Tag> : '-'),
    },
    { title: intl.formatMessage({ id: 'common.updatedAt', defaultMessage: 'Updated at' }), dataIndex: 'updated_at', key: 'updated_at', render: (v?: string) => v?.slice(0, 19) ?? '-' },
    {
      title: intl.formatMessage({ id: 'common.actions', defaultMessage: 'Actions' }),
      key: 'actions',
      render: (_: unknown, record: TaskEntry) => (
        <Space>
          <Link to={`/projects/${pid}/sessions/${record.session_id}`}>
            <Button size="small" type="primary">
              {intl.formatMessage({ id: 'common.open', defaultMessage: 'Open' })}
            </Button>
          </Link>
          <Link to={`/audit?session_id=${record.session_id}`}>
            <Button size="small">
              {intl.formatMessage({ id: 'common.audit', defaultMessage: 'Audit' })}
            </Button>
          </Link>
          <Button size="small" onClick={() => void handleFork(record.session_id)} data-testid={`fork-${record.session_id}`}>
            {intl.formatMessage({ id: 'ps.fork', defaultMessage: 'Fork' })}
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div data-testid="project-sessions-page">
      <PageHeader
        title={intl.formatMessage({ id: 'ps.header.title', defaultMessage: 'Project task panel' })}
        description={<Typography.Text className="mono" type="secondary">{intl.formatMessage({ id: 'common.project', defaultMessage: 'Project' })} {pid}</Typography.Text>}
        actions={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)} data-testid="create-session-btn">
            {intl.formatMessage({ id: 'ps.create', defaultMessage: 'New session' })}
          </Button>
        }
      />
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} md={8}>
          <Card data-testid="axis-cost">
            <Statistic
              title={intl.formatMessage({ id: 'ps.axis.cost', defaultMessage: 'Cost axis' })}
              value={dashboard ? `${dashboard.cost.used} / ${dashboard.cost.limit}` : '-'}
              suffix={dashboard ? `（${Math.round(dashboard.cost.ratio * 100)}%）` : ''}
            />
            <Space>
              {axisTag(dashboard?.cost.status)}
              <Typography.Text type="secondary">
                {dashboard
                  ? intl.formatMessage(
                      { id: 'dashboard.estimatedUsd', defaultMessage: 'Estimated {amount} USD' },
                      { amount: dashboard.cost.estimated_usd },
                    )
                  : ''}
              </Typography.Text>
            </Space>
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card data-testid="axis-progress">
            <Statistic
              title={intl.formatMessage({ id: 'ps.axis.progress', defaultMessage: 'Progress axis' })}
              value={dashboard ? `${Math.round(dashboard.progress.score * 100)}%` : '-'}
              suffix={
                dashboard
                  ? intl.formatMessage(
                      { id: 'dashboard.phases', defaultMessage: ' phases {done}/{total}' },
                      { done: dashboard.progress.phases.done, total: dashboard.progress.phases.total },
                    )
                  : ''
              }
            />
            <Space>{axisTag(dashboard?.progress.status)}</Space>
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card data-testid="axis-health">
            <Statistic
              title={intl.formatMessage({ id: 'ps.axis.health', defaultMessage: 'Health axis' })}
              value={dashboard ? `${Math.round(dashboard.health.score * 100)}%` : '-'}
            />
            <Space>{axisTag(dashboard?.health.status)}</Space>
          </Card>
        </Col>
      </Row>
      <Card data-testid="task-filters" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Select
            placeholder={intl.formatMessage({ id: 'common.status', defaultMessage: 'Status' })}
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
            placeholder={intl.formatMessage({ id: 'ps.col.assignee', defaultMessage: 'Assignee' })}
            allowClear
            style={{ width: 160 }}
            value={filters.assignee}
            onChange={(e) => setFilter({ assignee: e.target.value || undefined })}
            onPressEnter={applyFilters}
            data-testid="filter-assignee"
          />
          <Input
            placeholder={intl.formatMessage({ id: 'ps.filters.keyword', defaultMessage: 'Keyword' })}
            allowClear
            style={{ width: 200 }}
            value={filters.q}
            onChange={(e) => setFilter({ q: e.target.value || undefined })}
            onPressEnter={applyFilters}
            data-testid="filter-q"
          />
          <Button onClick={applyFilters}>
            {intl.formatMessage({ id: 'ps.filters.apply', defaultMessage: 'Filter' })}
          </Button>
        </Space>
      </Card>
      <Card>
        <Table<TaskEntry>
          rowKey="session_id"
          columns={columns}
          dataSource={tasks}
          loading={loading}
          pagination={false}
          locale={{
            emptyText: intl.formatMessage({
              id: 'ps.empty',
              defaultMessage: 'No tasks; click "New session" in the top right',
            }),
          }}
          data-testid="sessions-table"
        />
      </Card>
      <Modal
        title={intl.formatMessage({ id: 'ps.modal.title', defaultMessage: 'New session' })}
        open={modalOpen}
        onOk={handleCreate}
        confirmLoading={creating}
        onCancel={() => setModalOpen(false)}
        okText={intl.formatMessage({ id: 'common.create', defaultMessage: 'Create' })}
        cancelText={intl.formatMessage({ id: 'common.cancel', defaultMessage: 'Cancel' })}
        destroyOnHidden
        width={640}
        data-testid="create-session-modal"
      >
        <Form form={form} layout="vertical" initialValues={{ deterministic: false, yes: false }}>
          <Form.Item
            name="goal"
            label={intl.formatMessage({ id: 'ps.modal.goal', defaultMessage: 'Session goal' })}
            rules={[
              {
                required: true,
                message: intl.formatMessage({
                  id: 'ps.modal.goalRequired',
                  defaultMessage: 'Please enter the session goal',
                }),
              },
            ]}
          >
            <Input.TextArea
              rows={3}
              placeholder={intl.formatMessage({
                id: 'ps.modal.goalPlaceholder',
                defaultMessage: 'Describe this build/development goal…',
              })}
              data-testid="session-goal-input"
            />
          </Form.Item>
          <Form.Item name="model" label={intl.formatMessage({ id: 'common.model', defaultMessage: 'Model' })}>
            <Select
              allowClear
              placeholder={intl.formatMessage({
                id: 'ps.modal.modelPlaceholder',
                defaultMessage: 'Default is decided by the backend',
              })}
              options={MODEL_OPTIONS}
              data-testid="session-model-select"
            />
          </Form.Item>
          <Form.Item name="flow" label={intl.formatMessage({ id: 'ps.modal.flow', defaultMessage: 'Flow file' })}>
            <Input
              placeholder="examples/flows/build-product.yaml"
              data-testid="session-flow-input"
            />
          </Form.Item>
          <Form.Item name="budget" label={intl.formatMessage({ id: 'ps.modal.budget', defaultMessage: 'Token budget' })}>
            <InputNumber
              min={0}
              step={1000}
              style={{ width: '100%' }}
              placeholder={intl.formatMessage({ id: 'ps.modal.budgetPlaceholder', defaultMessage: 'Optional' })}
              data-testid="session-budget-input"
            />
          </Form.Item>
          <Space size="large">
            <Form.Item
              name="deterministic"
              label={intl.formatMessage({ id: 'ps.modal.deterministic', defaultMessage: 'Deterministic mode' })}
              valuePropName="checked"
            >
              <Switch data-testid="session-deterministic-switch" />
            </Form.Item>
            <Form.Item
              name="yes"
              label={intl.formatMessage({ id: 'ps.modal.yes', defaultMessage: 'Auto approve (--yes)' })}
              valuePropName="checked"
            >
              <Switch data-testid="session-yes-switch" />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </div>
  );
}
