import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Empty,
  Form,
  InputNumber,
  message,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import { ExperimentOutlined, ReloadOutlined } from '@ant-design/icons';
import * as api from '../api/endpoints';
import { apiErrorMessage } from '../store/appStore';
import { useProjectParam } from '../hooks/useProjectParam';
import PageHeader from '../components/PageHeader';
import ProjectSelector from '../components/ProjectSelector';
import type { EvolutionProposal } from '../api/types';

export default function Evolution() {
  const [projectId, setProjectId] = useProjectParam();
  const [proposals, setProposals] = useState<EvolutionProposal[]>([]);
  const [loading, setLoading] = useState(false);
  const [actingId, setActingId] = useState<string | null>(null);
  const [genOpen, setGenOpen] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [retroResult, setRetroResult] = useState<unknown | null>(null);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const list = await api.fetchEvolutionProposals(projectId || undefined);
      setProposals(list);
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const act = useCallback(
    async (id: string, kind: 'apply' | 'rollback') => {
      setActingId(id);
      try {
        if (kind === 'apply') await api.applyEvolutionProposal(id);
        else await api.rollbackEvolutionProposal(id);
        message.success(kind === 'apply' ? '提案已生效' : '提案已回滚');
        void load();
      } catch (err) {
        message.error(apiErrorMessage(err));
      } finally {
        setActingId(null);
      }
    },
    [load],
  );

  const generate = useCallback(async () => {
    try {
      const values = await form.validateFields();
      setGenerating(true);
      const result = await api.generateEvolutionProposals({
        project_id: projectId || undefined,
        min_evidence: values.min_evidence,
        limit: values.limit,
      });
      message.success('提案生成完成');
      setGenOpen(false);
      form.resetFields();
      void load();
      setRetroResult(result);
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) return;
      message.error(apiErrorMessage(err));
    } finally {
      setGenerating(false);
    }
  }, [form, projectId, load]);

  const retro = useCallback(async () => {
    try {
      const result = await api.runEvolutionRetro({ project_id: projectId || undefined });
      setRetroResult(result);
      message.success('复盘完成');
      void load();
    } catch (err) {
      message.error(apiErrorMessage(err));
    }
  }, [projectId, load]);

  const columns = [
    { title: 'ID', dataIndex: 'id', key: 'id', render: (v: string) => <Typography.Text className="mono">{v}</Typography.Text> },
    {
      title: '标题',
      dataIndex: 'title',
      key: 'title',
      render: (v: string | undefined, record: EvolutionProposal) => v ?? record.summary ?? '-',
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (v?: string) => (
        <Tag color={v === 'applied' ? 'green' : v === 'proposed' ? 'blue' : 'default'}>{v ?? '-'}</Tag>
      ),
    },
    {
      title: '证据',
      dataIndex: 'evidence',
      key: 'evidence',
      ellipsis: true,
      render: (v?: string) => v ?? '-',
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, record: EvolutionProposal) => (
        <Space>
          <Button size="small" type="primary" loading={actingId === record.id} onClick={() => void act(record.id, 'apply')}>
            应用
          </Button>
          <Popconfirm title="确定回滚该提案？" onConfirm={() => void act(record.id, 'rollback')}>
            <Button size="small" loading={actingId === record.id}>回滚</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div data-testid="evolution-page">
      <PageHeader
        title="进化管理"
        description="生成、评审、应用与回滚流程进化提案"
        actions={
          <Space>
            <Button icon={<ExperimentOutlined />} onClick={() => setGenOpen(true)} data-testid="generate-proposals-btn">
              生成提案
            </Button>
            <Button icon={<ReloadOutlined />} onClick={() => void retro()} data-testid="retro-btn">
              复盘
            </Button>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => void load()}
              aria-label="刷新提案列表"
              data-testid="refresh-proposals-btn"
            />
          </Space>
        }
      />
      <div style={{ marginBottom: 16 }}>
        <ProjectSelector
          value={projectId || undefined}
          onChange={setProjectId}
          placeholder="选择项目（可选）"
        />
      </div>
      {retroResult !== null && (
        <Alert
          type="success"
          showIcon
          closable
          message="复盘结果"
          description={<pre className="code-preview">{JSON.stringify(retroResult, null, 2)}</pre>}
          style={{ marginBottom: 16 }}
          data-testid="retro-result"
        />
      )}
      <Card>
        {proposals.length === 0 && !loading ? (
          <Empty description="暂无进化提案，点击「生成提案」或「复盘」开始" />
        ) : (
          <Table<EvolutionProposal>
            rowKey="id"
            columns={columns}
            dataSource={proposals}
            loading={loading}
            pagination={false}
            data-testid="proposals-table"
          />
        )}
      </Card>
      <Modal
        title="生成进化提案"
        open={genOpen}
        onOk={generate}
        confirmLoading={generating}
        onCancel={() => setGenOpen(false)}
        okText="生成"
        cancelText="取消"
        data-testid="generate-modal"
      >
        <Form form={form} layout="vertical" initialValues={{ min_evidence: 2, limit: 5 }}>
          <Form.Item name="min_evidence" label="最小证据数">
            <InputNumber min={1} max={20} style={{ width: '100%' }} data-testid="min-evidence-input" />
          </Form.Item>
          <Form.Item name="limit" label="提案数量上限">
            <InputNumber min={1} max={50} style={{ width: '100%' }} data-testid="limit-input" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}