import { useCallback, useEffect, useState } from 'react';
import {
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  message,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Typography,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { Link, useParams } from 'react-router-dom';
import * as api from '../api/endpoints';
import { apiErrorMessage } from '../store/appStore';
import PageHeader from '../components/PageHeader';
import StatusTag from '../components/StatusTag';
import type { SessionSnapshot } from '../api/types';

const MODEL_OPTIONS = ['codex', 'chat', 'responses', 'anthropic', 'deterministic'].map((m) => ({
  value: m,
  label: m,
}));

export default function ProjectSessions() {
  const { pid = '' } = useParams();
  const [sessions, setSessions] = useState<SessionSnapshot[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    if (!pid) return;
    setLoading(true);
    try {
      const list = await api.fetchProjectSessions(pid);
      setSessions(list);
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [pid]);

  useEffect(() => {
    void load();
  }, [load]);

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
      void load();
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) return;
      message.error(apiErrorMessage(err));
    } finally {
      setCreating(false);
    }
  }, [form, pid, load]);

  const columns = [
    { title: '会话 ID', dataIndex: 'session_id', key: 'session_id', render: (v: string) => <Typography.Text className="mono">{v}</Typography.Text> },
    {
      title: '目标',
      dataIndex: 'goal',
      key: 'goal',
      ellipsis: true,
      render: (v: string) => <span title={v}>{v}</span>,
    },
    { title: '模型', dataIndex: 'model', key: 'model', render: (v?: string) => v ?? '-' },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (v: string) => <StatusTag status={v} />,
    },
    {
      title: '当前阶段',
      dataIndex: 'current_phase',
      key: 'current_phase',
      render: (v?: string | null) => v ?? '-',
    },
    {
      title: 'Token 已用',
      key: 'token',
      render: (_: unknown, record: SessionSnapshot) => record.token?.used ?? 0,
    },
    { title: '消息数', dataIndex: 'transcript_count', key: 'transcript_count', render: (v?: number) => v ?? 0 },
    { title: '审批门', dataIndex: 'gate_count', key: 'gate_count', render: (v?: number) => v ?? 0 },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, record: SessionSnapshot) => (
        <Space>
          <Link to={`/projects/${pid}/sessions/${record.session_id}`}>
            <Button size="small" type="primary">打开</Button>
          </Link>
          <Link to={`/audit?session_id=${record.session_id}`}>
            <Button size="small">审计</Button>
          </Link>
        </Space>
      ),
    },
  ];

  return (
    <div data-testid="project-sessions-page">
      <PageHeader
        title="项目会话"
        description={<Typography.Text className="mono" type="secondary">项目 {pid}</Typography.Text>}
        actions={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)} data-testid="create-session-btn">
            新建会话
          </Button>
        }
      />
      <Card>
        <Table<SessionSnapshot>
          rowKey="session_id"
          columns={columns}
          dataSource={sessions}
          loading={loading}
          pagination={false}
          locale={{ emptyText: '暂无会话，点击右上角新建' }}
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